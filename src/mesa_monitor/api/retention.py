"""Limpieza periódica de datos de alto volumen (SPEC §15). Las sesiones y visitas se guardan
indefinidamente; se recortan transiciones, log de cámaras, eventos y entregas de webhooks.

Días configurables en `app_settings.retention` (ej. {"state_transitions": 180}).
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.enums import DeliveryStatus
from mesa_monitor.db.models import (
    AppSetting,
    CameraStatusLog,
    EventLog,
    StateTransition,
    WebhookDelivery,
)

log = logging.getLogger(__name__)
DEFAULT_DAYS = {
    "state_transitions": 180,
    "camera_status_log": 90,
    "events": 7,
    "webhook_deliveries": 30,
}


async def run_retention(
    sm: async_sessionmaker[AsyncSession], now: datetime | None = None
) -> dict[str, int]:
    now = now or datetime.now(UTC)
    async with sm() as db:
        row = await db.get(AppSetting, "retention")
        days = {**DEFAULT_DAYS, **(row.value if row and isinstance(row.value, dict) else {})}
        deleted = {}
        targets = [
            ("state_transitions", StateTransition, StateTransition.ts),
            ("camera_status_log", CameraStatusLog, CameraStatusLog.ts),
            ("events", EventLog, EventLog.ts),
        ]
        for key, model, col in targets:
            res = await db.execute(delete(model).where(col < now - timedelta(days=int(days[key]))))
            deleted[key] = int(res.rowcount or 0)  # type: ignore[attr-defined]
        res = await db.execute(
            delete(WebhookDelivery).where(
                WebhookDelivery.created_at < now - timedelta(days=int(days["webhook_deliveries"])),
                WebhookDelivery.status != DeliveryStatus.PENDING,
            )
        )
        deleted["webhook_deliveries"] = int(res.rowcount or 0)  # type: ignore[attr-defined]
        await db.commit()
    if any(deleted.values()):
        log.info("Retención: borrados %s", deleted)
    return deleted


class RetentionTask:
    def __init__(self, sm: async_sessionmaker[AsyncSession], every_s: float = 6 * 3600) -> None:
        self._sm = sm
        self.every_s = every_s
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            try:
                await run_retention(self._sm)
            except Exception:
                log.exception("Error en la limpieza de datos")
            await asyncio.sleep(self.every_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="retention")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
