"""Vision-worker: cámaras en vivo → state engine → base (SPEC §4).

Ciclo:
1. Carga la configuración (cámaras, zonas, umbrales, perfil de personal, horarios) y
   reconstruye el estado vigente de cada mesa desde la base (no pierde sesiones).
2. Un hilo por cámara lee el substream desde go2rtc (RTSP local) y guarda el último frame.
3. Cada tick procesa las cámaras que corresponden según su fps, fusiona las vistas de cada mesa
   (mayor prioridad sana) y alimenta el state engine; los eventos se guardan y notifican.
4. `LISTEN config_changed`: zonas, mesas y umbrales se aplican en caliente.
5. Fuera del horario operativo queda en reposo; al cerrar, cierra las sesiones abiertas.
"""

import asyncio
import contextlib
import logging
import signal
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any

import asyncpg
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from mesa_monitor.config import Settings, get_settings
from mesa_monitor.core.enums import SessionCloseReason
from mesa_monitor.core.schedule import is_open
from mesa_monitor.core.state_engine import Event, StateEngine
from mesa_monitor.db.audit import CHANNEL_CONFIG
from mesa_monitor.db.models import AppSetting
from mesa_monitor.db.session import make_engine, make_sessionmaker
from mesa_monitor.log import setup_logging
from mesa_monitor.vision.config_loader import WorkerConfig, load_worker_config
from mesa_monitor.vision.persistence import Persistence

log = logging.getLogger("mesa_monitor.worker")

FRAME_STALE_S = 10.0  # un frame más viejo que esto = cámara sin imagen
STATUS_EVERY_S = 10.0
DEBUG_PORT = 8001


async def wait_for_database(database_url: str, max_delay_s: float = 30.0) -> None:
    engine = make_engine(database_url)
    delay = 1.0
    try:
        while True:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return
            except Exception as exc:  # reintenta con backoff exponencial
                log.warning("Base no disponible (%s). Reintento en %.0f s", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay_s)
    finally:
        await engine.dispose()


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine_db = make_engine(settings.database_url)
        self.sm = make_sessionmaker(self.engine_db)
        self.tz = settings.tz
        self.persist = Persistence(self.sm, self.tz)
        self.state = StateEngine()
        self.cfg = WorkerConfig()
        self.detector: Any = None
        self.pipelines: dict[str, Any] = {}
        self.sources: dict[str, Any] = {}
        self._next_due: dict[str, float] = {}
        self._last_results: dict[str, tuple[datetime, Any]] = {}
        self._reload = asyncio.Event()
        self._stop = asyncio.Event()
        self._was_open: bool | None = None
        self._debug_jpeg: dict[str, bytes] = {}
        self._debug_lock = threading.Lock()

    # ------------------------------------------------------------------ config

    async def load(self, first: bool = False) -> None:
        from mesa_monitor.vision.detector import Detector
        from mesa_monitor.vision.pipeline import CameraPipeline
        from mesa_monitor.vision.sources import RtspSource

        cfg = await load_worker_config(self.sm, self.tz, self.settings.config_dir)
        if self.detector is None or self.detector.params != cfg.detector:
            log.info(
                "Cargando RF-DETR %s (mosaicos: %s)…",
                cfg.detector.model,
                cfg.detector.tiling.enabled,
            )
            self.detector = await asyncio.to_thread(Detector, cfg.detector)
            self.pipelines.clear()
        self.persist.tables = cfg.tables
        self.persist.cutoff = cfg.cutoff

        for cid, cam in cfg.cameras.items():
            if cid not in self.sources:
                src = RtspSource(cid, f"{self.settings.go2rtc_rtsp_url}/{cid}")
                src.start()
                self.sources[cid] = src
            zones = cfg.zones[cid]
            if cid in self.pipelines and self.pipelines[cid].tracker is not None:
                self.pipelines[cid].reconfigure(zones, cfg.weights, cfg.profile)
            else:
                self.pipelines[cid] = CameraPipeline(
                    cid,
                    zones,
                    self.detector,
                    fps=cam.fps,
                    tracker=cfg.tracker,
                    weights=cfg.weights,
                    profile=cfg.profile,
                    posture=cfg.posture,
                )
        for cid in list(self.sources):
            if cid not in cfg.cameras:
                self.sources.pop(cid).stop()
                self.pipelines.pop(cid, None)

        if first:
            snaps = await self.persist.load_snapshots(cfg.tables)
            self.state.restore(snaps, cfg.params)
            log.info("Estado reconstruido para %d mesas", len(snaps))
        now = datetime.now(UTC)
        events: list[Event] = []
        for tid in cfg.tables:
            m = self.state.machine(tid, cfg.params.get(tid))
            m.update_params(cfg.params.get(tid, m.params))
            events += m.set_enabled(tid in cfg.enabled_tables, now)
        self.cfg = cfg
        if events:
            await self._save(events, now)
        log.info(
            "Configuración: %d cámaras, %d mesas habilitadas, perfil de personal: %s",
            len(cfg.cameras),
            len(cfg.enabled_tables),
            "sí" if cfg.profile else "no",
        )

    # ------------------------------------------------------------------ ciclo

    async def tick(self) -> None:
        now = datetime.now(UTC)
        open_now = is_open(now, self.cfg.hours, self.tz)
        if self._was_open and not open_now:
            log.info("Fin del horario operativo: se cierran las sesiones abiertas")
            await self._save(self.state.close_all(now, SessionCloseReason.CLOSING_TIME), now)
        self._was_open = open_now
        if not open_now:
            return

        mono = time.monotonic()
        healthy: dict[str, bool] = {}
        for cid, cam in self.cfg.cameras.items():
            frame = self.sources[cid].latest()
            fresh = frame is not None and (now - frame.ts).total_seconds() < FRAME_STALE_S
            healthy[cid] = fresh
            zones = self.cfg.zones.get(cid)
            if zones is None or not zones.views:
                continue  # cámara sin mesas dibujadas: no se gasta GPU en ella
            if not fresh or mono < self._next_due.get(cid, 0):
                continue
            self._next_due[cid] = mono + 1.0 / cam.fps
            pipe = self.pipelines[cid]
            result = await asyncio.to_thread(pipe.process, frame.image, frame.ts)
            self._last_results[cid] = (frame.ts, result)
            if self.cfg.debug_overlay:
                await asyncio.to_thread(self._render_debug, cid)

        from mesa_monitor.vision.scene import CameraTableObs, fuse

        events: list[Event] = []
        for tid in self.cfg.enabled_tables:
            cands = []
            for cid, view in self.cfg.views_of(tid):
                last = self._last_results.get(cid)
                ok = healthy.get(cid, False) and last is not None
                counts = last[1].tables.get(tid) if last else None
                cands.append(CameraTableObs(cid, view.priority, ok, counts))
            if not cands:
                continue  # mesa sin ninguna vista dibujada: no se observa
            events += self.state.observe(fuse(tid, now, cands))
        await self._save(events, now)

    async def _save(self, events: list[Event], now: datetime) -> None:
        snaps = [m.snapshot() for m in self.state.machines.values()]
        await self.persist.apply(events, snaps, now)

    def _render_debug(self, cid: str) -> None:
        from PIL import Image

        from mesa_monitor.vision.pipeline import annotate

        pipe = self.pipelines[cid]
        if pipe.last_frame is None or pipe.last_result is None:
            return
        labels = {tid: t.label for tid, t in self.cfg.tables.items()}
        img = Image.fromarray(annotate(pipe.last_frame, pipe.zones, pipe.last_result, labels))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=70)
        with self._debug_lock:
            self._debug_jpeg[cid] = buf.getvalue()

    async def publish_status(self) -> None:
        cams: dict[str, Any] = {}
        now = datetime.now(UTC)
        for cid, src in self.sources.items():
            frame = src.latest()
            pipe = self.pipelines.get(cid)
            cams[cid] = {
                "fps": pipe.stats.fps if pipe else 0,
                "latency_ms": round(pipe.stats.avg_latency_ms, 1) if pipe else None,
                "people": pipe.stats.last_people if pipe else 0,
                "frame_age_s": round((now - frame.ts).total_seconds(), 1) if frame else None,
                "error": src.last_error,
            }
        status = {
            "ts": now.isoformat(),
            "model": self.cfg.detector.model,
            "tiling": self.cfg.detector.tiling.enabled,
            "open": bool(self._was_open),
            "cameras": cams,
            "gpu": _gpu_status(),
        }
        async with self.sm() as db:
            stmt = insert(AppSetting).values(key="worker_status", value=status)
            await db.execute(
                stmt.on_conflict_do_update(
                    index_elements=["key"], set_={"value": status, "updated_at": now}
                )
            )
            await db.commit()

    async def listen_config(self) -> None:
        """LISTEN config_changed con reconexión."""
        dsn = self.settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
        while not self._stop.is_set():
            try:
                conn = await asyncpg.connect(dsn)
                await conn.add_listener(CHANNEL_CONFIG, lambda *_: self._reload.set())
                log.info("Escuchando cambios de configuración")
                while not self._stop.is_set() and not conn.is_closed():
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=5)
                await conn.close()
            except Exception as exc:
                log.warning("LISTEN config_changed falló (%s); reintento en 5 s", exc)
                await asyncio.sleep(5)

    def serve_debug(self) -> None:
        worker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silencio
                return

            def do_GET(self) -> None:
                cid = self.path.strip("/").removesuffix(".mjpeg").removeprefix("debug/")
                if not worker.cfg.debug_overlay or cid not in worker.cfg.cameras:
                    self.send_error(404, "Overlay de debug desactivado o cámara inexistente")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.end_headers()
                try:
                    while worker.cfg.debug_overlay:
                        with worker._debug_lock:
                            jpg = worker._debug_jpeg.get(cid)
                        if jpg:
                            self.wfile.write(
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
                            )
                        time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError):
                    return

        server = ThreadingHTTPServer(("0.0.0.0", DEBUG_PORT), Handler)  # noqa: S104  (red interna de Docker)
        threading.Thread(target=server.serve_forever, name="debug-mjpeg", daemon=True).start()

    async def run(self) -> None:
        await wait_for_database(self.settings.database_url)
        await self.load(first=True)
        self.serve_debug()
        listener = asyncio.create_task(self.listen_config())
        last_status = 0.0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                if self._reload.is_set():
                    self._reload.clear()
                    await self.load()
                try:
                    await self.tick()
                except Exception:
                    log.exception("Error en el ciclo del worker")
                if time.monotonic() - last_status > STATUS_EVERY_S:
                    last_status = time.monotonic()
                    with contextlib.suppress(Exception):
                        await self.publish_status()
                await asyncio.sleep(max(0.05, 0.2 - (time.monotonic() - started)))
        finally:
            listener.cancel()
            for src in self.sources.values():
                src.stop()
            await self.engine_db.dispose()

    def stop(self) -> None:
        self._stop.set()


def _gpu_status() -> dict[str, Any] | None:
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return {
            "name": torch.cuda.get_device_name(0),
            "memory_used_gb": round((total - free) / 1024**3, 2),
            "memory_total_gb": round(total / 1024**3, 2),
        }
    except Exception:
        return None


async def run() -> None:
    settings = get_settings()
    worker = Worker(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows sin Docker
            loop.add_signal_handler(sig, worker.stop)
    await worker.run()
    log.info("Worker detenido.")


def main() -> None:
    setup_logging(get_settings().log_level)
    asyncio.run(run())
