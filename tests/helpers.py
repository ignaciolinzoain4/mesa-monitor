"""Helpers para tests de API con base de datos."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from mesa_monitor.api.app import create_app
from tests.conftest import ADMIN_PASS, ADMIN_USER, fake_jpeg, make_settings

CAMERAS_YAML = """
connections:
  nvr: {host: nvr.local, user: u, password: p}
cameras:
  - {id: cam1, name: Salón, channel: 1}
  - {id: cam2, name: Galería, channel: 2}
"""


def go2rtc_ok(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=fake_jpeg())


@asynccontextmanager
async def api(
    database_url: str, config_dir: Path, cameras_yaml: str = CAMERAS_YAML, **settings: object
) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente logueado como admin contra una app real (sin tareas de fondo)."""
    (config_dir / "cameras.yaml").write_text(cameras_yaml)
    app = create_app(
        make_settings(database_url=database_url, config_dir=config_dir, **settings),
        go2rtc_transport=httpx.MockTransport(go2rtc_ok),
        start_background_tasks=False,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as client:
            resp = await client.post(
                "/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}
            )
            client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
            client.app = app  # type: ignore[attr-defined]
            yield client
