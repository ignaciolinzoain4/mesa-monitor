"""Contraseñas (argon2) y tokens JWT del dashboard."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from mesa_monitor.core.enums import UserRole

_hasher = PasswordHasher()
JWT_ALGORITHM = "HS256"
# Valores de ejemplo de .env.example: si quedaron sin cambiar, la API no arranca.
PLACEHOLDER_SECRETS = {"", "reemplazar-por-un-valor-largo-y-aleatorio"}
MIN_SECRET_LENGTH = 32


class InvalidTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def check_jwt_secret(secret: str) -> None:
    if secret in PLACEHOLDER_SECRETS or len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            "JWT_SECRET no está configurado (o es muy corto). Generá uno con:\n"
            '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
            "y ponelo en el archivo .env"
        )


def create_access_token(
    *, subject: str, role: UserRole, secret: str, ttl: timedelta, now: datetime | None = None
) -> str:
    issued = now or datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role.value,
        "iat": issued,
        "exp": issued + ttl,
        "typ": "user",
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, secret: str) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token, secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]}
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "user":
        raise InvalidTokenError("tipo de token inválido")
    return payload
