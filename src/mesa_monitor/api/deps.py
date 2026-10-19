"""Dependencias de FastAPI: sesión de base, usuario autenticado, roles."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.config import Settings
from mesa_monitor.core.enums import UserRole
from mesa_monitor.db.models import User
from mesa_monitor.security import InvalidTokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    role: UserRole


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def get_current_user(
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if creds is None:
        raise _unauthorized("Falta iniciar sesión")
    try:
        payload = decode_access_token(creds.credentials, settings.jwt_secret.get_secret_value())
    except InvalidTokenError as exc:
        raise _unauthorized("La sesión expiró o no es válida") from exc
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if user is None or not user.active:
        raise _unauthorized("Usuario inexistente o desactivado")
    return CurrentUser(id=user.id, username=user.username, role=user.role)


async def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if user.role is not UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requiere permisos de administrador")
    return user


AuthUser = Annotated[CurrentUser, Depends(get_current_user)]
AdminUser = Annotated[CurrentUser, Depends(require_admin)]
