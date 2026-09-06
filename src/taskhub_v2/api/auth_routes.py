from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from taskhub_v2.security.auth import CSRF_COOKIE, SESSION_COOKIE

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    token: str = Field(min_length=1)


@router.get("/status")
async def status(request: Request) -> dict:
    auth = request.app.state.auth
    session = auth.read_session(request.cookies.get(SESSION_COOKIE))
    return {"authenticated": bool(session), "configured": auth.configured()}


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    auth = request.app.state.auth
    if not auth.valid_admin_token(payload.token):
        raise HTTPException(status_code=401, detail="invalid admin token")
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
