"""Persistencia de los eventos del state engine y reconstrucción tras un reinicio (SPEC §4.2).

Cada tick se escribe en UNA transacción: sesiones, visitas, transiciones, `table_state`, la
bandeja `events` y un `NOTIFY mesa_events` por evento (la API los difunde por WebSocket y
webhooks). No se guarda nada que identifique personas (SPEC Anexo A).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.enums import TableState
from mesa_monitor.core.events import (
    StaffVisitData,
    TableRef,
    TableStaffVisitEvent,
    TableStateChangedEvent,
    TableStateData,
)
from mesa_monitor.core.ids import new_id
from mesa_monitor.core.schedule import DEFAULT_CUTOFF, business_date
from mesa_monitor.core.state_engine import (
    Event,
    SessionClosed,
    SessionOpened,
    SessionUpdated,
    TableSnapshot,
    Transition,
    VisitEnded,
    VisitStarted,
)
from mesa_monitor.db.audit import CHANNEL_EVENTS, notify
from mesa_monitor.db.models import (
    EventLog,
    StaffVisit,
    StateTransition,
    TableSession,
    TableStateRow,
)


@dataclass(frozen=True)
class TableMeta:
    id: str
    label: str
    sector: str

    @property
    def ref(self) -> TableRef:
        return TableRef(id=self.id, label=self.label, sector=self.sector)


def elapsed_seconds(snap: TableSnapshot, now: datetime) -> float | None:
    if snap.state_since is None:
        return None
    return max(0.0, (now - snap.state_since).total_seconds() - snap.paused_s)


def state_data(
    meta: TableMeta, snap: TableSnapshot, now: datetime, t: Transition | None = None
) -> TableStateData:
    return TableStateData(
        table=meta.ref,
        state=snap.state,
        state_since=snap.state_since,
        elapsed_seconds=elapsed_seconds(snap, now),
        previous_state=t.from_state if t else None,
        reason=t.reason if t else None,
        session_id=snap.session_id,
        seated_at=snap.seated_at,
        first_attention_at=snap.first_attention_at,
        last_visit_at=snap.last_visit_at,
        vacated_at=snap.vacated_at,
        people=snap.people_now,
    )


class Persistence:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        tz: ZoneInfo,
        cutoff: time = DEFAULT_CUTOFF,
    ) -> None:
        self._sm = sessionmaker
        self.tz = tz
        self.cutoff = cutoff
        self.tables: dict[str, TableMeta] = {}

    async def apply(
        self,
        events: Sequence[Event],
        snapshots: Iterable[TableSnapshot],
        now: datetime | None = None,
    ) -> int:
        """Guarda eventos + snapshots. Devuelve cuántos eventos públicos se emitieron."""
        now = now or datetime.now(UTC)
        snaps = {s.table_id: s for s in snapshots}
        published = 0
        async with self._sm() as db:
            for e in events:
                published += await self._apply_one(db, e, snaps, now)
            for snap in snaps.values():
                await self._upsert_state(db, snap, now)
            await db.commit()
        return published

    async def _apply_one(
        self, db: AsyncSession, e: Event, snaps: dict[str, TableSnapshot], now: datetime
    ) -> int:
        if isinstance(e, SessionOpened):
            db.add(
                TableSession(
                    id=e.session_id,
                    table_id=e.table_id,
                    business_date=business_date(e.seated_at, self.tz, self.cutoff),
                    seated_at=e.seated_at,
                )
            )
            await db.flush()
        elif isinstance(e, SessionUpdated):
            await db.execute(
                update(TableSession)
                .where(TableSession.id == e.session_id)
                .values(
                    first_attention_at=e.first_attention_at,
                    vacated_at=e.vacated_at,
                    max_people=e.max_people,
                    vision_lost_seconds=e.vision_lost_seconds,
                )
            )
        elif isinstance(e, SessionClosed):
            await db.execute(
                update(TableSession)
                .where(TableSession.id == e.session_id)
                .values(closed_at=e.closed_at, close_reason=e.reason, cleaned_at=e.cleaned_at)
            )
        elif isinstance(e, VisitStarted):
            db.add(
                StaffVisit(
                    id=e.visit_id,
                    session_id=e.session_id,
                    table_id=e.table_id,
                    started_at=e.started_at,
                    confidence=e.confidence,
                )
            )
            await db.flush()
            meta = self.tables.get(e.table_id)
            if meta is not None:
                ev = TableStaffVisitEvent(
                    id=new_id("evt"),
                    ts=now,
                    data=StaffVisitData(
                        table=meta.ref,
                        session_id=e.session_id,
                        visit_id=e.visit_id,
                        started_at=e.started_at,
                    ),
                )
                await self._publish(
                    db, ev.type, ev.id, ev.ts, e.table_id, ev.model_dump(mode="json")
                )
                return 1
        elif isinstance(e, VisitEnded):
            await db.execute(
                update(StaffVisit).where(StaffVisit.id == e.visit_id).values(ended_at=e.ended_at)
            )
        elif isinstance(e, Transition):
            db.add(
                StateTransition(
                    table_id=e.table_id,
                    session_id=e.session_id,
                    ts=e.ts,
                    from_state=e.from_state,
                    to_state=e.to_state,
                    reason=e.reason,
                    confidence=e.confidence,
                )
            )
            meta = self.tables.get(e.table_id)
            snap = snaps.get(e.table_id)
            if meta is not None and snap is not None:
                ev2 = TableStateChangedEvent(
                    id=new_id("evt"), ts=now, data=state_data(meta, snap, now, e)
                )
                # El snapshot es el estado final del tick: el evento lleva el destino real.
                ev2.data.state = e.to_state
                ev2.data.previous_state = e.from_state
                await self._publish(
                    db, ev2.type, ev2.id, ev2.ts, e.table_id, ev2.model_dump(mode="json")
                )
                return 1
        return 0

    async def _publish(
        self,
        db: AsyncSession,
        type_: str,
        event_id: str,
        ts: datetime,
        table_id: str | None,
        payload: dict[str, object],
    ) -> None:
        db.add(EventLog(id=event_id, type=type_, ts=ts, table_id=table_id, payload=payload))
        await notify(db, CHANNEL_EVENTS, {"id": event_id, "type": type_})

    async def _upsert_state(self, db: AsyncSession, s: TableSnapshot, now: datetime) -> None:
        values = {
            "state": s.state,
            "state_since": s.state_since,
            "paused_s": s.paused_s,
            "resume_state": s.resume_state,
            "vision_lost_since": s.vision_lost_since,
            "session_id": s.session_id,
            "people_now": s.people_now,
            "staff_present": s.staff_present,
            "updated_at": s.updated_at or now,
        }
        stmt = insert(TableStateRow).values(table_id=s.table_id, **values)
        await db.execute(stmt.on_conflict_do_update(index_elements=["table_id"], set_=values))

    async def load_snapshots(self, table_ids: Iterable[str]) -> list[TableSnapshot]:
        """Estado vigente desde la base: `table_state` + sesión abierta + última visita."""
        ids = list(table_ids)
        out: list[TableSnapshot] = []
        async with self._sm() as db:
            states = {
                r.table_id: r
                for r in (
                    await db.execute(select(TableStateRow).where(TableStateRow.table_id.in_(ids)))
                ).scalars()
            }
            sessions = {
                r.table_id: r
                for r in (
                    await db.execute(
                        select(TableSession).where(
                            TableSession.table_id.in_(ids), TableSession.closed_at.is_(None)
                        )
                    )
                ).scalars()
            }
            last_visit: dict[str, datetime] = {}
            if sessions:
                rows = await db.execute(
                    select(StaffVisit.session_id, StaffVisit.started_at).where(
                        StaffVisit.session_id.in_([s.id for s in sessions.values()])
                    )
                )
                for sid, started in rows.all():
                    if sid not in last_visit or started > last_visit[sid]:
                        last_visit[sid] = started
        for tid in ids:
            st = states.get(tid)
            ses = sessions.get(tid)
            if st is None and ses is None:
                continue
            state = st.state if st else TableState.OCUPADA_SIN_ATENDER
            # Sesión abierta pero estado inconsistente (ej. corte a mitad de una escritura):
            # se reconstruye desde la sesión.
            if ses is not None and state in (TableState.LIBRE, TableState.DESHABILITADA):
                state = (
                    TableState.ATENDIDA
                    if ses.first_attention_at
                    else TableState.OCUPADA_SIN_ATENDER
                )
            if ses is None and state not in (
                TableState.LIBRE,
                TableState.DESHABILITADA,
                TableState.SIN_VISION,
            ):
                state = TableState.LIBRE
            out.append(
                TableSnapshot(
                    table_id=tid,
                    state=state,
                    state_since=st.state_since if st else (ses.seated_at if ses else None),
                    paused_s=st.paused_s if st else 0.0,
                    resume_state=st.resume_state if st else None,
                    vision_lost_since=st.vision_lost_since if st else None,
                    session_id=ses.id if ses else None,
                    seated_at=ses.seated_at if ses else None,
                    first_attention_at=ses.first_attention_at if ses else None,
                    vacated_at=ses.vacated_at if ses else None,
                    last_visit_at=last_visit.get(ses.id) if ses else None,
                    max_people=ses.max_people if ses else 0,
                    vision_lost_seconds=ses.vision_lost_seconds if ses else 0.0,
                    people_now=0,
                    staff_present=False,
                    updated_at=st.updated_at if st else None,
                )
            )
        return out
