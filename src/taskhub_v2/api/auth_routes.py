from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from taskhub_v2.security.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthConfigurationError,
    PasswordSetupError,
    UserManagementError,
)
from taskhub_v2.security.passwords import PASSWORD_MIN_LENGTH
from taskhub_v2.security.rbac import ROLE_PERMISSIONS

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=64)
    token: str = Field(min_length=1, max_length=256)


class PasswordSetupRequest(BaseModel):
    bootstrap_token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=256)


class UserWriteRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    role: Literal["administrator", "project_owner", "developer", "auditor"]
    password: str = Field(default="", max_length=256)
    enabled: bool = True


@router.get("/status")
async def status(request: Request) -> dict:
    auth = request.app.state.auth
    session = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return {
        "authenticated": bool(session),
        "configured": auth.configured(),
        "password_login": auth.password_login_enabled(),
        "setup_required": auth.setup_required(),
        "actor": session.get("actor") if session else None,
        "role": session.get("role") if session else None,
        "permissions": sorted(ROLE_PERMISSIONS.get(session.get("role"), set())) if session else [],
        "idle_expires": session.get("idle_expires") if session else None,
        "absolute_expires": session.get("expires") if session else None,
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    auth = request.app.state.auth
    if auth.setup_required():
        raise HTTPException(status_code=409, detail="administrator password setup required")
    identity = request.client.host if request.client else "unknown"
    lock_identity = f"{identity}:{payload.username.lower()}"
    if auth.login_locked_seconds(lock_identity):
        request.app.state.operation_log.record("login", "rate_limited", actor=payload.username)
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后重试")
    try:
        user = auth.authenticate(payload.username, payload.token, identity)
    except PermissionError as error:
        request.app.state.operation_log.record("login", "rate_limited", actor=payload.username)
        raise HTTPException(status_code=429, detail="登录失败次数过多，请稍后重试") from error
    if not user:
        request.app.state.operation_log.record("login", "failed", actor=payload.username)
        raise HTTPException(status_code=401, detail="invalid administrator password")
    request.app.state.operation_log.record(
        "login", "passed", actor=user["username"], role=user["role"]
    )
    return _create_session(request, response, user["username"], user["role"])


@router.post("/setup")
async def setup(payload: PasswordSetupRequest, request: Request, response: Response) -> dict:
    auth = request.app.state.auth
    try:
        auth.initialize_password(payload.bootstrap_token, payload.password)
    except PermissionError as error:
        raise HTTPException(status_code=401, detail="invalid bootstrap token") from error
    except PasswordSetupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    request.app.state.operation_log.record("password_setup", "passed", actor="admin")
    return _create_session(request, response)


def _create_session(
    request: Request, response: Response, username: str = "admin", role: str = "administrator"
) -> dict:
    auth = request.app.state.auth
    session, csrf = auth.create_session(username, role)
    secure = request.app.state.settings.cookie_secure
    max_age = request.app.state.settings.session_absolute_seconds
    response.set_cookie(
        SESSION_COOKIE,
        session,
        httponly=True,
        samesite="strict",
        secure=secure,
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        httponly=False,
        samesite="strict",
        secure=secure,
        max_age=max_age,
        path="/",
    )
    return {"authenticated": True}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    session = getattr(request.state, "session", None)
    request.app.state.operation_log.record(
        "logout", "passed", actor=(session or {}).get("actor"), role=(session or {}).get("role")
    )
    request.app.state.auth.revoke_session(session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"authenticated": False}


@router.get("/users")
async def users(request: Request) -> dict:
    return {"users": request.app.state.auth.list_users()}


@router.put("/users/{username}")
async def save_user(username: str, payload: UserWriteRequest, request: Request) -> dict:
    if username.lower() != payload.username.lower():
        raise HTTPException(status_code=422, detail="username path and payload must match")
    try:
        return request.app.state.auth.upsert_user(
            payload.username, payload.role, payload.password, payload.enabled
        )
    except (ValueError, UserManagementError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete("/users/{username}")
async def delete_user(username: str, request: Request) -> dict:
    try:
        request.app.state.auth.delete_user(username)
    except UserManagementError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"deleted": username.lower()}


@router.post("/signing-key/rotate")
async def rotate_signing_key(request: Request, response: Response) -> dict:
    try:
        result = request.app.state.auth.rotate_signing_key()
    except AuthConfigurationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return result
