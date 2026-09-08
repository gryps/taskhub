import asyncio
import hashlib
import hmac
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from taskhub_v2.domain.models import Plan
from taskhub_v2.node_agent.coding import coding_available, modify_workspace, provider_health
from taskhub_v2.node_agent.runtime import (
    PROXY_VARIABLES,
    UnsafeArchiveError,
    extract_workspace,
    normalize_command,
    repair_managed_virtualenv,
    run_commands,
)
from taskhub_v2.services.diagnostics import coding_prerequisites_ok, node_diagnostics

JOB_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class ExecuteRequest(BaseModel):
    commands: list[list[str]] = Field(max_length=30)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_capabilities: set[str] = Field(default_factory=set, max_length=20)
    target_url: str = Field(default="", max_length=500)
    git_commit: str = Field(default="", pattern=r"^$|^[a-fA-F0-9]{7,64}$")
    artifact_paths: list[str] = Field(default_factory=list, max_length=30)


class CodingRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    plan: Plan
    feedback: str = Field(default="", max_length=20_000)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class NodeRuntime:
    def __init__(self, node_id: str, token: str, root: str, max_upload_bytes: int):
        self.node_id = node_id
        self.token = token
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.locks: dict[str, asyncio.Lock] = {}

    def authorize(self, authorization: str) -> None:
        supplied = authorization.removeprefix("Bearer ")
        if not self.token or not hmac.compare_digest(supplied, self.token):
            raise HTTPException(status_code=401, detail="invalid node token")

    def job_dir(self, job_id: str) -> Path:
        if not JOB_PATTERN.fullmatch(job_id):
            raise HTTPException(status_code=422, detail="invalid job ID")
        target = (self.root / job_id).resolve()
        if not target.is_relative_to(self.root):
            raise HTTPException(status_code=422, detail="invalid job path")
        return target

    @staticmethod
    def result_file(target: Path) -> Path:
        return target / ".taskhub-execution-result.json"


def create_node_app() -> FastAPI:
    runtime = NodeRuntime(
        node_id=os.getenv("TASKHUB_NODE_ID", "node-local"),
        token=os.getenv("TASKHUB_NODE_TOKEN", ""),
        root=os.getenv("TASKHUB_NODE_WORK_ROOT", "/var/lib/taskhub-node/jobs"),
        max_upload_bytes=int(os.getenv("TASKHUB_NODE_MAX_UPLOAD_BYTES", "104857600")),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        yield

    app = FastAPI(title="TaskHub Node Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health(authorization: str = Header(default="")) -> dict:
        runtime.authorize(authorization)
        usage = shutil.disk_usage(runtime.root)
        return {
            "status": "ok",
            "node_id": runtime.node_id,
            "cpu_count": os.cpu_count() or 1,
            "disk_free_bytes": usage.free,
            "capabilities": await asyncio.to_thread(detect_capabilities),
            "versions": await asyncio.to_thread(browser_versions),
            "system": await asyncio.to_thread(node_diagnostics),
            "provider_health": provider_health(),
        }

    @app.put("/api/jobs/{job_id}/workspace")
    async def upload_workspace(
        job_id: str,
        request: Request,
        sha256: str,
        authorization: str = Header(default=""),
    ) -> dict:
        runtime.authorize(authorization)
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise HTTPException(status_code=422, detail="invalid archive digest")
        target = runtime.job_dir(job_id)
        lock = runtime.locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            metadata = target / ".taskhub-workspace.json"
            if metadata.is_file() and json.loads(metadata.read_text()).get("sha256") == sha256:
                return {"job_id": job_id, "archive_sha256": sha256, "size": 0,
                        "reused": True}
            digest = hashlib.sha256()
            size = 0
            descriptor, archive_name = tempfile.mkstemp(dir=runtime.root, suffix=".tar.gz")
            try:
                with os.fdopen(descriptor, "wb") as archive:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > runtime.max_upload_bytes:
                            raise HTTPException(status_code=413, detail="workspace archive too large")
                        digest.update(chunk)
                        archive.write(chunk)
                if digest.hexdigest() != sha256:
                    raise HTTPException(status_code=422, detail="workspace digest mismatch")
                try:
                    extract_workspace(Path(archive_name), target, runtime.root)
                except UnsafeArchiveError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
            finally:
                Path(archive_name).unlink(missing_ok=True)
            (target / ".taskhub-workspace.json").write_text(
                json.dumps({"sha256": sha256}), encoding="utf-8"
            )
        return {"job_id": job_id, "archive_sha256": sha256, "size": size}

    @app.post("/api/jobs/{job_id}/execute")
    async def execute(
        job_id: str,
        payload: ExecuteRequest,
        authorization: str = Header(default=""),
    ) -> dict:
        runtime.authorize(authorization)
        capabilities = await asyncio.to_thread(detect_capabilities)
        missing = sorted(
            name
            for name in payload.required_capabilities
            if not capabilities.get(name)
        )
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "browser_capability_missing",
                    "missing": missing,
                    "node": runtime.node_id,
                },
            )
        target = runtime.job_dir(job_id)
        metadata = target / ".taskhub-workspace.json"
        if not metadata.is_file():
            raise HTTPException(status_code=409, detail="workspace is not uploaded")
        if json.loads(metadata.read_text(encoding="utf-8")).get("sha256") != payload.archive_sha256:
            raise HTTPException(status_code=409, detail="workspace version changed")
        lock = runtime.locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            request_key = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
            result_file = runtime.result_file(target)
            if repair_managed_virtualenv(target):
                result_file.unlink(missing_ok=True)
            if result_file.is_file():
                saved = json.loads(result_file.read_text(encoding="utf-8"))
                if saved.get("request_key") == request_key:
                    return saved["result"]
            started_at = datetime.now(UTC)
            tests = await _prepare_browser_acceptance_dependencies(
                target, payload.commands, payload.required_capabilities, payload.timeout_seconds
            )
            if not any(test["exit_code"] for test in tests):
                tests.extend(await run_commands(
                target, payload.commands, payload.timeout_seconds,
                execution_environment={
                    "TASKHUB_PREVIEW_URL": payload.target_url,
                    "TASKHUB_TARGET_URL": payload.target_url,
                    "TASKHUB_GIT_COMMIT": payload.git_commit,
                    "TASKHUB_CHROMIUM_CHANNEL": "chrome",
                    "TASKHUB_EDGE_CHANNEL": "msedge",
                },
                ))
            artifacts = _artifact_manifest(
                target, payload.artifact_paths, runtime.max_upload_bytes
            )
            result = {
                "node_id": runtime.node_id,
                "job_id": job_id,
                "tests": tests,
                "metadata": {
                    "target_url": payload.target_url,
                    "git_commit": payload.git_commit,
                    "versions": await asyncio.to_thread(browser_versions),
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(UTC).isoformat(),
                },
                "artifacts": artifacts,
            }
            temporary = result_file.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"request_key": request_key, "result": result}),
                encoding="utf-8",
            )
            temporary.replace(result_file)
            return result

    @app.get("/api/jobs/{job_id}/artifacts/{artifact_path:path}")
    async def download_artifact(job_id: str, artifact_path: str, authorization: str = Header(default="")):
        runtime.authorize(authorization)
        target = runtime.job_dir(job_id)
        path = (target / artifact_path).resolve()
        if not path.is_relative_to(target) or not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="artifact not found")
        return FileResponse(path)

    @app.post("/api/jobs/{job_id}/code")
    async def code(
        job_id: str,
        payload: CodingRequest,
        authorization: str = Header(default=""),
    ) -> Response:
        runtime.authorize(authorization)
        if not coding_available():
            raise HTTPException(status_code=409, detail="coding is not configured")
        target = runtime.job_dir(job_id)
        metadata = target / ".taskhub-workspace.json"
        if not metadata.is_file():
            raise HTTPException(status_code=409, detail="workspace is not uploaded")
        if json.loads(metadata.read_text()).get("sha256") != payload.archive_sha256:
            raise HTTPException(status_code=409, detail="workspace version changed")
        lock = runtime.locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            try:
                bundle = await modify_workspace(
                    target, payload.requirement, payload.plan, payload.feedback
                )
            except Exception as exc:
                reason = getattr(exc, "reason", exc.__class__.__name__)
                raise HTTPException(status_code=502, detail=str(reason)[:200]) from exc
        return Response(content=bundle, media_type="application/gzip")

    return app


def detect_capabilities() -> dict[str, bool]:
    import importlib.util

    coding_ready = coding_available() and coding_prerequisites_ok()
    return {
        "git": bool(shutil.which("git")),
        "python3": True,
        "node": bool(shutil.which("node")),
        "npm": bool(shutil.which("npm")),
        "npx": bool(shutil.which("npx")),
        "pytest": importlib.util.find_spec("pytest") is not None,
        "workspace_write_sandbox": coding_prerequisites_ok(),
        "coding": coding_ready,
        **_probe_browsers()["capabilities"],
    }


_probe_lock = threading.Lock()
_probe_cache = None
_probe_deadline = 0.0


def _probe_browsers() -> dict:
    global _probe_cache, _probe_deadline
    with _probe_lock:
        if _probe_cache is None or time.monotonic() >= _probe_deadline:
            _probe_cache = _run_browser_probe()
            _probe_deadline = time.monotonic() + 30
        return _probe_cache


def _run_browser_probe() -> dict:
    empty = {"capabilities": dict.fromkeys(
        ("windows_gui", "playwright", "chromium", "edge", "screenshot", "video", "trace"), False
    ), "versions": {}}
    if os.name != "nt":
        return empty
    try:
        result = subprocess.run(
            [sys.executable, "-m", "taskhub_v2.node_agent.browser_probe"],
            capture_output=True, text=True, timeout=90, check=True,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return empty


def _browser_path(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if os.name == "nt":
        roots = [os.getenv("PROGRAMFILES"), os.getenv("PROGRAMFILES(X86)"), os.getenv("LOCALAPPDATA")]
        relatives = ["Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"]
        for root in filter(None, roots):
            for relative in relatives:
                candidate = Path(root) / relative
                if candidate.is_file() and any(name.lower() in candidate.name.lower() for name in names):
                    return str(candidate)
    return None


def browser_versions() -> dict[str, str]:
    versions = {"platform": platform.platform(), "python": platform.python_version()}
    versions.update(_probe_browsers()["versions"])
    return versions


def _artifact_manifest(root: Path, requested: list[str], max_bytes: int) -> list[dict]:
    result: list[dict] = []
    seen: set[Path] = set()
    total = 0
    for value in requested:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(status_code=422, detail="invalid artifact path")
        candidate = (root / relative).resolve()
        paths = [candidate] if candidate.is_file() else sorted(candidate.rglob("*")) if candidate.is_dir() else []
        for path in paths:
            if path in seen or path.is_symlink() or not path.is_file() or not path.is_relative_to(root):
                continue
            seen.add(path)
            size = path.stat().st_size
            total += size
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="browser artifacts too large")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result.append({"path": path.relative_to(root).as_posix(), "sha256": digest, "size": size})
    return result


async def _prepare_browser_acceptance_dependencies(
    target: Path, commands: list[list[str]], required_capabilities: set[str], timeout: int
) -> list[dict]:
    if "playwright" not in required_capabilities:
        return []
    if not (target / "package.json").is_file() or (target / "node_modules").is_dir():
        return []
    if not any(Path(command[0]).name.lower() in {"npx", "npx.cmd"} for command in commands if command):
        return []
    command = ["npm", "ci"] if (target / "package-lock.json").is_file() else ["npm", "install"]
    command.extend(["--ignore-scripts", "--no-audit", "--no-fund"])

    def install() -> dict:
        environment = dict(os.environ)
        environment["PATH"] = os.pathsep.join(
            (str(Path(sys.executable).parent), environment.get("PATH", ""))
        )
        for key in PROXY_VARIABLES:
            environment.pop(key, None)
        try:
            result = subprocess.run(
                normalize_command(command),
                cwd=target,
                env=environment,
                capture_output=True,
                text=True,
                timeout=min(max(timeout, 60), 600),
            )
            output = (result.stdout + result.stderr)[-32_000:]
            return {"command": command, "exit_code": result.returncode, "output_tail": output}
        except FileNotFoundError:
            return {"command": command, "exit_code": 127, "output_tail": "command not found"}
        except TimeoutError:
            return {"command": command, "exit_code": 124, "output_tail": "command timed out"}

    return [await asyncio.to_thread(install)]
