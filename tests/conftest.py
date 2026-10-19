"""Fixtures comunes.

Los tests marcados `db` necesitan un PostgreSQL vacío en `TEST_DATABASE_URL`
(ej. `postgresql+asyncpg://mesa@localhost:5432/mesa_test`). Sin esa variable se saltean.
"""

import os
import struct
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.config import Settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_JWT_SECRET = "test-secret-" + "x" * 40
ADMIN_USER = "admin"
ADMIN_PASS = "clave-de-test"


def alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "jwt_secret": TEST_JWT_SECRET,
        "admin_username": ADMIN_USER,
        "admin_password": ADMIN_PASS,
        "go2rtc_url": "http://go2rtc.test",
        "config_dir": Path("/nonexistent"),
        "dashboard_dist": Path("/nonexistent"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def fake_jpeg(width: int = 704, height: int = 480) -> bytes:
    """JPEG mínimo (SOI + APP0 + SOF0 + EOI): alcanza para leer la resolución."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3) + b"\x01\x11\x00" * 3
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


async def app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture(scope="session")
def database_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL no está definida")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_db(database_url: str) -> str:
    """Base con el esquema en `head`, recreado desde cero para la sesión de tests."""
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return database_url


@pytest.fixture
async def clean_db(migrated_db: str) -> str:
    """Base migrada y vacía (TRUNCATE de todas las tablas de datos)."""
    engine = create_async_engine(migrated_db)
    async with engine.begin() as conn:
        tables = (
            await conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                    " AND tablename <> 'alembic_version'"
                )
            )
        ).scalars()
        await conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await engine.dispose()
    return migrated_db
