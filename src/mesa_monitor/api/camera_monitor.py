"""Monitor de cámaras: pide un frame del substream a go2rtc cada `probe_interval_s`.

Estados:
- `online`   — llegó un frame y la resolución coincide con la esperada (si hay una configurada).
- `degraded` — llega imagen con otra resolución (zonas desalineadas o substream mal configurado).
- `offline`  — no llega imagen hace más de `offline_after_s`.

Cada cambio de estado se guarda en `camera_status_log` y se publica como evento `camera.status`.
El worker reporta además fps y latencia por cámara (Salud del sistema).
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.enums import CameraStatus
from mesa_monitor.db.models import CameraStatusLog
from mesa_monitor.vision.go2rtc import Go2rtcClient, SnapshotError, StreamKind

log = logging.getLogger(__name__)


@dataclass
class CameraHealth:
    camera_id: str
    expected_resolution: tuple[int, int] | None
    status: CameraStatus = CameraStatus.OFFLINE
    last_ok_at: datetime | None = None
    last_checked_at: datetime | None = None
    resolution: tuple[int, int] | None = None
    detail: str | None = "Todavía no se consultó"
    first_failure_at: datetime | None = field(default=None, repr=False)
    reported: bool = field(default=False, repr=False)  # ya se registró el primer estado


class CameraMonitor:
    def __init__(
        self,
        client: Go2rtcClient,
        sessionmaker: async_sessionmaker[AsyncSession] | None,
        *,
        probe_interval_s: float,
        offline_after_s: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._sessionmaker = sessionmaker
        self._interval = probe_interval_s
        self._offline_after = offline_after_s
        self._clock = clock
        self._health: dict[str, CameraHealth] = {}
        self.names: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None

    def set_cameras(self, cameras: dict[str, tuple[int, int] | None]) -> None:
        """Cámaras a vigilar: id -> resolución esperada del substream."""
        self._health = {
            cid: self._health.get(cid) or CameraHealth(cid, expected)
            for cid, expected in cameras.items()
        }
        for cid, expected in cameras.items():
            self._health[cid].expected_resolution = expected

    def health(self, camera_id: str) -> CameraHealth | None:
        return self._health.get(camera_id)

    async def check(self, camera_id: str) -> CameraHealth:
        h = self._health[camera_id]
        now = self._clock()
        previous = h.status
        h.last_checked_at = now
        try:
            frame = await self._client.frame(camera_id, StreamKind.SUB)
        except SnapshotError as exc:
            h.first_failure_at = h.first_failure_at or now
            h.detail = str(exc)
            down_for = (now - h.first_failure_at).total_seconds()
            # Antes del primer frame no hay nada que tolerar: la cámara está caída.
            if h.last_ok_at is None or down_for >= self._offline_after:
                h.status = CameraStatus.OFFLINE
        else:
            h.first_failure_at = None
            h.last_ok_at = now
            h.resolution = (frame.width, frame.height) if frame.width and frame.height else None
            if h.expected_resolution and h.resolution != h.expected_resolution:
                h.status = CameraStatus.DEGRADED
                got = f"{frame.width}x{frame.height}"
                exp = "x".join(map(str, h.expected_resolution))
                h.detail = f"Resolución {got}, se esperaba {exp}"
            else:
                h.status = CameraStatus.ONLINE
                h.detail = None
        if h.status is not previous or not h.reported:
            h.reported = True
            await self._log_change(h)
        return h

    async def _log_change(self, h: CameraHealth) -> None:
        log.info("Cámara %s: %s (%s)", h.camera_id, h.status.value, h.detail or "ok")
        if self._sessionmaker is None:
            return
        from mesa_monitor.api.publish import publish
        from mesa_monitor.core.events import CameraStatusData

        async with self._sessionmaker() as session:
            session.add(
                CameraStatusLog(
                    camera_id=h.camera_id,
                    ts=h.last_checked_at or self._clock(),
                    status=h.status,
                    detail=h.detail,
                )
            )
            await publish(
                session,
                "camera.status",
                CameraStatusData(
                    camera_id=h.camera_id,
                    name=self.names.get(h.camera_id, h.camera_id),
                    status=h.status,
                    detail=h.detail,
                ),
            )
            await session.commit()

    async def run_once(self) -> None:
        await asyncio.gather(*(self._safe_check(cid) for cid in list(self._health)))

    async def _safe_check(self, camera_id: str) -> None:
        try:
            await self.check(camera_id)
        except Exception:
            log.exception("Error inesperado chequeando la cámara %s", camera_id)

    async def _loop(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self._interval)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="camera-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
