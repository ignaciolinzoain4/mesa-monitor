"""Motor de alertas (SPEC §10): cada segundo compara las alertas que deberían estar activas
(`core.alerts`) con las de la base y abre, escala o resuelve.

Ciclo de vida: abierta → (reconocida) → resuelta.
- Una sola alerta activa por (mesa|cámara, tipo): la base lo garantiza con un índice único.
- Escalamiento warning → critical: se actualiza la MISMA alerta y vuelve a "abierta" (anula
  "Visto" y "Posponer": una alerta que empeora tiene que volver a sonar).
- Resolución automática cuando la condición desaparece (la mesa cambió de estado).
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.api.camera_monitor import CameraMonitor
from mesa_monitor.api.publish import publish
from mesa_monitor.api.state_view import alert_data
from mesa_monitor.core.alerts import (
    SEVERITY_RANK,
    DesiredAlert,
    TableAlertInput,
    camera_alert,
    table_alerts,
)
from mesa_monitor.core.enums import AlertStatus, CameraStatus
from mesa_monitor.core.ids import new_id
from mesa_monitor.core.thresholds import ThresholdRow, resolve
from mesa_monitor.db.models import (
    Alert,
    Camera,
    StaffVisit,
    Table,
    TableSession,
    TableStateRow,
    Threshold,
)

log = logging.getLogger(__name__)


class AlertEngine:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        monitor: CameraMonitor,
        tz: Any,
        interval_s: float = 1.0,
    ) -> None:
        self._sm = sessionmaker
        self._monitor = monitor
        self._tz = tz
        self.interval_s = interval_s
        self._task: asyncio.Task[None] | None = None
        self._thresholds: list[ThresholdRow] | None = None

    def invalidate(self) -> None:
        """Umbrales cambiados: se recargan en el próximo tick (aplicación en caliente)."""
        self._thresholds = None

    async def _rows(self, db: AsyncSession) -> list[ThresholdRow]:
        if self._thresholds is None:
            self._thresholds = [
                ThresholdRow(r.scope, r.scope_ref, r.key, r.value, r.valid_from, r.valid_to)
                for r in (await db.execute(select(Threshold))).scalars()
            ]
        return self._thresholds

    async def desired(self, db: AsyncSession, now: datetime) -> list[DesiredAlert]:
        rows = await self._rows(db)
        local_t = now.astimezone(self._tz).time()
        tables = {t.id: t for t in (await db.execute(select(Table).where(Table.enabled))).scalars()}
        states = (await db.execute(select(TableStateRow))).scalars().all()
        sessions = {
            s.table_id: s
            for s in (
                await db.execute(select(TableSession).where(TableSession.closed_at.is_(None)))
            ).scalars()
        }
        last_visit: dict[str, datetime] = {}
        if sessions:
            for sid, started in (
                await db.execute(
                    select(StaffVisit.session_id, StaffVisit.started_at).where(
                        StaffVisit.session_id.in_([s.id for s in sessions.values()])
                    )
                )
            ).all():
                if sid not in last_visit or started > last_visit[sid]:
                    last_visit[sid] = started
        out: list[DesiredAlert] = []
        for st in states:
            t = tables.get(st.table_id)
            if t is None:
                continue
            ses = sessions.get(t.id)
            values = resolve(rows, table_id=t.id, sector=t.sector, local_time=local_t)
            out += table_alerts(
                TableAlertInput(
                    table_id=t.id,
                    state=st.state,
                    state_since=st.state_since,
                    paused_s=st.paused_s,
                    seated_at=ses.seated_at if ses else None,
                    first_attention_at=ses.first_attention_at if ses else None,
                    last_visit_at=last_visit.get(ses.id) if ses else None,
                ),
                values,
                now,
            )
        global_values = resolve(rows, local_time=local_t)
        for cam_id in (await db.execute(select(Camera.id).where(Camera.enabled))).scalars():
            h = self._monitor.health(cam_id)
            if h is None or h.status is not CameraStatus.OFFLINE:
                continue
            down_since = h.first_failure_at or h.last_checked_at
            a = camera_alert(cam_id, down_since, global_values, now)
            if a:
                out.append(a)
        return out

    async def tick(self, now: datetime | None = None) -> int:
        """Reconciliación. Devuelve cuántos eventos se publicaron."""
        now = now or datetime.now(UTC)
        published = 0
        async with self._sm() as db:
            desired = {d.key: d for d in await self.desired(db, now)}
            active = (
                (await db.execute(select(Alert).where(Alert.status != AlertStatus.RESOLVED)))
                .scalars()
                .all()
            )
            tables = {t.id: t for t in (await db.execute(select(Table))).scalars()}
            by_key = {(a.table_id or f"cam:{a.camera_id}", a.alert_type): a for a in active}

            for key, d in desired.items():
                a = by_key.get(key)
                if a is None:
                    a = Alert(
                        id=new_id("alr"),
                        alert_type=d.alert_type,
                        severity=d.severity,
                        status=AlertStatus.OPEN,
                        table_id=d.table_id,
                        camera_id=d.camera_id,
                        session_id=None,
                        state=d.state,
                        state_since=d.since,
                        threshold_seconds=d.threshold_s,
                        opened_at=now,
                        updated_at=now,
                    )
                    db.add(a)
                    await db.flush()
                    await publish(
                        db,
                        "alert.opened",
                        alert_data(a, tables.get(a.table_id or ""), now),
                        a.table_id,
                    )
                    published += 1
                elif SEVERITY_RANK[d.severity] != SEVERITY_RANK[a.severity]:
                    escalated = SEVERITY_RANK[d.severity] > SEVERITY_RANK[a.severity]
                    a.severity = d.severity
                    a.threshold_seconds = d.threshold_s
                    a.updated_at = now
                    if escalated:
                        a.status = AlertStatus.OPEN
                        a.acknowledged_at = None
                        a.acknowledged_by = None
                        a.snoozed_until = None
                    await publish(
                        db,
                        "alert.updated",
                        alert_data(a, tables.get(a.table_id or ""), now),
                        a.table_id,
                    )
                    published += 1

            for key, a in by_key.items():
                if key in desired:
                    continue
                a.status = AlertStatus.RESOLVED
                a.resolved_at = now
                a.updated_at = now
                a.resolve_reason = "condition_cleared"
                await publish(
                    db,
                    "alert.resolved",
                    alert_data(a, tables.get(a.table_id or ""), now),
                    a.table_id,
                )
                published += 1
            await db.commit()
        return published

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Error en el motor de alertas")
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="alert-engine")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
