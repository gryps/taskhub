from __future__ import annotations

import os
from pathlib import Path

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
from app.douyin_graph import router as douyin_graph_router
from app.graph import compiled_graph
from app.planner import router as planner_router
from app.taskhub import init_taskhub, record_audit_event, router as taskhub_router

load_dotenv()

app = FastAPI(title="Gryps LangGraph Control", version="0.2.0")
app.include_router(taskhub_router)
app.include_router(douyin_graph_router)
app.include_router(planner_router)
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


def is_worker_endpoint(path: str) -> bool:
    return path.startswith("/taskhub/") and any(path.endswith(suffix) for suffix in WORKER_PATH_SUFFIXES)


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


@app.on_event("startup")
def startup() -> None:
    init_taskhub()


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
def workers() -> list[dict[str, str]]:
    result = []
    for url in worker_urls():
        try:
            response = httpx.get(f"{url}/health", timeout=3)
            response.raise_for_status()
            payload = response.json()
            result.append({"url": url, "status": "ok", "detail": str(payload)})
        except Exception as exc:
            result.append({"url": url, "status": "error", "detail": exc.__class__.__name__})
    return result


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
