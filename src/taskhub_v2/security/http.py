from fastapi.responses import JSONResponse

from taskhub_v2.security.agent_access import bearer_token
from taskhub_v2.security.auth import CSRF_COOKIE, SESSION_COOKIE


def required_permission(path: str, method: str) -> str | None:
    if path == "/api/auth/logout":
        return None
    if path.startswith("/api/auth/users"):
        return "users:manage"
    if path.startswith("/api/auth/agent-") or path == "/api/auth/signing-key/rotate":
        return "security:manage"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if path.startswith("/api/change-requests"):
        return "delivery:execute"
    if path.startswith(
        ("/api/containers", "/api/settings", "/api/onboarding")
    ):
        return "infrastructure:manage"
    if path.startswith("/api/deployment"):
        return "release:manage"
    if path.startswith("/api/projects"):
        return "projects:manage"
    if path.startswith(("/api/requirements", "/api/product-specs")):
        return "projects:manage"
    if path.startswith("/api/runs"):
        return "delivery:execute"
    return "infrastructure:manage"


def secure_response(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "style-src-attr 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    return response


def _public_path(path: str) -> bool:
    return (
        path == "/"
        or path.startswith("/static/")
        or path
        in {
            "/api/health",
            "/api/auth/status",
            "/api/auth/login",
            "/api/auth/setup",
            "/api/auth/agent-pairings/start",
        }
        or (path.startswith("/api/auth/agent-pairings/") and path.endswith("/exchange"))
    )


def _request_session(request, auth, agent_access):
    authorization = request.headers.get("authorization")
    token = bearer_token(authorization)
    session = agent_access.authenticate(token) if token else None
    if authorization and not session:
        return None, "invalid bearer credential"
    if not session:
        session = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return session, "authentication required"


def authentication_middleware(auth, agent_access, operation_log):
    async def require_authentication(request, call_next):
        path = request.url.path
        if _public_path(path):
            response = await call_next(request)
            return secure_response(response)
        session, authentication_error = _request_session(request, auth, agent_access)
        if not session:
            return JSONResponse({"detail": authentication_error}, status_code=401)
        request.state.session = session
        permission = required_permission(path, request.method)
        if permission and not auth.allowed(session, permission):
            operation_log.record(
                "access_denied",
                "forbidden",
                actor=session.get("actor"),
                role=session.get("role"),
                method=request.method,
                path=path,
            )
            return JSONResponse({"detail": "permission denied"}, status_code=403)
        browser_mutation = (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and session.get("auth_type") != "bearer"
        )
        if browser_mutation and not auth.valid_csrf(
            session, request.headers.get("x-csrf-token", ""), request.cookies.get(CSRF_COOKIE, "")
        ):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
        response = await call_next(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            operation_log.record(
                "management_api",
                "passed" if response.status_code < 400 else "failed",
                actor=session.get("actor"),
                role=session.get("role"),
                method=request.method,
                path=path,
                status_code=response.status_code,
            )
        return secure_response(response)

    return require_authentication
