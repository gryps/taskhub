import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from taskhub_v2.domain.models import Plan
from taskhub_v2.node_agent.coding import coding_available, modify_workspace, provider_health
from taskhub_v2.node_agent.runtime import (
    UnsafeArchiveError,
    extract_workspace,
    run_commands,
)

JOB_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class ExecuteRequest(BaseModel):
    commands: list[list[str]] = Field(max_length=30)
    timeout_seconds: int = Field(default=600, ge=1, le=3600)
    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


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
            "capabilities": detect_capabilities(),
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
            digest = hashlib.sha256()
            size = 0
            with tempfile.NamedTemporaryFile(dir=runtime.root, suffix=".tar.gz") as archive:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > runtime.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="workspace archive too large")
                    digest.update(chunk)
                    archive.write(chunk)
                archive.flush()
                if digest.hexdigest() != sha256:
                    raise HTTPException(status_code=422, detail="workspace digest mismatch")
                try:
                    extract_workspace(Path(archive.name), target, runtime.root)
                except UnsafeArchiveError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
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
        target = runtime.job_dir(job_id)
        metadata = target / ".taskhub-workspace.json"
        if not metadata.is_file():
            raise HTTPException(status_code=409, detail="workspace is not uploaded")
        if json.loads(metadata.read_text(encoding="utf-8")).get("sha256") != payload.archive_sha256:
            raise HTTPException(status_code=409, detail="workspace version changed")
        lock = runtime.locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            tests = await run_commands(target, payload.commands, payload.timeout_seconds)
        return {
            "node_id": runtime.node_id,
            "job_id": job_id,
            "tests": tests,
        }

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

    return {
        "git": bool(shutil.which("git")),
        "python3": True,
        "node": bool(shutil.which("node")),
        "npm": bool(shutil.which("npm")),
        "pytest": importlib.util.find_spec("pytest") is not None,
        "coding": coding_available(),
    }
