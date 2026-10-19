"""Estado vigente, sesiones y su línea de tiempo (SPEC §12.1)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select

from mesa_monitor.api.deps import AnyPrincipal, AuthUser, DbSession
from mesa_monitor.api.state_view import snapshot
from mesa_monitor.core.enums import SessionCloseReason
from mesa_monitor.core.events import SnapshotData, TableRef
from mesa_monitor.db.models import StaffVisit, StateTransition, Table, TableSession

router = APIRouter(tags=["state"])


@router.get("/state", response_model=SnapshotData)
async def get_state(request: Request, db: DbSession, _: AnyPrincipal) -> SnapshotData:
    """Snapshot de todas las mesas, alertas activas y cámaras (lo mismo que el primer mensaje
    del WebSocket)."""
    return await snapshot(db, request.app.state.camera_monitor)


class VisitOut(BaseModel):
    started_at: datetime
    ended_at: datetime | None


class SessionOut(BaseModel):
    id: str
    table: TableRef
    business_date: str
    seated_at: datetime
    first_attention_at: datetime | None
    vacated_at: datetime | None
    cleaned_at: datetime | None
    closed_at: datetime | None
    close_reason: SessionCloseReason | None
    max_people: int
    visits: int
    wait_seconds: float | None
    stay_seconds: float | None
    dirty_seconds: float | None
    vision_lost_seconds: float


class TransitionOut(BaseModel):
    ts: datetime
    from_state: str | None
    to_state: str
    reason: str


class SessionDetail(SessionOut):
    visit_list: list[VisitOut]
    transitions: list[TransitionOut]


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    return (b - a).total_seconds() if a and b else None


def _out(s: TableSession, t: Table, visits: int) -> SessionOut:
    return SessionOut(
        id=s.id,
        table=TableRef(id=t.id, label=t.label, sector=t.sector),
        business_date=s.business_date.isoformat(),
        seated_at=s.seated_at,
        first_attention_at=s.first_attention_at,
        vacated_at=s.vacated_at,
        cleaned_at=s.cleaned_at,
        closed_at=s.closed_at,
        close_reason=s.close_reason,
        max_people=s.max_people,
        visits=visits,
        wait_seconds=_secs(s.seated_at, s.first_attention_at),
        stay_seconds=_secs(s.seated_at, s.vacated_at),
        dirty_seconds=_secs(s.vacated_at, s.cleaned_at),
        vision_lost_seconds=s.vision_lost_seconds,
    )


async def _list(
    db: DbSession,
    from_: datetime | None,
    to: datetime | None,
    sector: str | None,
    table: str | None,
    limit: int,
) -> list[SessionOut]:
    to = to or datetime.now(UTC)
    from_ = from_ or to - timedelta(days=1)
    q = (
        select(TableSession, Table)
        .join(Table, Table.id == TableSession.table_id)
        .where(TableSession.seated_at >= from_, TableSession.seated_at < to)
        .order_by(TableSession.seated_at.desc())
        .limit(limit)
    )
    if sector:
        q = q.where(Table.sector == sector)
    if table:
        q = q.where((Table.id == table) | (Table.label == table))
    rows = (await db.execute(q)).all()
    counts: dict[str, int] = {}
    if rows:
        for sid in (
            await db.execute(
                select(StaffVisit.session_id).where(
                    StaffVisit.session_id.in_([s.id for s, _ in rows])
                )
            )
        ).scalars():
            counts[sid] = counts.get(sid, 0) + 1
    return [_out(s, t, counts.get(s.id, 0)) for s, t in rows]


FromQ = Annotated[datetime | None, Query(alias="from", description="Desde (ISO-8601)")]
ToQ = Annotated[datetime | None, Query(description="Hasta (ISO-8601)")]


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    db: DbSession,
    _: AuthUser,
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = Query(default=None, description="id o etiqueta de mesa"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[SessionOut]:
    return await _list(db, from_, to, sector, table, limit)


@router.get("/tables/{table_id}/sessions", response_model=list[SessionOut])
async def table_sessions(
    table_id: str, db: DbSession, _: AuthUser, from_: FromQ = None, to: ToQ = None
) -> list[SessionOut]:
    if await db.get(Table, table_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Mesa inexistente")
    return await _list(db, from_, to, None, table_id, 5000)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, db: DbSession, _: AuthUser) -> SessionDetail:
    row = (
        await db.execute(
            select(TableSession, Table)
            .join(Table, Table.id == TableSession.table_id)
            .where(TableSession.id == session_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Sesión inexistente")
    s, t = row
    visits = (
        (
            await db.execute(
                select(StaffVisit)
                .where(StaffVisit.session_id == s.id)
                .order_by(StaffVisit.started_at)
            )
        )
        .scalars()
        .all()
    )
    trans = (
        (
            await db.execute(
                select(StateTransition)
                .where(StateTransition.session_id == s.id)
                .order_by(StateTransition.ts)
            )
        )
        .scalars()
        .all()
    )
    base = _out(s, t, len(visits))
    return SessionDetail(
        **base.model_dump(),
        visit_list=[VisitOut(started_at=v.started_at, ended_at=v.ended_at) for v in visits],
        transitions=[
            TransitionOut(ts=x.ts, from_state=x.from_state, to_state=x.to_state, reason=x.reason)
            for x in trans
        ],
    )
