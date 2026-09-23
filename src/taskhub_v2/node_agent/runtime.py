import asyncio
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
from contextlib import suppress
from pathlib import Path

FORBIDDEN_NAMES = {".env", ".env.local", "auth.json", "credentials.json"}
PROXY_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


class UnsafeArchiveError(ValueError):
    pass


WINDOWS_COMMANDS = {"python3": "python.exe", "npm": "npm.cmd", "npx": "npx.cmd"}


def dependency_cache_environment(
    workdir: Path,
    environment: dict[str, str],
    execution_environment: dict[str, str] | None = None,
) -> None:
    """Give isolated jobs persistent download caches owned by their node.

    Workspaces and virtual environments remain job-local.  Only package-manager
    downloads are shared, so clean-install checks keep their isolation while
    avoiding repeated network transfers across jobs and nested test projects.
    """
    overrides = execution_environment or {}
    enabled = environment.get("TASKHUB_NODE_DEPENDENCY_CACHE", "true").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return
    configured = environment.get("TASKHUB_NODE_CACHE_ROOT", "").strip()
    cache_root = Path(configured).expanduser() if configured else workdir.parent.parent / "cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    pip_cache = cache_root / "pip"
    npm_cache = cache_root / "npm"
    uv_cache = cache_root / "uv"
    pip_cache.mkdir(exist_ok=True)
    npm_cache.mkdir(exist_ok=True)
    uv_cache.mkdir(exist_ok=True)
    environment.setdefault("PIP_CACHE_DIR", str(pip_cache))
    environment.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    environment.setdefault("npm_config_cache", str(npm_cache))
    environment.setdefault("npm_config_prefer_offline", "true")
    environment.setdefault("npm_config_audit", "false")
    environment.setdefault("npm_config_fund", "false")
    environment.setdefault("UV_CACHE_DIR", str(uv_cache))
    # The worker image historically disabled pip's cache globally.  Remove that
    # inherited default, while still honoring an explicit per-job opt-out.
    if "PIP_NO_CACHE_DIR" not in overrides:
        environment.pop("PIP_NO_CACHE_DIR", None)


def subprocess_group_options() -> dict[str, object]:
    """Start commands in an isolated group so timeouts can stop descendants."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def kill_process_tree(process: asyncio.subprocess.Process) -> None:
    """Force-stop a timed-out command and every process it spawned."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        await asyncio.to_thread(
            subprocess.run,
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with suppress(ProcessLookupError):
        await process.wait()


def normalize_command(command: list[str], *, windows: bool | None = None) -> list[str]:
    """Map portable commands to executables provided by the node runtime."""
    if not command:
        return command
    executable_name = Path(command[0]).name.lower()
    if executable_name in {"python", "python3", "python.exe"}:
        return [sys.executable, *command[1:]]
    is_windows = os.name == "nt" if windows is None else windows
    if not is_windows:
        return list(command)
    executable = WINDOWS_COMMANDS.get(executable_name, command[0])
    return [executable, *command[1:]]


def extract_workspace(archive: Path, target: Path, root: Path) -> None:
    staging = Path(tempfile.mkdtemp(prefix="workspace-", dir=root))
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                member_path = Path(member.name)
                if (
                    member_path.is_absolute()
                    or ".." in member_path.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member_path.name in FORBIDDEN_NAMES
                    or member_path.name.startswith(".env.")
                ):
                    raise UnsafeArchiveError("unsafe workspace archive")
            bundle.extractall(staging, filter="data")
        if target.exists():
            shutil.rmtree(target)
        staging.replace(target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def repair_managed_virtualenv(workdir: Path) -> bool:
    venv = workdir / ".venv"
    if not venv.exists():
        return False
    python_candidates = (venv / "bin" / "python", venv / "Scripts" / "python.exe")
    if any(candidate.is_file() for candidate in python_candidates):
        return False
    if venv.is_symlink() or not venv.is_dir():
        venv.unlink()
    else:
        shutil.rmtree(venv)
    return True


async def run_commands(
    workdir: Path, commands: list[list[str]], timeout: int,
    *, execution_environment: dict[str, str] | None = None
) -> list[dict]:
    results = []
    environment = dict(os.environ)
    environment.update(execution_environment or {})
    dependency_cache_environment(workdir, environment, execution_environment)
    # Keep skip names and reasons reviewable when projects invoke pytest with -q.
    environment.setdefault("PYTEST_ADDOPTS", "-ra")
    environment["PATH"] = os.pathsep.join(
        (str(Path(sys.executable).parent), environment.get("PATH", ""))
    )
    for key in PROXY_VARIABLES:
        environment.pop(key, None)
    for command in commands:
        if not command or not command[0].strip():
            raise ValueError("empty command")
        command = normalize_command(command)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **subprocess_group_options(),
            )
        except FileNotFoundError:
            results.append(
                {"command": command, "exit_code": 127, "output_tail": "command not found"}
            )
            break
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
            exit_code = process.returncode
            output = stdout.decode(errors="replace")[-32_000:]
        except TimeoutError:
            await kill_process_tree(process)
            exit_code = 124
            output = "command timed out"
        results.append(
            {"command": command, "exit_code": exit_code, "output_tail": output}
        )
        if exit_code:
            break
    return results
