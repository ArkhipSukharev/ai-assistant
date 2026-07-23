from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import Settings, get_settings
from app.database import User
from app.models.schemas import LoginRequest, UserResponse
from app.services.auth_service import AuthService

SESSION_COOKIE = "assistant_session"

router = APIRouter(prefix="/api/auth", tags=["auth"])
limiter = Limiter(key_func=get_remote_address)


def get_auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Auth service is not ready")
    return service


async def get_current_user(
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    user = await auth.user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
    return user


@router.post("/login", response_model=UserResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    payload: LoginRequest,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> User:
    result = await auth.authenticate(payload.email, payload.password)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
        )
    user, token = result
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.auth_session_days * 86400,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return user


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.logout(token)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
