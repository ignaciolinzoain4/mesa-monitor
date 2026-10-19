"""Lectura del estado vigente (mesas, alertas activas, cámaras) para la API y el WebSocket."""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.api.camera_monitor import CameraMonitor
from mesa_monitor.core.enums import AlertStatus, CameraStatus, TableState
from mesa_monitor.core.events import (
    AlertData,
    CameraStatusData,
    SnapshotData,
    TableRef,
    TableStateData,
)
from mesa_monitor.db.models import Alert, Camera, StaffVisit, Table, TableSession, TableStateRow


async def table_states(db: AsyncSession, now: datetime | None = None) -> list[TableStateData]:
    now = now or datetime.now(UTC)
    tables = (await db.execute(select(Table).order_by(Table.sector, Table.label))).scalars().all()
    states = {r.table_id: r for r in (await db.execute(select(TableStateRow))).scalars()}
    sessions = {
        s.table_id: s
        for s in (
            await db.execute(select(TableSession).where(TableSession.closed_at.is_(None)))
        ).scalars()
    }
    last_visit = dict(
        (
            await db.execute(
                select(StaffVisit.session_id, func.max(StaffVisit.started_at))
                .where(StaffVisit.session_id.in_([s.id for s in sessions.values()]))
                .group_by(StaffVisit.session_id)
            )
        ).all()
    )
    out = []
    for t in tables:
        st = states.get(t.id)
        ses = sessions.get(t.id)
        state = (
            TableState.DESHABILITADA if not t.enabled else (st.state if st else TableState.LIBRE)
        )
        since = st.state_since if st else None
        elapsed = None
        if since is not None:
            elapsed = max(0.0, (now - since).total_seconds() - (st.paused_s if st else 0.0))
        out.append(
            TableStateData(
                table=TableRef(id=t.id, label=t.label, sector=t.sector),
                state=state,
                state_since=since,
                elapsed_seconds=elapsed,
                session_id=ses.id if ses else None,
                seated_at=ses.seated_at if ses else None,
                first_attention_at=ses.first_attention_at if ses else None,
                last_visit_at=last_visit.get(ses.id) if ses else None,
                vacated_at=ses.vacated_at if ses else None,
                people=st.people_now if st else 0,
            )
        )
    return out


def alert_data(a: Alert, table: Table | None, now: datetime | None = None) -> AlertData:
    now = now or datetime.now(UTC)
    elapsed = (now - a.state_since).total_seconds() if a.state_since else None
    return AlertData(
        alert_id=a.id,
        alert_type=a.alert_type,
        severity=a.severity,
        status=a.status,
        table=TableRef(id=table.id, label=table.label, sector=table.sector) if table else None,
        camera_id=a.camera_id,
        state=a.state,
        state_since=a.state_since,
        elapsed_seconds=elapsed,
        threshold_seconds=a.threshold_seconds,
        opened_at=a.opened_at,
        acknowledged_at=a.acknowledged_at,
        snoozed_until=a.snoozed_until,
        resolved_at=a.resolved_at,
    )


async def active_alerts(db: AsyncSession) -> list[AlertData]:
    rows = (
        await db.execute(
            select(Alert, Table)
            .outerjoin(Table, Table.id == Alert.table_id)
            .where(Alert.status != AlertStatus.RESOLVED)
            .order_by(Alert.opened_at)
        )
    ).all()
    return [alert_data(a, t) for a, t in rows]


async def camera_states(db: AsyncSession, monitor: CameraMonitor) -> list[CameraStatusData]:
    out = []
    for c in (await db.execute(select(Camera).where(Camera.enabled).order_by(Camera.id))).scalars():
        h = monitor.health(c.id)
        out.append(
            CameraStatusData(
                camera_id=c.id,
                name=c.name,
                status=h.status if h else CameraStatus.OFFLINE,
                detail=h.detail if h else None,
            )
        )
    return out


async def snapshot(db: AsyncSession, monitor: CameraMonitor) -> SnapshotData:
    return SnapshotData(
        tables=await table_states(db),
        alerts=await active_alerts(db),
        cameras=await camera_states(db, monitor),
    )
