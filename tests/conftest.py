"""Fixtures comunes.

Los tests marcados `db` necesitan un PostgreSQL vacío en `TEST_DATABASE_URL`
(ej. `postgresql+asyncpg://mesa@localhost:5432/mesa_test`). Sin esa variable se saltean.
"""

import os

import pytest
from alembic import command
from alembic.config import Config

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def alembic_config(database_url: str) -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


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
