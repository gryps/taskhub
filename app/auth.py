from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import HTTPException, Request


SESSION_COOKIE = "langgraph_session"
CSRF_COOKIE = "langgraph_csrf"
SESSION_TTL_SECONDS = 12 * 60 * 60


def _secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def constant_time_equal(left: str, right: str) -> bool:
    return bool(left and right) and hmac.compare_digest(left.encode(), right.encode())


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session(actor: str = "admin") -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = {
        "actor": actor,
        "csrf": csrf,
        "expires_at": int(time.time()) + int(os.getenv("AUTH_SESSION_TTL", SESSION_TTL_SECONDS)),
    }
    encoded = _encode(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(_secret("AUTH_SESSION_SECRET").encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}", csrf


def read_session(value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    encoded, signature = value.rsplit(".", 1)
    expected = hmac.new(_secret("AUTH_SESSION_SECRET").encode(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(payload.get("expires_at", 0)) <= int(time.time()):
        return None
    return payload


def bearer_token(request: Request) -> str:
    value = request.headers.get("authorization", "")
    scheme, _, token = value.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def authenticate_admin(request: Request) -> tuple[str | None, bool]:
    if constant_time_equal(bearer_token(request), os.getenv("LANGGRAPH_ADMIN_TOKEN", "")):
        return "admin:token", False
    session = read_session(request.cookies.get(SESSION_COOKIE))
    if session:
        return str(session.get("actor") or "admin"), True
    return None, False


def authenticate_worker(request: Request) -> str | None:
    token = request.headers.get("x-taskhub-worker-token", "")
    if constant_time_equal(token, os.getenv("TASKHUB_WORKER_TOKEN", "")):
        return "worker"
    return None


def require_csrf(request: Request) -> None:
    session = read_session(request.cookies.get(SESSION_COOKIE))
    header = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    expected = str((session or {}).get("csrf") or "")
    if not expected or not constant_time_equal(header, expected) or not constant_time_equal(cookie, expected):
        raise HTTPException(status_code=403, detail="CSRF validation failed")
