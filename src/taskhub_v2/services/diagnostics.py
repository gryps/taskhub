import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from taskhub_v2.config import Settings


def controller_diagnostics(settings: Settings) -> dict:
    return {
        "role": "controller",
        "host": platform.node(),
        "platform": _platform(),
        "checks": host_checks(settings, role="controller"),
    }


def node_diagnostics() -> dict:
    return {
        "role": "node",
        "host": platform.node(),
        "platform": _platform(),
        "checks": host_checks(None, role="node"),
    }


def host_checks(settings: Settings | None, *, role: str) -> list[dict]:
    checks = [
        _check("system", "操作系统", _os_release(), expected="Ubuntu 24.04"),
        _check("system", "CPU 架构", platform.machine(), expected="x86_64/amd64"),
        _check("runtime", "Python 版本", platform.python_version(), expected="3.12+"),
        _command_check("runtime", "Git", "git", "--version"),
        _command_check("runtime", "Codex CLI", _codex_bin(settings), "--version"),
        _command_check("runtime", "Node.js", "node", "--version", optional=True),
        _command_check("runtime", "npm", "npm", "--version", optional=True),
        _command_check("runtime", "ripgrep", "rg", "--version", optional=True),
        _command_check("runtime", "jq", "jq", "--version", optional=True),
        _command_check("runtime", "zip", "zip", "-v", optional=True),
        _command_check("runtime", "unzip", "unzip", "-v", optional=True),
        _command_check("runtime", "PostgreSQL client", "psql", "--version", optional=True),
        _test_database_check(),
        _user_namespace_check(),
        _bubblewrap_check(),
    ]
    checks.extend(_configuration_checks(settings, role))
    return checks


def coding_prerequisites_ok() -> bool:
    return _user_namespace_available()


def summarize_checks(checks: list[dict]) -> str:
    if any(item["status"] == "fail" for item in checks):
        return "fail"
    if any(item["status"] == "warn" for item in checks):
        return "warn"
    return "pass"


def _platform() -> dict[str, str]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def _configuration_checks(settings: Settings | None, role: str) -> list[dict]:
    if role == "node":
        return [
            _path_check("configuration", "节点配置", Path.home() / ".config/taskhub-node/node.env"),
            _path_check(
                "configuration",
                "节点模型配置",
                Path.home() / ".config/taskhub-node/providers.env",
                optional=True,
            ),
        ]
    assert settings is not None
    return [
        _path_check("configuration", "项目注册表", Path(settings.projects_file)),
        _path_check("configuration", "执行节点注册表", Path(settings.nodes_file)),
        _path_check("configuration", "Provider 配置", Path(settings.provider_secrets_file)),
        _path_check("configuration", "工作区目录", Path(settings.workspace_root), directory=True),
        _path_check("configuration", "产物目录", Path(settings.artifact_root), directory=True),
    ]


def _path_check(
    category: str, name: str, path: Path, *, directory: bool = False, optional: bool = False
) -> dict:
    exists = path.is_dir() if directory else path.is_file()
    status = "pass" if exists else "warn" if optional else "fail"
    detail = "存在" if exists else "未找到"
    result = _check(category, name, detail, status=status, actual=str(path))
    if exists and path.name.endswith(".env"):
        mode = path.stat().st_mode & 0o777
        result["detail"] = f"存在，权限 {mode:o}"
        if mode & 0o077:
            result["status"] = "warn"
            result["recommendation"] = f"建议执行 chmod 600 {path}"
    return result


def _command_check(
    category: str, name: str, command: str, *args: str, optional: bool = False
) -> dict:
    executable = command if Path(command).is_file() else shutil.which(command)
    if not executable:
        return _check(
            category,
            name,
            "未安装或不在 PATH",
            status="warn" if optional else "fail",
            recommendation=f"安装 {command} 或修正 PATH",
        )
    rc, output = _run([executable, *args])
    status = "pass" if rc == 0 else "warn" if optional else "fail"
    detail = output.splitlines()[0] if output.strip() else f"exit {rc}"
    return _check(category, name, detail, status=status, actual=executable)


def _user_namespace_check() -> dict:
    rc, output = _run(["unshare", "-Ur", "true"])
    if rc == 0:
        return _check("preflight", "User namespace", "可用")
    return _check(
        "preflight",
        "User namespace",
        output or f"exit {rc}",
        status="fail",
        recommendation="修复宿主机 user namespace/AppArmor 权限，否则 Codex workspace-write 无法改代码",
    )


def _bubblewrap_check() -> dict:
    executable = shutil.which("bwrap")
    if executable:
        return _check("preflight", "bubblewrap", "已安装", actual=executable)
    return _check(
        "preflight",
        "bubblewrap",
        "系统未安装，Codex 会尝试使用 bundled bubblewrap",
        status="warn",
        recommendation="建议安装 bubblewrap，并确认 user namespace 可用",
    )


def _test_database_check() -> dict:
    from taskhub_v2.node_agent.test_database import TestDatabaseManager

    result = TestDatabaseManager.from_environment().probe()
    return _check(
        "test_database", "隔离测试数据库", result["detail"],
        status="pass" if result["available"] else "warn",
        recommendation="配置 TASKHUB_TEST_DATABASE_ADMIN_DSN 并授予建库权限",
    )


def _user_namespace_available() -> bool:
    rc, _ = _run(["unshare", "-Ur", "true"])
    return rc == 0


def _codex_bin(settings: Settings | None) -> str:
    if settings is not None:
        return settings.codex_cli_bin
    return os.getenv("TASKHUB_CODEX_CLI_BIN", str(Path.home() / ".local/bin/codex"))


def _os_release() -> str:
    path = Path("/etc/os-release")
    if not path.is_file():
        return platform.platform()
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip('"')
    return f"{values.get('PRETTY_NAME') or values.get('ID', '')}"


def _run(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    output = (result.stdout + result.stderr).strip()
    return result.returncode, output[:1000]


def _check(
    category: str,
    name: str,
    detail: str,
    *,
    status: str = "pass",
    expected: str = "",
    actual: str = "",
    recommendation: str = "",
) -> dict:
    return {
        "category": category,
        "name": name,
        "status": status,
        "detail": detail,
        "expected": expected,
        "actual": actual,
        "recommendation": recommendation,
    }
