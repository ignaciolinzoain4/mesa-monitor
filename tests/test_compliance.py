"""Restricciones de cumplimiento (SPEC Anexo A.3): el sistema no identifica empleados.

Recorre el esquema (modelos y base real, incluidas vistas y vistas materializadas) y falla si
alguna columna permite identificar a quién hizo una visita o atendió una mesa.
"""

import re

import pytest
from sqlalchemy import Connection, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.core.metrics import MetricsGroupBy
from mesa_monitor.db import models  # noqa: F401
from mesa_monitor.db.base import Base

FORBIDDEN_COLUMN = re.compile(
    r"^(staff_id|waiter_id|mozo.*|employee.*|empleado.*|person_id|persona.*|"
    r"track_id|track_.*|.*_track_id|server_id|assigned_staff.*)$",
    re.I,
)
ALLOWED_GROUP_BY = {"hour", "day", "weekday", "shift", "table", "sector"}


def _violations(columns_by_table: dict[str, list[str]]) -> list[str]:
    return [
        f"{table}.{col}"
        for table, cols in columns_by_table.items()
        for col in cols
        if FORBIDDEN_COLUMN.match(col)
    ]


@pytest.mark.parametrize(
    "column", ["staff_id", "waiter_id", "employee_name", "track_id", "last_track_id", "mozo"]
)
def test_forbidden_pattern_catches_identity_columns(column: str) -> None:
    assert FORBIDDEN_COLUMN.match(column)


@pytest.mark.parametrize("column", ["table_id", "session_id", "started_at", "acknowledged_by"])
def test_forbidden_pattern_allows_regular_columns(column: str) -> None:
    assert not FORBIDDEN_COLUMN.match(column)


def test_models_have_no_employee_identity_columns() -> None:
    columns = {t.name: [c.name for c in t.columns] for t in Base.metadata.sorted_tables}
    assert _violations(columns) == []


def test_staff_visits_store_only_times() -> None:
    cols = {c.name for c in Base.metadata.tables["staff_visits"].columns}
    assert cols == {"id", "session_id", "table_id", "started_at", "ended_at", "confidence"}


def test_metrics_group_by_has_no_person_dimension() -> None:
    assert {g.value for g in MetricsGroupBy} == ALLOWED_GROUP_BY


@pytest.mark.db
async def test_database_has_no_employee_identity_columns(migrated_db: str) -> None:
    def collect(sync_conn: Connection) -> dict[str, list[str]]:
        insp = inspect(sync_conn)
        names = insp.get_table_names() + insp.get_view_names() + insp.get_materialized_view_names()
        return {n: [c["name"] for c in insp.get_columns(n)] for n in names}

    engine = create_async_engine(migrated_db)
    async with engine.connect() as conn:
        columns = await conn.run_sync(collect)
    await engine.dispose()
    assert columns, "no se leyó ninguna tabla"
    assert _violations(columns) == []
