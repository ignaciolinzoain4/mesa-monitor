"""Dependencias de FastAPI: sesión de base, quién llama (usuario, API key o dispositivo) y permisos.

Credenciales aceptadas:
- `Authorization: Bearer <JWT>`  — usuarios del dashboard (login con usuario y contraseña).
- `X-API-Key: mm_...`            — integraciones (sistema de gestión, cliente Python).
- `Authorization: Bearer mmd_...` — dispositivos (PCs de tiqueo con la pantalla /alertas).
El WebSocket acepta cualquiera de ellas como `?token=...` (los navegadores no mandan headers).
"""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.config import Settings
from mesa_monitor.core.enums import ActorType, UserRole
from mesa_monitor.db.audit import Actor
from mesa_monitor.db.models import ApiKey, DeviceToken, User
from mesa_monitor.security import InvalidTokenError, decode_access_token

_bearer = HTTPBearer(auto_error=False)
_api_key = APIKeyHeader(name="X-API-Key", auto_error=False)

API_KEY_PREFIX = "mm_"
DEVICE_TOKEN_PREFIX = "mmd_"  # noqa: S105  (prefijo, no un secreto)

Kind = Literal["user", "api_key", "device"]


def token_hash(token: str) -> str:
    """Hash de tokens de alta entropía (API keys y tokens de dispositivo)."""
    return hashlib.sha256(token.encode()).hexdigest()


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass(frozen=True)
class Principal:
    kind: Kind
    id: str
    username: str  # nombre visible: usuario, nombre de la API key o del dispositivo
    role: UserRole | None  # None para dispositivos

    @property
    def is_admin(self) -> bool:
        return self.role is UserRole.ADMIN

    @property
    def actor(self) -> Actor:
        kind = {"user": ActorType.USER, "api_key": ActorType.API_KEY, "device": ActorType.DEVICE}
        return Actor(kind[self.kind], self.username)


# Compatibilidad con el nombre anterior.
CurrentUser = Principal


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def resolve_token(db: AsyncSession, settings: Settings, token: str) -> Principal:
    """Valida cualquier credencial (JWT, API key o token de dispositivo)."""
    now = datetime.now(UTC)
    if token.startswith(API_KEY_PREFIX):
        key = (
            await db.execute(select(ApiKey).where(ApiKey.key_hash == token_hash(token)))
        ).scalar_one_or_none()
        if key is None or key.revoked_at is not None:
            raise _unauthorized("API key inválida o revocada")
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
        return Principal("api_key", key.id, key.name, key.role)
    if token.startswith(DEVICE_TOKEN_PREFIX):
        dev = (
            await db.execute(select(DeviceToken).where(DeviceToken.token_hash == token_hash(token)))
        ).scalar_one_or_none()
        if dev is None or dev.revoked_at is not None:
            raise _unauthorized("Dispositivo no autorizado o revocado")
        await db.execute(
            update(DeviceToken).where(DeviceToken.id == dev.id).values(last_seen_at=now)
        )
        await db.commit()
        return Principal("device", dev.id, dev.name, None)
    try:
        payload = decode_access_token(token, settings.jwt_secret.get_secret_value())
    except InvalidTokenError as exc:
        raise _unauthorized("La sesión expiró o no es válida") from exc
    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if user is None or not user.active:
        raise _unauthorized("Usuario inexistente o desactivado")
    return Principal("user", user.id, user.username, user.role)


async def get_principal(
    request: Request,
    db: DbSession,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    api_key: Annotated[str | None, Depends(_api_key)],
) -> Principal:
    # `?access_token=` solo para <img>/MJPEG (el navegador no puede mandar headers ahí).
    query_token = request.query_params.get("access_token") if request.method == "GET" else None
    token = api_key or (creds.credentials if creds else None) or query_token
    if not token:
        raise _unauthorized("Falta iniciar sesión o la API key")
    return await resolve_token(db, settings, token)


async def get_current_user(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    """Usuarios y API keys (no dispositivos): lectura del dashboard e integraciones."""
    if principal.kind == "device":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No disponible para dispositivos")
    return principal


async def require_admin(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requiere permisos de administrador")
    return principal


async def require_user_session(
    principal: Annotated[Principal, Depends(get_principal)],
) -> Principal:
    if principal.kind != "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Solo para usuarios del dashboard")
    return principal


AnyPrincipal = Annotated[Principal, Depends(get_principal)]  # incluye dispositivos
AuthUser = Annotated[Principal, Depends(get_current_user)]
AdminUser = Annotated[Principal, Depends(require_admin)]
UserSession = Annotated[Principal, Depends(require_user_session)]
