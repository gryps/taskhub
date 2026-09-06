from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

PERSISTENT_NAMES = {".env", ".venv"}


def write_state(path: Path, deployment_id: str, status: str, detail: str, **extra) -> None:
    payload = {
        "deployment_id": deployment_id,
        "status": status,
        "detail": detail[-4000:],
        "updated_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="deployment.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def command(*arguments: str, cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stdout + result.stderr).strip()[-4000:])
    return result.stdout


def prepare_candidate(repository: Path, remote: str, branch: str, commit: str, root: Path) -> Path:
    command("git", "-C", str(repository), "fetch", remote, branch)
    authority = command("git", "-C", str(repository), "rev-parse", f"{remote}/{branch}").strip()
    if authority != commit:
        raise RuntimeError("待部署提交不是权威分支当前版本")
    if command("git", "-C", str(repository), "status", "--porcelain").strip():
        raise RuntimeError("控制中心项目工作副本存在未提交修改")
    archive = root / "release.tar"
    with archive.open("wb") as handle:
        result = subprocess.run(
            ["git", "-C", str(repository), "archive", commit],
            stdout=handle,
            stderr=subprocess.PIPE,
            timeout=120,
            check=False,
        )
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace")[-4000:])
    candidate = root / "candidate"
    candidate.mkdir()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.issym() or member.islnk():
                raise RuntimeError("发布归档包含不安全路径")
        bundle.extractall(candidate, filter="data")
    return candidate


def run_tests(candidate: Path, target: Path, tests: list[list[str]]) -> None:
    if not tests:
        raise RuntimeError("没有配置强制测试命令")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(candidate / "src")
    environment["PATH"] = os.pathsep.join(
        (str(target / ".venv" / "bin"), environment.get("PATH", ""))
    )
    for test in tests:
        if not test:
            raise RuntimeError("测试命令为空")
        command(*test, cwd=candidate, env=environment)


def copy_application(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for child in target.iterdir():
        if child.name in PERSISTENT_NAMES:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination)
        else:
            shutil.copy2(child, destination)


def service_restart(service: str) -> None:
    command("systemctl", "--user", "restart", service)


def wait_for_health(url: str, timeout: int = 40) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_error = "health check timed out"
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=3) as response:
                payload = json.loads(response.read())
            if response.status == 200 and payload.get("status") == "ok":
                return
            last_error = f"health response: {payload}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(last_error)


def deploy(args: argparse.Namespace) -> None:
    state_file = Path(args.state_file).expanduser().resolve()
    repository = Path(args.repository).expanduser().resolve()
    target = Path(args.target).expanduser().resolve()
    tests = json.loads(args.tests_json)
    lock_file = state_file.with_suffix(".lock")
    lock_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with lock_file.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("另一个部署任务正在执行") from exc
        changed = False
        with tempfile.TemporaryDirectory(prefix="taskhub-deploy-") as temporary:
            root = Path(temporary)
            try:
                write_state(
                    state_file,
                    args.deployment_id,
                    "validating",
                    "正在校验权威提交",
                    project_id=args.project_id,
                    commit=args.commit,
                )
                candidate = prepare_candidate(
                    repository, args.remote, args.branch, args.commit, root
                )
                write_state(
                    state_file,
                    args.deployment_id,
                    "testing",
                    "正在执行部署前测试",
                    project_id=args.project_id,
                    commit=args.commit,
                )
                run_tests(candidate, target, tests)
                backup = root / "backup"
                shutil.copytree(
                    target,
                    backup,
                    ignore=shutil.ignore_patterns(".venv", ".env", "__pycache__", ".pytest_cache"),
                )
                write_state(
                    state_file,
                    args.deployment_id,
                    "deploying",
                    "正在替换应用文件",
                    project_id=args.project_id,
                    commit=args.commit,
                )
                copy_application(candidate, target)
                (target / ".taskhub-release").write_text(args.commit + "\n", encoding="utf-8")
                changed = True
                write_state(
                    state_file,
                    args.deployment_id,
                    "restarting",
                    "正在重启并执行健康检查",
                    project_id=args.project_id,
                    commit=args.commit,
                )
                service_restart(args.service)
                wait_for_health(args.health_url)
                write_state(
                    state_file,
                    args.deployment_id,
                    "completed",
                    "部署及健康检查通过",
                    project_id=args.project_id,
                    commit=args.commit,
                )
            except Exception as exc:
                if changed:
                    try:
                        copy_application(backup, target)
                        service_restart(args.service)
                        wait_for_health(args.health_url)
                        write_state(
                            state_file,
                            args.deployment_id,
                            "rolled_back",
                            f"部署失败，已回滚：{exc}",
                            project_id=args.project_id,
                            commit=args.commit,
                        )
                    except Exception as rollback_error:
                        write_state(
                            state_file,
                            args.deployment_id,
                            "rollback_failed",
                            f"部署失败：{exc}；回滚失败：{rollback_error}",
                            project_id=args.project_id,
                            commit=args.commit,
                        )
                else:
                    write_state(
                        state_file,
                        args.deployment_id,
                        "failed",
                        str(exc),
                        project_id=args.project_id,
                        commit=args.commit,
                    )
                raise


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    for name in (
        "deployment-id",
        "project-id",
        "repository",
        "remote",
        "branch",
        "commit",
        "tests-json",
        "target",
        "service",
        "state-file",
        "health-url",
    ):
        result.add_argument(f"--{name}", required=True)
    return result


if __name__ == "__main__":
    deploy(parser().parse_args())
