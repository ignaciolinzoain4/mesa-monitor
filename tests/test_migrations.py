"""Migraciones: suben/bajan limpias, coinciden con los modelos y los constraints clave existen."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from mesa_monitor.db import models  # noqa: F401
from mesa_monitor.db.base import Base
from tests.conftest import alembic_config

pytestmark = pytest.mark.db


@pytest.fixture
async def conn(migrated_db: str) -> AsyncIterator[AsyncConnection]:
    """Conexión dentro de una transacción que se descarta al final del test."""
    engine = create_async_engine(migrated_db)
    async with engine.connect() as connection:
        trans = await connection.begin()
        yield connection
        await trans.rollback()
    await engine.dispose()


def test_downgrade_and_upgrade_roundtrip(migrated_db: str) -> None:
    cfg = alembic_config(migrated_db)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


async def test_models_match_migrations(conn: AsyncConnection) -> None:
    def diff(sync_conn: Connection) -> list[object]:
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return list(compare_metadata(ctx, Base.metadata))

    assert await conn.run_sync(diff) == []


async def _insert_table(conn: AsyncConnection, table_id: str = "tbl_1") -> None:
    await conn.execute(
        text("INSERT INTO tables (id, label, sector) VALUES (:id, :label, 'salon')"),
        {"id": table_id, "label": table_id},
    )


async def test_only_one_active_alert_per_table_and_type(conn: AsyncConnection) -> None:
    await _insert_table(conn)
    now = datetime.now(UTC)
    insert = text(
        "INSERT INTO alerts (id, alert_type, severity, status, table_id, threshold_seconds,"
        " opened_at, updated_at) VALUES (:id, 'SIN_ATENCION', 'warning', :status, 'tbl_1',"
        " 180, :now, :now)"
    )
    await conn.execute(insert, {"id": "alr_1", "status": "resolved", "now": now})
    await conn.execute(insert, {"id": "alr_2", "status": "open", "now": now})
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await conn.execute(insert, {"id": "alr_3", "status": "acknowledged", "now": now})


async def test_only_one_open_session_per_table(conn: AsyncConnection) -> None:
    await _insert_table(conn)
    insert = text(
        "INSERT INTO table_sessions (id, table_id, business_date, seated_at, closed_at)"
        " VALUES (:id, 'tbl_1', :d, :ts, :closed)"
    )
    params = {"d": date(2026, 10, 18), "ts": datetime.now(UTC)}
    await conn.execute(insert, {"id": "ses_1", "closed": datetime.now(UTC), **params})
    await conn.execute(insert, {"id": "ses_2", "closed": None, **params})
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await conn.execute(insert, {"id": "ses_3", "closed": None, **params})


async def test_enum_check_constraints_reject_unknown_values(conn: AsyncConnection) -> None:
    await _insert_table(conn)
    with pytest.raises(IntegrityError):
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO state_transitions (table_id, ts, to_state, reason)"
                    " VALUES ('tbl_1', now(), 'INVENTADO', 'test')"
                )
            )
