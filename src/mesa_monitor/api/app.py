"""Fábrica de la aplicación FastAPI.

Se corre con: `uvicorn --factory mesa_monitor.api.app:create_app`
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from mesa_monitor import __version__
from mesa_monitor.api.alert_engine import AlertEngine
from mesa_monitor.api.camera_monitor import CameraMonitor
from mesa_monitor.api.events_hub import EventsHub
from mesa_monitor.api.ratelimit import RateLimitMiddleware
from mesa_monitor.api.retention import RetentionTask
from mesa_monitor.api.routes import (
    access,
    alerts,
    auth,
    cameras,
    config_admin,
    config_io,
    health,
    metrics,
    staff_profiles,
    state,
    system,
    tables,
    webhooks,
    ws,
    zones,
)
from mesa_monitor.api.security_headers import SecurityHeadersMiddleware
from mesa_monitor.api.snapshots import SnapshotCache
from mesa_monitor.api.webhooks import WebhookDispatcher
from mesa_monitor.config import Settings, get_settings
from mesa_monitor.config.cameras import CamerasConfigError, CamerasFile, load_cameras
from mesa_monitor.db.bootstrap import ensure_admin, sync_cameras
from mesa_monitor.db.session import make_engine, make_sessionmaker
from mesa_monitor.log import setup_logging
from mesa_monitor.security import check_jwt_secret
from mesa_monitor.vision.go2rtc import Go2rtcClient

API_PREFIX = "/api/v1"
log = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Sirve el dashboard y devuelve `index.html` para rutas del SPA (ej. /alertas)."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def _load_cameras(settings: Settings) -> CamerasFile | None:
    path = Path(settings.config_dir) / "cameras.yaml"
    if not path.is_file():
        log.warning("No hay %s: el sistema arranca sin cámaras.", path)
        return None
    try:
        return load_cameras(path)
    except CamerasConfigError as exc:
        log.error("%s", exc)
        return None


def create_app(
    settings: Settings | None = None,
    *,
    go2rtc_transport: httpx.AsyncBaseTransport | None = None,
    webhook_transport: httpx.AsyncBaseTransport | None = None,
    start_background_tasks: bool = True,
    rate_limit: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    check_jwt_secret(settings.jwt_secret.get_secret_value())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        app.state.engine = engine
        app.state.sessionmaker = sessionmaker
        app.state.go2rtc = Go2rtcClient(settings.go2rtc_url, transport=go2rtc_transport)
        monitor = CameraMonitor(
            app.state.go2rtc,
            sessionmaker,
            probe_interval_s=settings.camera_probe_interval_s,
            offline_after_s=settings.camera_offline_after_s,
        )
        app.state.camera_monitor = monitor
        alert_engine = AlertEngine(sessionmaker, monitor, settings.tz)
        app.state.alert_engine = alert_engine
        hub = EventsHub(settings.database_url, sessionmaker)
        hub.on_config_changed.append(lambda data: alert_engine.invalidate())
        app.state.events_hub = hub
        dispatcher = WebhookDispatcher(sessionmaker, transport=webhook_transport)
        app.state.webhooks = dispatcher
        retention = RetentionTask(sessionmaker)
        try:
            async with sessionmaker() as session:
                await ensure_admin(
                    session, settings.admin_username, settings.admin_password.get_secret_value()
                )
                cams = _load_cameras(settings)
                if cams is not None:
                    await sync_cameras(session, cams)
                    monitor.set_cameras(
                        {c.id: c.expected_resolution for c in cams.cameras if c.enabled}
                    )
                    monitor.names = {c.id: c.name for c in cams.cameras}
        except Exception:
            # Sin base la API igual levanta: /health informa el problema.
            log.exception("No se pudo inicializar la base")
        if start_background_tasks:
            monitor.start()
            hub.start()
            alert_engine.start()
            dispatcher.start()
            retention.start()
        try:
            yield
        finally:
            await retention.stop()
            await alert_engine.stop()
            await dispatcher.stop()
            await hub.stop()
            await monitor.stop()
            await app.state.go2rtc.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Mesa Monitor API",
        version=__version__,
        lifespan=lifespan,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.snapshots = SnapshotCache()

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.add_middleware(SecurityHeadersMiddleware)
    if rate_limit:
        app.add_middleware(RateLimitMiddleware)

    for module in (
        health,
        auth,
        access,
        state,
        alerts,
        metrics,
        cameras,
        tables,
        zones,
        config_io,
        config_admin,
        staff_profiles,
        webhooks,
        system,
        ws,
    ):
        app.include_router(module.router, prefix=API_PREFIX)

    dist = Path(settings.dashboard_dist)
    if (dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=dist, html=True), name="dashboard")

    return app
