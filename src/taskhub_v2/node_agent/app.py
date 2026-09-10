import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from taskhub_v2.domain.models import Plan
from taskhub_v2.node_agent.browser_capabilities import (
    browser_prerequisites,
    browser_versions,
    probe_browsers,
)
from taskhub_v2.node_agent.browser_dependencies import prepare_browser_dependencies
from taskhub_v2.node_agent.coding import coding_available, modify_workspace, provider_health
from taskhub_v2.node_agent.runtime import (
    UnsafeArchiveError,
    extract_workspace,
    repair_managed_virtualenv,
    run_commands,
)
from taskhub_v2.node_agent.system_load import RollingLoadSampler
from taskhub_v2.node_agent.test_database import TestDatabaseManager
from taskhub_v2.services.diagnostics import coding_prerequisites_ok, node_diagnostics

JOB_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
NODE_ROLE_WORKLOADS = {
    "execution": ["build", "coding"],
    "test": ["acceptance", "test"],
    "preproduction": ["acceptance", "build", "test"],
}


class ExecuteRequest(BaseModel):
    commands: list[list[str]] = Field(max_length=30)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    required_capabilities: set[str] = Field(default_factory=set, max_length=20)
    target_url: str = Field(default="", max_length=500)
    git_commit: str = Field(default="", pattern=r"^$|^[a-fA-F0-9]{7,64}$")
    artifact_paths: list[str] = Field(default_factory=list, max_length=30)
    execution_environment: dict[str, str] = Field(default_factory=dict, max_length=20)

    @field_validator("execution_environment")
    @classmethod
    def safe_execution_environment(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "TASKHUB_TEST_TARGET_URL",
            "TASKHUB_TEST_EDGE_HOST",
            "TASKHUB_TEST_ORIGIN_HOST",
            "TASKHUB_TEST_EXPECTED_ENVIRONMENT",
            "TASKHUB_TEST_ENVIRONMENT_PROFILE",
        }
        if not set(value).issubset(allowed) or any(len(item) > 500 for item in value.values()):
            raise ValueError("invalid test environment variables")
        return value


class CodingRequest(BaseModel):
    requirement: str = Field(min_length=3, max_length=20_000)
    plan: Plan
    feedback: str = Field(default="", max_length=20_000)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class NodeRuntime:
    def __init__(self, node_id: str, role: str, token: str, root: str, max_upload_bytes: int):
        self.node_id = node_id
        self.role = role if role in NODE_ROLE_WORKLOADS else "test"
        self.token = token
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.locks: dict[str, asyncio.Lock] = {}
        self.load_sampler = RollingLoadSampler()

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
        role=os.getenv("TASKHUB_NODE_ROLE", "test"),
        token=os.getenv("TASKHUB_NODE_TOKEN", ""),
        root=os.getenv("TASKHUB_NODE_WORK_ROOT", "/var/lib/taskhub-node/jobs"),
        max_upload_bytes=int(os.getenv("TASKHUB_NODE_MAX_UPLOAD_BYTES", "104857600")),
    )
    test_databases = TestDatabaseManager.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        await runtime.load_sampler.start(runtime.root)
        try:
            yield
        finally:
            await runtime.load_sampler.stop()

    app = FastAPI(title="TaskHub Node Agent", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health(authorization: str = Header(default="")) -> dict:
        runtime.authorize(authorization)
        usage = shutil.disk_usage(runtime.root)
        database = await asyncio.to_thread(test_databases.probe)
        prerequisites = browser_prerequisites()
        capabilities = await asyncio.to_thread(runtime_capabilities, database, prerequisites)
        return {
            "status": "ok",
            "node_id": runtime.node_id,
            "role": runtime.role,
            "workloads": NODE_ROLE_WORKLOADS[runtime.role],
            "cpu_count": os.cpu_count() or 1,
            "disk_free_bytes": usage.free,
            "load": runtime.load_sampler.peak(),
            "capabilities": capabilities,
            "test_database": database,
            "browser_prerequisites": prerequisites,
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
                return {"job_id": job_id, "archive_sha256": sha256, "size": 0, "reused": True}
            digest = hashlib.sha256()
            size = 0
            descriptor, archive_name = tempfile.mkstemp(dir=runtime.root, suffix=".tar.gz")
            try:
                with os.fdopen(descriptor, "wb") as archive:
                    async for chunk in request.stream():
                        size += len(chunk)
                        if size > runtime.max_upload_bytes:
                            raise HTTPException(
                                status_code=413, detail="workspace archive too large"
                            )
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
        database = await asyncio.to_thread(test_databases.probe)
        capabilities = await asyncio.to_thread(
            runtime_capabilities, database, browser_prerequisites()
        )
        missing = sorted(
            name for name in payload.required_capabilities if not capabilities.get(name)
        )
        if missing:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "node_capability_missing",
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
            tests = await prepare_browser_dependencies(
                target, payload.commands, payload.required_capabilities, payload.timeout_seconds
            )
            if not any(test["exit_code"] for test in tests):
                environment = {
                    "TASKHUB_PREVIEW_URL": payload.target_url,
                    "TASKHUB_TARGET_URL": payload.target_url,
                    "TASKHUB_GIT_COMMIT": payload.git_commit,
                    "TASKHUB_CHROMIUM_CHANNEL": "chrome",
                    "TASKHUB_EDGE_CHANNEL": "msedge",
                    "TASKHUB_BROWSER_PROFILE_DIR": os.getenv("TASKHUB_BROWSER_PROFILE_DIR", ""),
                    "TASKHUB_BROWSER_AUTH_TARGET": os.getenv("TASKHUB_BROWSER_AUTH_TARGET", ""),
                    **payload.execution_environment,
                }
                if "test_database" in payload.required_capabilities:
                    with test_databases.database(job_id) as database_environment:
                        tests.extend(
                            await run_commands(
                                target,
                                payload.commands,
                                payload.timeout_seconds,
                                execution_environment={**environment, **database_environment},
                            )
                        )
                else:
                    tests.extend(
                        await run_commands(
                            target,
                            payload.commands,
                            payload.timeout_seconds,
                            execution_environment=environment,
                        )
                    )
            artifacts = _artifact_manifest(target, payload.artifact_paths, runtime.max_upload_bytes)
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
    async def download_artifact(
        job_id: str, artifact_path: str, authorization: str = Header(default="")
    ):
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
        if "coding" not in NODE_ROLE_WORKLOADS[runtime.role]:
            raise HTTPException(status_code=403, detail="node role does not allow coding")
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
        **probe_browsers()["capabilities"],
    }


def runtime_capabilities(database: dict, browser: dict) -> dict[str, bool]:
    capabilities = detect_capabilities()
    capabilities["test_database"] = bool(database.get("available"))
    capabilities["browser_profile"] = bool(browser.get("profile_configured"))
    capabilities["browser_authenticated"] = bool(browser.get("authenticated"))
    return capabilities


def _artifact_manifest(root: Path, requested: list[str], max_bytes: int) -> list[dict]:
    result: list[dict] = []
    seen: set[Path] = set()
    total = 0
    for value in requested:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise HTTPException(status_code=422, detail="invalid artifact path")
        candidate = (root / relative).resolve()
        paths = (
            [candidate]
            if candidate.is_file()
            else sorted(candidate.rglob("*"))
            if candidate.is_dir()
            else []
        )
        for path in paths:
            if (
                path in seen
                or path.is_symlink()
                or not path.is_file()
                or not path.is_relative_to(root)
            ):
                continue
            seen.add(path)
            size = path.stat().st_size
            total += size
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="browser artifacts too large")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result.append(
                {"path": path.relative_to(root).as_posix(), "sha256": digest, "size": size}
            )
    return result
