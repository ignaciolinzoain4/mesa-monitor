"""Login del dashboard (`POST /auth/login`) y usuario actual (`GET /auth/me`)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from mesa_monitor.api.deps import AuthUser, DbSession, get_settings_dep
from mesa_monitor.config import Settings
from mesa_monitor.core.enums import UserRole
from mesa_monitor.db.models import User
from mesa_monitor.security import create_access_token, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    role: UserRole


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
    user: UserOut


@router.post("/login", response_model=TokenOut)
async def login(
    body: LoginIn, db: DbSession, settings: Annotated[Settings, Depends(get_settings_dep)]
) -> TokenOut:
    user = (
        await db.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Usuario o contraseña incorrectos")
    user.last_login_at = datetime.now(UTC)
    await db.commit()
    ttl = timedelta(minutes=settings.jwt_ttl_minutes)
    token = create_access_token(
        subject=user.id, role=user.role, secret=settings.jwt_secret.get_secret_value(), ttl=ttl
    )
    return TokenOut(
        access_token=token,
        expires_in=int(ttl.total_seconds()),
        user=UserOut(username=user.username, role=user.role),
    )


@router.get("/me", response_model=UserOut)
async def me(user: AuthUser) -> UserOut:
    return UserOut(username=user.username, role=user.role)
