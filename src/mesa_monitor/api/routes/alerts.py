"""Alertas: listado, "Visto" (reconocer) y "Posponer" (SPEC §10.3)."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from mesa_monitor.api.deps import AnyPrincipal, DbSession
from mesa_monitor.api.publish import publish
from mesa_monitor.api.state_view import alert_data
from mesa_monitor.core.enums import AlertStatus
from mesa_monitor.core.events import AlertData
from mesa_monitor.core.thresholds import ThresholdRow, resolve
from mesa_monitor.db.audit import record_audit
from mesa_monitor.db.models import Alert, Table, Threshold

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertScreenSettings(BaseModel):
    sound_repeat_s: float


@router.get("/screen-settings", response_model=AlertScreenSettings)
async def screen_settings(db: DbSession, _: AnyPrincipal) -> AlertScreenSettings:
    """Parámetros de la pantalla /alertas (los dispositivos no pueden leer todos los umbrales)."""
    rows = [
        ThresholdRow(r.scope, r.scope_ref, r.key, r.value, r.valid_from, r.valid_to)
        for r in (await db.execute(select(Threshold))).scalars()
    ]
    return AlertScreenSettings(sound_repeat_s=float(resolve(rows)["alert_sound_repeat_s"]))


@router.get("", response_model=list[AlertData])
async def list_alerts(
    db: DbSession,
    _: AnyPrincipal,
    status_: Annotated[Literal["active", "all"], Query(alias="status")] = "active",
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[AlertData]:
    q = select(Alert, Table).outerjoin(Table, Table.id == Alert.table_id)
    if status_ == "active":
        q = q.where(Alert.status != AlertStatus.RESOLVED)
    if from_:
        q = q.where(Alert.opened_at >= from_)
    if to:
        q = q.where(Alert.opened_at < to)
    rows = (await db.execute(q.order_by(Alert.opened_at.desc()).limit(limit))).all()
    now = datetime.now(UTC)
    return [alert_data(a, t, now) for a, t in rows]


async def _active(db: DbSession, alert_id: str) -> tuple[Alert, Table | None]:
    row = (
        await db.execute(
            select(Alert, Table)
            .outerjoin(Table, Table.id == Alert.table_id)
            .where(Alert.id == alert_id)
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Alerta inexistente")
    a, t = row
    if a.status is AlertStatus.RESOLVED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="La alerta ya está resuelta")
    return a, t


@router.post("/{alert_id}/ack", response_model=AlertData)
async def ack_alert(alert_id: str, db: DbSession, who: AnyPrincipal) -> AlertData:
    """ "Visto": silencia el sonido; la alerta sigue visible hasta resolverse."""
    a, t = await _active(db, alert_id)
    now = datetime.now(UTC)
    a.status = AlertStatus.ACKNOWLEDGED
    a.acknowledged_at = now
    # Cumplimiento: se registra el dispositivo/usuario que reconoció, nunca un empleado detectado.
    a.acknowledged_by = who.actor.label
    a.updated_at = now
    data = alert_data(a, t, now)
    await publish(db, "alert.updated", data, a.table_id)
    await record_audit(db, who.actor, "ack", "alert", a.id)
    await db.commit()
    return data


class SnoozeIn(BaseModel):
    minutes: int = Field(ge=1, le=240)


@router.post("/{alert_id}/snooze", response_model=AlertData)
async def snooze_alert(
    alert_id: str, body: SnoozeIn, db: DbSession, who: AnyPrincipal
) -> AlertData:
    a, t = await _active(db, alert_id)
    now = datetime.now(UTC)
    a.snoozed_until = now + timedelta(minutes=body.minutes)
    a.updated_at = now
    data = alert_data(a, t, now)
    await publish(db, "alert.updated", data, a.table_id)
    await record_audit(db, who.actor, "snooze", "alert", a.id, after={"minutes": body.minutes})
    await db.commit()
    return data
