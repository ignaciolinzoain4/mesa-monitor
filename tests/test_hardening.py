from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.api.retention import run_retention
from mesa_monitor.db.session import make_sessionmaker
from tests.helpers import api


async def test_security_headers(tmp_path: Path) -> None:
    from mesa_monitor.api.app import create_app
    from tests.conftest import app_client, make_settings

    app = create_app(
        make_settings(database_url="postgresql+asyncpg://x@127.0.0.1:1/x"),
        start_background_tasks=False,
    )
    async for c in app_client(app):
        r = await c.get("/api/v1/health")
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-frame-options"] == "DENY"


@pytest.mark.db
async def test_retention_deletes_only_old_rows(clean_db: str, tmp_path: Path) -> None:
    engine = create_async_engine(clean_db)
    old = datetime.now(UTC) - timedelta(days=400)
    async with engine.begin() as c:
        await c.execute(text("INSERT INTO tables (id, label, sector) VALUES ('t', 't', 's')"))
        for ts in (old, datetime.now(UTC)):
            await c.execute(
                text(
                    "INSERT INTO state_transitions (table_id, ts, to_state, reason) VALUES ('t', :ts, 'LIBRE', 'x')"
                ),
                {"ts": ts},
            )
            await c.execute(
                text(
                    "INSERT INTO events (id, type, ts, payload) VALUES (:id, 'heartbeat', :ts, '{}')"
                ),
                {"id": f"e{ts.timestamp()}", "ts": ts},
            )
        await c.execute(
            text(
                "INSERT INTO table_sessions (id, table_id, business_date, seated_at) VALUES ('s', 't', '2025-01-01', :ts)"
            ),
            {"ts": old},
        )
    deleted = await run_retention(make_sessionmaker(engine))
    assert deleted["state_transitions"] == 1
    assert deleted["events"] == 1
    async with engine.connect() as c:
        assert (
            await c.execute(text("SELECT count(*) FROM table_sessions"))
        ).scalar_one() == 1  # nunca se borran
    await engine.dispose()
    async with api(clean_db, tmp_path):
        pass


def test_crop_collector_saves_rate_limited_and_cleans(tmp_path: Path) -> None:
    import json as _json
    import os

    import numpy as np

    from mesa_monitor.vision.crops import CropCollector
    from mesa_monitor.vision.scene import Box, PersonResult, SceneResult

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    res = SceneResult(
        people=[PersonResult(Box(0.1, 0.1, 0.3, 0.6, track_id=7), True, None, True)], tables={}
    )
    c = CropCollector(tmp_path, retention_days=7, every_s=10)
    now = datetime.now(UTC)
    assert c.collect("cam1", frame, res, now) == 1
    assert c.collect("cam1", frame, res, now) == 0  # máximo uno cada 10 s por persona
    (meta,) = list(tmp_path.rglob("*.json"))
    data = _json.loads(meta.read_text())
    assert data["auto_label"] == "staff"
    assert "track" not in str(data)  # sin ids de seguimiento persistidos
    for f in tmp_path.rglob("crop_*"):
        os.utime(f, (0, 0))  # simula archivos viejos
    assert c.cleanup() == 2
