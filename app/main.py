from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
import httpx
from pydantic import BaseModel, Field
import psycopg
import redis
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.staticfiles import StaticFiles

from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    authenticate_admin,
    authenticate_worker,
    constant_time_equal,
    create_session,
    require_csrf,
)
from app.coder_executor import router as coder_executor_router
from app.advanced import init_advanced, router as advanced_router, run_advanced_cycle
from app.douyin_graph import router as douyin_graph_router
from app.graph import compiled_graph
from app.integration import init_integration, router as integration_router, run_integration_cycle
from app.monitoring import router as monitoring_router
from app.notifications import notification_config, router as notification_router, run_notification_cycle_safely
from app.operations import operations_policy, router as operations_router, run_operations_cycle_safely
from app.planner import router as planner_router
from app.readiness import assess_workers, simulate_dual_pipeline
from app.taskhub import init_taskhub, record_audit_event, router as taskhub_router
from app.workflow_executor import run_automation_cycle

load_dotenv()

app = FastAPI(title="Gryps LangGraph Control", version="0.2.0")
app.include_router(taskhub_router)
app.include_router(douyin_graph_router)
app.include_router(planner_router)
app.include_router(monitoring_router)
app.include_router(notification_router)
app.include_router(operations_router)
app.include_router(advanced_router)
app.include_router(integration_router)
app.include_router(coder_executor_router)
STATIC_DIR = Path(__file__).resolve().parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class InvokeRequest(BaseModel):
    input: str = Field(..., min_length=1)


class WorkerRunRequest(BaseModel):
    tool: str = Field(..., min_length=1)
    input: str | None = None
    worker: str | None = None


class LoginRequest(BaseModel):
    token: str = Field(..., min_length=1)


PUBLIC_PATHS = {"/", "/health", "/auth/status", "/auth/login"}
WORKER_PATH_SUFFIXES = {"/claim", "/heartbeat", "/complete", "/fail"}
workflow_automation_task: asyncio.Task[None] | None = None
notification_task: asyncio.Task[None] | None = None
operations_task: asyncio.Task[None] | None = None
advanced_task: asyncio.Task[None] | None = None
integration_task: asyncio.Task[None] | None = None


def is_worker_endpoint(path: str) -> bool:
    return (
        path.startswith("/taskhub/") and any(path.endswith(suffix) for suffix in WORKER_PATH_SUFFIXES)
    ) or path.startswith("/taskhub/integration/artifacts/") or path == "/taskhub/coder/execute"


@app.middleware("http")
async def authentication_and_audit(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await call_next(request)

    actor, cookie_auth = authenticate_admin(request)
    if not actor and is_worker_endpoint(path):
        worker_actor = authenticate_worker(request)
        if worker_actor:
            actor = f"worker:{request.headers.get('x-worker-id', 'unknown')}"
            cookie_auth = False
    if not actor:
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    if cookie_auth and request.method not in {"GET", "HEAD", "OPTIONS"}:
        try:
            require_csrf(request)
        except Exception:
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)

    request.state.actor = actor
    response = await call_next(request)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        record_audit_event(
            actor=actor,
            action=f"{request.method} {path}",
            status_code=response.status_code,
            client_ip=request.client.host if request.client else None,
        )
    return response


async def workflow_automation_loop() -> None:
    interval = max(2, int(os.getenv("WORKFLOW_ROLE_POLL_SECONDS", "10")))
    while True:
        worked = await asyncio.to_thread(run_automation_cycle)
        await asyncio.sleep(0 if worked else interval)


async def notification_loop() -> None:
    while True:
        await asyncio.to_thread(run_notification_cycle_safely)
        await asyncio.sleep(notification_config()["poll_seconds"])


async def operations_loop() -> None:
    while True:
        await asyncio.to_thread(run_operations_cycle_safely)
        await asyncio.sleep(operations_policy()["poll_seconds"])


async def advanced_loop() -> None:
    interval = max(30, int(os.getenv("TASKHUB_ADVANCED_POLL_SECONDS", "60")))
    while True:
        worked = await asyncio.to_thread(run_advanced_cycle)
        await asyncio.sleep(1 if worked else interval)


async def integration_loop() -> None:
    interval = max(30, int(os.getenv("TASKHUB_INTEGRATION_POLL_SECONDS", "60")))
    while True:
        worked = await asyncio.to_thread(run_integration_cycle)
        await asyncio.sleep(1 if worked else interval)


@app.on_event("startup")
async def startup() -> None:
    global advanced_task, integration_task, notification_task, operations_task, workflow_automation_task
    init_taskhub()
    init_advanced()
    init_integration()
    if os.getenv("WORKFLOW_ROLE_AUTOMATION_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        workflow_automation_task = asyncio.create_task(workflow_automation_loop())
    notification_task = asyncio.create_task(notification_loop())
    operations_task = asyncio.create_task(operations_loop())
    advanced_task = asyncio.create_task(advanced_loop())
    integration_task = asyncio.create_task(integration_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    global advanced_task, integration_task, notification_task, operations_task, workflow_automation_task
    for task in (workflow_automation_task, notification_task, operations_task, advanced_task, integration_task):
        if not task:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    workflow_automation_task = None
    notification_task = None
    operations_task = None
    advanced_task = None
    integration_task = None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/auth/status")
def auth_status(request: Request) -> dict[str, str | bool]:
    actor, _ = authenticate_admin(request)
    return {"authenticated": bool(actor), "actor": actor or ""}


@app.get("/system/status")
def system_status() -> dict[str, str | bool | int]:
    return {
        "admin_auth_configured": bool(os.getenv("LANGGRAPH_ADMIN_TOKEN", "").strip()),
        "signed_sessions_configured": bool(os.getenv("AUTH_SESSION_SECRET", "").strip()),
        "csrf_enforced": True,
        "worker_auth_configured": bool(os.getenv("TASKHUB_WORKER_TOKEN", "").strip()),
        "cookie_secure": os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"},
        "session_ttl_seconds": int(os.getenv("AUTH_SESSION_TTL", str(12 * 60 * 60))),
        "code_apply_enabled": os.getenv("CODE_APPLY_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        "workflow_role_automation_enabled": os.getenv("WORKFLOW_ROLE_AUTOMATION_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        "notification_enabled": notification_config()["enabled"],
        "remediation_enabled": operations_policy()["enabled"],
        "remediation_mode": operations_policy()["mode"],
    }


@app.post("/auth/login")
def auth_login(request: Request, payload: LoginRequest) -> Response:
    expected = os.getenv("LANGGRAPH_ADMIN_TOKEN", "")
    if not constant_time_equal(payload.token, expected):
        record_audit_event(
            actor="anonymous",
            action="AUTH_LOGIN_FAILED",
            status_code=401,
            client_ip=request.client.host if request.client else None,
        )
        return JSONResponse({"detail": "invalid token"}, status_code=401)
    session, csrf = create_session()
    response = JSONResponse({"authenticated": True, "actor": "admin"})
    secure = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in {"1", "true", "yes", "on"}
    max_age = int(os.getenv("AUTH_SESSION_TTL", str(12 * 60 * 60)))
    response.set_cookie(SESSION_COOKIE, session, httponly=True, secure=secure, samesite="strict", max_age=max_age)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure, samesite="strict", max_age=max_age)
    record_audit_event(
        actor="admin",
        action="AUTH_LOGIN_SUCCEEDED",
        status_code=200,
        client_ip=request.client.host if request.client else None,
    )
    return response


@app.post("/auth/logout")
def auth_logout() -> Response:
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return response


def worker_urls() -> list[str]:
    raw_urls = os.getenv("WORKER_URLS", "")
    return [url.strip().rstrip("/") for url in raw_urls.split(",") if url.strip()]


@app.get("/health")
def health() -> dict[str, str]:
    checks = {
        "status": "ok",
        "service": "langgraph-control",
        "env": os.getenv("LANGGRAPH_ENV", "local"),
        "postgres": "not_configured",
        "redis": "not_configured",
        "redis_role": "health_check_only",
        "workers": "0",
    }

    database_url = os.getenv("DATABASE_URL")
    if database_url:
        try:
            with psycopg.connect(database_url, connect_timeout=2) as conn:
                with conn.cursor() as cur:
                    cur.execute("select 1")
                    cur.fetchone()
            checks["postgres"] = "ok"
        except Exception as exc:
            checks["status"] = "degraded"
            checks["postgres"] = exc.__class__.__name__

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["status"] = "degraded"
            checks["redis"] = exc.__class__.__name__

    workers = worker_urls()
    checks["workers"] = str(len(workers))

    return {
        key: str(value)
        for key, value in checks.items()
    }


@app.post("/invoke")
def invoke(request: InvokeRequest) -> dict[str, str]:
    return compiled_graph.invoke({"input": request.input})


@app.get("/workers")
def workers() -> list[dict[str, Any]]:
    result = []
    for url in worker_urls():
        try:
            response = httpx.get(f"{url}/health", timeout=3)
            response.raise_for_status()
            payload = response.json()
            result.append(
                {
                    "url": url,
                    "status": "ok",
                    "worker_id": str(payload.get("worker") or ""),
                    "hostname": str(payload.get("hostname") or ""),
                    "task_types": str(payload.get("task_types") or ""),
                    "detail": str(payload),
                }
            )
        except Exception as exc:
            result.append(
                {
                    "url": url,
                    "status": "error",
                    "worker_id": "",
                    "hostname": "",
                    "task_types": "",
                    "detail": exc.__class__.__name__,
                }
            )
    return result


@app.get("/taskhub/operations/readiness")
def production_readiness() -> dict[str, Any]:
    worker_items = workers()
    return {
        "workers": assess_workers(worker_items),
        "normal_load_test": simulate_dual_pipeline(200),
        "worker_loss_test": simulate_dual_pipeline(200, fail_after=25),
        "policy": operations_policy(),
    }


@app.post("/worker/run")
def worker_run(request: WorkerRunRequest) -> dict[str, str]:
    urls = worker_urls()
    if request.worker:
        urls = [request.worker.rstrip("/")]
    if not urls:
        return {"status": "error", "detail": "no worker configured"}

    response = httpx.post(
        f"{urls[0]}/run",
        json={"tool": request.tool, "input": request.input},
        headers={"x-taskhub-worker-token": os.getenv("TASKHUB_WORKER_TOKEN", "")},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "status": "ok",
        "worker": urls[0],
        "tool": str(payload.get("tool", request.tool)),
        "result": str(payload.get("result", "")),
    }
