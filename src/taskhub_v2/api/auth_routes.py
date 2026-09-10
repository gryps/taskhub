from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from taskhub_v2.security.auth import (
    CSRF_COOKIE,
    PASSWORD_MIN_LENGTH,
    SESSION_COOKIE,
    PasswordSetupError,
)

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class PasswordSetupRequest(BaseModel):
    bootstrap_token: str = Field(min_length=1, max_length=512)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=256)


@router.get("/status")
async def status(request: Request) -> dict:
    auth = request.app.state.auth
    session = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return {
        "authenticated": bool(session),
        "configured": auth.configured(),
        "password_login": auth.password_login_enabled(),
        "setup_required": auth.setup_required(),
    }


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    auth = request.app.state.auth
    if auth.setup_required():
        raise HTTPException(status_code=409, detail="administrator password setup required")
    if not auth.valid_login_secret(payload.token):
        raise HTTPException(status_code=401, detail="invalid administrator password")
    return _create_session(request, response)


@router.post("/setup")
async def setup(
    payload: PasswordSetupRequest, request: Request, response: Response
) -> dict:
    auth = request.app.state.auth
    try:
        auth.initialize_password(payload.bootstrap_token, payload.password)
    except PermissionError as error:
        raise HTTPException(status_code=401, detail="invalid bootstrap token") from error
    except PasswordSetupError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return _create_session(request, response)


def _create_session(request: Request, response: Response) -> dict:
    auth = request.app.state.auth
    session, csrf = auth.create_session()
    secure = request.app.state.settings.cookie_secure
    response.set_cookie(SESSION_COOKIE, session, httponly=True, samesite="strict", secure=secure)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="strict", secure=secure)
    return {"authenticated": True}


@router.post("/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    return {"authenticated": False}
