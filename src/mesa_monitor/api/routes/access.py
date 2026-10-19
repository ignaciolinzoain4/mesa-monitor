"""Usuarios, API keys y tokens de dispositivo (solo administradores).

- API keys (`mm_...`): para el sistema de gestión. Se muestran UNA vez; se guarda el hash.
- Tokens de dispositivo (`mmd_...`): para las PCs de tiqueo. Se usan en un link
  (`/alertas?token=...`) o con un PIN corto de 6 dígitos. El PIN se canjea por el token.
"""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mesa_monitor.api.deps import (
    API_KEY_PREFIX,
    DEVICE_TOKEN_PREFIX,
    AdminUser,
    DbSession,
    UserSession,
    token_hash,
)
from mesa_monitor.core.enums import UserRole
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import record_audit
from mesa_monitor.db.models import ApiKey, DeviceToken, User
from mesa_monitor.security import hash_password, verify_password

router = APIRouter(tags=["access"])

Username = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[a-zA-Z0-9._-]{3,64}$")
]
Password = Annotated[str, StringConstraints(min_length=8, max_length=200)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


# --------------------------------------------------------------------------- usuarios


class UserOut(BaseModel):
    id: str
    username: str
    role: UserRole
    active: bool
    last_login_at: datetime | None


class UserIn(BaseModel):
    username: Username
    password: Password
    role: UserRole = UserRole.VIEWER


class UserPatch(BaseModel):
    role: UserRole | None = None
    active: bool | None = None
    password: Password | None = None


def _user(u: User) -> UserOut:
    return UserOut(
        id=u.id, username=u.username, role=u.role, active=u.active, last_login_at=u.last_login_at
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DbSession, _: AdminUser) -> list[UserOut]:
    return [_user(u) for u in (await db.execute(select(User).order_by(User.username))).scalars()]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserIn, db: DbSession, who: AdminUser) -> UserOut:
    u = User(
        id=new_id("usr"),
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    db.add(u)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese usuario ya existe") from exc
    await record_audit(
        db, who.actor, "create", "user", u.id, after={"username": u.username, "role": u.role}
    )
    await db.commit()
    return _user(u)


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserPatch, db: DbSession, who: AdminUser) -> UserOut:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Usuario inexistente")
    if u.id == who.id and (body.active is False or body.role is UserRole.VIEWER):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="No podés quitarte tus propios permisos"
        )
    if body.role is not None:
        u.role = body.role
    if body.active is not None:
        u.active = body.active
    if body.password is not None:
        u.password_hash = hash_password(body.password)
    await record_audit(
        db,
        who.actor,
        "update",
        "user",
        u.id,
        after=body.model_dump(exclude_none=True, exclude={"password"})
        | ({"password": "cambiada"} if body.password else {}),
    )
    await db.commit()
    return _user(u)


class PasswordChange(BaseModel):
    current_password: str
    new_password: Password


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(body: PasswordChange, db: DbSession, who: UserSession) -> Response:
    u = await db.get(User, who.id)
    if u is None or not verify_password(u.password_hash, body.current_password):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="La contraseña actual no es correcta")
    u.password_hash = hash_password(body.new_password)
    await record_audit(db, who.actor, "change_password", "user", u.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- API keys


class ApiKeyIn(BaseModel):
    name: Name
    role: UserRole = UserRole.VIEWER


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    role: UserRole
    created_at: datetime
    created_by: str | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(
        description="Se muestra una sola vez: guardala en el .env del sistema de gestión"
    )


def _key(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id,
        name=k.name,
        prefix=k.prefix,
        role=k.role,
        created_at=k.created_at,
        created_by=k.created_by,
        last_used_at=k.last_used_at,
        revoked_at=k.revoked_at,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(db: DbSession, _: AdminUser) -> list[ApiKeyOut]:
    return [
        _key(k)
        for k in (await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars()
    ]


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyIn, db: DbSession, who: AdminUser) -> ApiKeyCreated:
    raw = API_KEY_PREFIX + secrets.token_urlsafe(32)
    k = ApiKey(
        id=new_id("key"),
        name=body.name,
        prefix=raw[:10],
        key_hash=token_hash(raw),
        role=body.role,
        created_by=who.username,
        created_at=datetime.now(UTC),
    )
    db.add(k)
    await record_audit(
        db, who.actor, "create", "api_key", k.id, after={"name": k.name, "role": k.role}
    )
    await db.commit()
    return ApiKeyCreated(**_key(k).model_dump(), key=raw)


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: str, db: DbSession, who: AdminUser) -> Response:
    k = await db.get(ApiKey, key_id)
    if k is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API key inexistente")
    k.revoked_at = datetime.now(UTC)
    await record_audit(db, who.actor, "revoke", "api_key", k.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- dispositivos


class DeviceIn(BaseModel):
    name: Name
    with_pin: bool = True


class DeviceOut(BaseModel):
    id: str
    name: str
    has_pin: bool
    created_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None


class DeviceCreated(DeviceOut):
    token: str
    pin: str | None
    link_path: str


def _dev(d: DeviceToken) -> DeviceOut:
    return DeviceOut(
        id=d.id,
        name=d.name,
        has_pin=d.pin_hash is not None,
        created_at=d.created_at,
        last_seen_at=d.last_seen_at,
        revoked_at=d.revoked_at,
    )


@router.get("/device-tokens", response_model=list[DeviceOut])
async def list_devices(db: DbSession, _: AdminUser) -> list[DeviceOut]:
    rows = (await db.execute(select(DeviceToken).order_by(DeviceToken.created_at.desc()))).scalars()
    return [_dev(d) for d in rows]


@router.post("/device-tokens", response_model=DeviceCreated, status_code=status.HTTP_201_CREATED)
async def create_device(body: DeviceIn, db: DbSession, who: AdminUser) -> DeviceCreated:
    token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    pin = None
    if body.with_pin:
        existing = {
            d.pin_hash
            for d in (
                await db.execute(select(DeviceToken).where(DeviceToken.revoked_at.is_(None)))
            ).scalars()
        }
        for _ in range(20):
            candidate = f"{secrets.randbelow(10**6):06d}"
            if token_hash("pin:" + candidate) not in existing:
                pin = candidate
                break
    d = DeviceToken(
        id=new_id("dev"),
        name=body.name,
        token_hash=token_hash(token),
        pin_hash=token_hash("pin:" + pin) if pin else None,
        created_at=datetime.now(UTC),
    )
    db.add(d)
    await record_audit(db, who.actor, "create", "device", d.id, after={"name": d.name})
    await db.commit()
    return DeviceCreated(
        **_dev(d).model_dump(), token=token, pin=pin, link_path=f"/alertas?token={token}"
    )


@router.delete("/device-tokens/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device(device_id: str, db: DbSession, who: AdminUser) -> Response:
    d = await db.get(DeviceToken, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dispositivo inexistente")
    d.revoked_at = datetime.now(UTC)
    await record_audit(db, who.actor, "revoke", "device", d.id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class PinIn(BaseModel):
    pin: Annotated[str, StringConstraints(pattern=r"^\d{6}$")]


class PinOut(BaseModel):
    token: str
    name: str


@router.post("/auth/device-pin", response_model=PinOut)
async def device_pin_login(body: PinIn, db: DbSession) -> PinOut:
    """Canjea el PIN de una PC de tiqueo por un token nuevo de ese dispositivo (el anterior deja
    de valer). Con límite de intentos por IP (ver `api.ratelimit`)."""
    d = (
        await db.execute(
            select(DeviceToken).where(
                DeviceToken.pin_hash == token_hash("pin:" + body.pin),
                DeviceToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="PIN incorrecto")
    token = DEVICE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    d.token_hash = token_hash(token)
    d.last_seen_at = datetime.now(UTC)
    await db.commit()
    return PinOut(token=token, name=d.name)
