"""Persistencia del state engine y reconstrucción tras un reinicio (SPEC §4.2, §8.4)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.core.enums import TableState
from mesa_monitor.core.state_engine import EngineParams, Observation, StateEngine, TableMachine
from mesa_monitor.db.models import (
    EventLog,
    StaffVisit,
    StateTransition,
    TableSession,
    TableStateRow,
)
from mesa_monitor.db.session import make_sessionmaker
from mesa_monitor.vision.persistence import Persistence, TableMeta

pytestmark = pytest.mark.db
T0 = datetime(2026, 10, 18, 21, 0, tzinfo=UTC)


async def test_events_are_persisted_and_state_restored(clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tables (id, label, sector) VALUES ('t1', '1', 'salon')")
        )
    sm = make_sessionmaker(engine)
    persist = Persistence(sm, ZoneInfo("America/Argentina/Cordoba"))
    persist.tables = {"t1": TableMeta("t1", "1", "salon")}

    state = StateEngine()
    for s in range(0, 40):  # se sientan
        ev = state.observe(Observation("t1", T0 + timedelta(seconds=s), 2, False, 0))
        await persist.apply(ev, [state.machines["t1"].snapshot()])
    for s in range(40, 50):  # visita del mozo
        ev = state.observe(Observation("t1", T0 + timedelta(seconds=s), 2, True, 0))
        await persist.apply(ev, [state.machines["t1"].snapshot()])

    async with sm() as db:
        ses = (await db.execute(select(TableSession))).scalar_one()
        assert ses.seated_at == T0
        assert ses.first_attention_at == T0 + timedelta(seconds=40)
        assert ses.business_date.isoformat() == "2026-10-18"
        assert (await db.execute(select(func.count()).select_from(StaffVisit))).scalar_one() == 1
        trans = (
            (await db.execute(select(StateTransition.to_state).order_by(StateTransition.id)))
            .scalars()
            .all()
        )
        assert trans == [TableState.OCUPADA_SIN_ATENDER, TableState.ATENDIDA]
        types = (await db.execute(select(EventLog.type).order_by(EventLog.ts))).scalars().all()
        assert types.count("table.state_changed") == 2
        assert types.count("table.staff_visit") == 1
        st = (await db.execute(select(TableStateRow))).scalar_one()
        assert st.state is TableState.ATENDIDA

    # reinicio: el worker reconstruye desde la base y sigue la misma sesión
    snaps = await persist.load_snapshots(["t1"])
    m = TableMachine.restore(snaps[0], EngineParams())
    assert m.state is TableState.ATENDIDA
    assert m.session is not None
    assert m.session.id == ses.id
    assert m.session.last_visit_at == T0 + timedelta(seconds=40)
    await engine.dispose()


async def test_restore_without_state_row_uses_open_session(clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tables (id, label, sector) VALUES ('t2', '2', 'salon')")
        )
        await conn.execute(
            text(
                "INSERT INTO table_sessions (id, table_id, business_date, seated_at)"
                " VALUES ('ses_a', 't2', '2026-10-18', :ts)"
            ),
            {"ts": T0},
        )
    persist = Persistence(make_sessionmaker(engine), ZoneInfo("UTC"))
    (snap,) = await persist.load_snapshots(["t2"])
    assert snap.state is TableState.OCUPADA_SIN_ATENDER
    assert snap.session_id == "ses_a"
    await engine.dispose()
