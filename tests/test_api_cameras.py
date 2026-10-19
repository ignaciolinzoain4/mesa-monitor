from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.api.app import create_app
from mesa_monitor.db.models import Camera
from tests.conftest import ADMIN_PASS, ADMIN_USER, app_client, fake_jpeg, make_settings

pytestmark = pytest.mark.db

CAMERAS_YAML = """
connections:
  nvr: {host: nvr.local, user: u, password: p}
cameras:
  - {id: salon-1, name: Salón, channel: 1, expected_resolution: [704, 480]}
  - {id: caida, name: Caída, channel: 2}
  - {id: apagada, name: Apagada, channel: 3, enabled: false}
"""


def go2rtc_handler(req: httpx.Request) -> httpx.Response:
    src = req.url.params["src"]
    if src.startswith("caida"):
        return httpx.Response(200, content=b"")
    return httpx.Response(200, content=fake_jpeg(1920 if src.endswith("_main") else 704, 480))


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "cameras.yaml").write_text(CAMERAS_YAML)
    return tmp_path


async def _login(client: httpx.AsyncClient, password: str = ADMIN_PASS) -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"username": ADMIN_USER, "password": password}
    )


async def test_login_and_me(clean_db: str, config_dir: Path) -> None:
    app = create_app(
        make_settings(database_url=clean_db, config_dir=config_dir), start_background_tasks=False
    )
    async for client in app_client(app):
        assert (await _login(client, "mala")).status_code == 401
        resp = await _login(client)
        assert resp.status_code == 200
        token = resp.json()["access_token"]
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.json() == {"username": ADMIN_USER, "role": "admin"}
        bad = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer basura"})
        assert bad.status_code == 401


async def test_cameras_require_login(clean_db: str, config_dir: Path) -> None:
    app = create_app(
        make_settings(database_url=clean_db, config_dir=config_dir), start_background_tasks=False
    )
    async for client in app_client(app):
        assert (await client.get("/api/v1/cameras")).status_code == 401
        assert (await client.get("/api/v1/cameras/salon-1/snapshot")).status_code == 401


async def test_cameras_synced_listed_and_snapshot_proxied(clean_db: str, config_dir: Path) -> None:
    app = create_app(
        make_settings(database_url=clean_db, config_dir=config_dir),
        go2rtc_transport=httpx.MockTransport(go2rtc_handler),
        start_background_tasks=False,
    )
    async for client in app_client(app):
        await app.state.camera_monitor.run_once()
        token = (await _login(client)).json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}

        cams = {c["id"]: c for c in (await client.get("/api/v1/cameras", headers=auth)).json()}
        assert set(cams) == {"salon-1", "caida", "apagada"}
        assert cams["salon-1"]["status"] == "online"
        assert cams["salon-1"]["resolution"] == [704, 480]
        assert cams["caida"]["status"] == "offline"
        assert cams["apagada"]["enabled"] is False

        snap = await client.get("/api/v1/cameras/salon-1/snapshot", headers=auth)
        assert snap.status_code == 200
        assert snap.headers["content-type"] == "image/jpeg"
        assert snap.headers["x-frame-size"] == "1920x480"
        assert snap.headers["cache-control"] == "no-store"

        down = await client.get("/api/v1/cameras/caida/snapshot", headers=auth)
        assert down.status_code == 502
        assert "sin señal" in down.json()["detail"]
        off = await client.get("/api/v1/cameras/apagada/snapshot", headers=auth)
        assert off.status_code == 409
        missing = await client.get("/api/v1/cameras/nope/snapshot", headers=auth)
        assert missing.status_code == 404


async def test_camera_removed_from_yaml_is_disabled_not_deleted(
    clean_db: str, config_dir: Path
) -> None:
    settings = make_settings(database_url=clean_db, config_dir=config_dir)
    async for _ in app_client(create_app(settings, start_background_tasks=False)):
        pass
    (config_dir / "cameras.yaml").write_text(
        "connections: {nvr: {host: h}}\ncameras: [{id: salon-1, name: Salón, channel: 1}]\n"
    )
    async for _ in app_client(create_app(settings, start_background_tasks=False)):
        pass
    engine = create_async_engine(clean_db)
    async with engine.connect() as conn:
        rows = {r.id: r.enabled for r in (await conn.execute(select(Camera))).all()}
    await engine.dispose()
    assert rows == {"salon-1": True, "caida": False, "apagada": False}
