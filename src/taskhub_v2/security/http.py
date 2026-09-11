def required_permission(path: str, method: str) -> str | None:
    if path == "/api/auth/logout":
        return None
    if path.startswith("/api/auth/users") or path == "/api/auth/signing-key/rotate":
        return "users:manage" if "users" in path else "security:manage"
    if method in {"GET", "HEAD", "OPTIONS"}:
        return "read"
    if path.startswith("/api/change-requests"):
        return "delivery:execute"
    if path.startswith(
        ("/api/hosts", "/api/remote-nodes", "/api/containers", "/api/settings", "/api/onboarding")
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
