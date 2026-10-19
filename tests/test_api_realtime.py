"""F5: motor de alertas, WebSocket, webhooks, API keys, dispositivos y umbrales."""

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.api.app import create_app
from mesa_monitor.api.webhooks import verify
from mesa_monitor.core.enums import AlertStatus, DeliveryStatus, Severity
from mesa_monitor.db.models import Alert, AuditLog, WebhookDelivery
from tests.conftest import ADMIN_PASS, ADMIN_USER, make_settings
from tests.helpers import CAMERAS_YAML, api, go2rtc_ok

pytestmark = pytest.mark.db


async def _exec(db_url: str, sql: str, **params: object) -> None:
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.execute(text(sql), params)
    await engine.dispose()


async def _table_waiting(db_url: str, seconds: float, table_id: str = "t1") -> None:
    since = datetime.now(UTC) - timedelta(seconds=seconds)
    await _exec(
        db_url, "INSERT INTO tables (id, label, sector) VALUES (:t, :t, 'salon')", t=table_id
    )
    await _exec(
        db_url,
        "INSERT INTO table_sessions (id, table_id, business_date, seated_at) VALUES (:s, :t, '2026-10-18', :ts)",
        s=f"ses_{table_id}",
        t=table_id,
        ts=since,
    )
    await _exec(
        db_url,
        "INSERT INTO table_state (table_id, state, state_since, session_id, updated_at)"
        " VALUES (:t, 'OCUPADA_SIN_ATENDER', :ts, :s, now())",
        t=table_id,
        ts=since,
        s=f"ses_{table_id}",
    )


async def test_alert_lifecycle_open_escalate_ack_snooze_resolve(
    clean_db: str, tmp_path: Path
) -> None:
    await _table_waiting(clean_db, 200)
    async with api(clean_db, tmp_path) as c:
        engine = c.app.state.alert_engine  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        await engine.tick(now)
        (a,) = (await c.get("/alerts")).json()
        assert (a["alert_type"], a["severity"], a["status"]) == ("SIN_ATENCION", "warning", "open")
        assert a["table"]["label"] == "t1"

        r = await c.post(f"/alerts/{a['alert_id']}/ack")
        assert r.json()["status"] == "acknowledged"
        r = await c.post(f"/alerts/{a['alert_id']}/snooze", json={"minutes": 5})
        assert r.json()["snoozed_until"] is not None

        # pasa el umbral crítico: MISMA alerta, escalada y vuelve a sonar (anula Visto/Posponer)
        await engine.tick(now + timedelta(seconds=200))
        (b,) = (await c.get("/alerts")).json()
        assert b["alert_id"] == a["alert_id"]
        assert (b["severity"], b["status"], b["snoozed_until"]) == ("critical", "open", None)

        # el mozo atiende → se resuelve sola
        await _exec(clean_db, "UPDATE table_state SET state = 'ATENDIDA' WHERE table_id = 't1'")
        await _exec(clean_db, "UPDATE table_sessions SET first_attention_at = now()")
        await engine.tick(now + timedelta(seconds=201))
        assert (await c.get("/alerts")).json() == []
        hist = (await c.get("/alerts?status=all")).json()
        assert hist[0]["status"] == "resolved"

    engine_db = create_async_engine(clean_db)
    async with engine_db.connect() as conn:
        types = (await conn.execute(text("SELECT type FROM events ORDER BY ts"))).scalars().all()
        acks = (
            (await conn.execute(select(AuditLog.action).where(AuditLog.entity_type == "alert")))
            .scalars()
            .all()
        )
        alert = (await conn.execute(select(Alert))).one()
    await engine_db.dispose()
    assert types.count("alert.opened") == 1
    assert types.count("alert.resolved") == 1
    assert types.count("alert.updated") == 3  # ack, snooze, escalamiento
    assert set(acks) == {"ack", "snooze"}
    assert alert.acknowledged_by is None  # el escalamiento lo limpió
    assert Severity(alert.severity) is Severity.CRITICAL
    assert AlertStatus(alert.status) is AlertStatus.RESOLVED


def test_websocket_snapshot_and_alert_within_5_seconds(clean_db: str, tmp_path: Path) -> None:
    """Criterio de aceptación F5: la alerta llega por WS ≤ 5 s después de cruzar el umbral."""
    import asyncio

    created = time.monotonic()
    asyncio.run(_table_waiting(clean_db, 178))  # el umbral de 180 s se cruza 2 s después
    crossed_at = created + 2.0
    (tmp_path / "cameras.yaml").write_text(CAMERAS_YAML)
    app = create_app(
        make_settings(database_url=clean_db, config_dir=tmp_path),
        go2rtc_transport=httpx.MockTransport(go2rtc_ok),
    )
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}
        ).json()["access_token"]
        with client.websocket_connect(f"/api/v1/ws?token={token}") as ws:
            first = ws.receive_json()
            assert first["type"] == "snapshot"
            assert first["schema_version"] == 1
            assert [t["table"]["label"] for t in first["data"]["tables"]] == ["t1"]
            t0 = time.monotonic()
            while True:
                msg = ws.receive_json()
                if msg["type"] == "alert.opened":
                    break
                assert time.monotonic() - t0 < 10, "no llegó la alerta"
            latency = time.monotonic() - crossed_at
            assert latency <= 5, f"la alerta tardó {latency:.1f} s"
            assert msg["data"]["alert_type"] == "SIN_ATENCION"
        with pytest.raises(Exception):  # noqa: B017, PT011
            with client.websocket_connect("/api/v1/ws?token=malo") as bad:
                bad.receive_json()


async def test_webhook_signed_delivery_and_retries(clean_db: str, tmp_path: Path) -> None:
    received: list[httpx.Request] = []
    fail = {"n": 1}

    def receiver(req: httpx.Request) -> httpx.Response:
        received.append(req)
        if fail["n"] > 0:
            fail["n"] -= 1
            return httpx.Response(500)
        return httpx.Response(204)

    async with api(clean_db, tmp_path) as c:
        app = c.app  # type: ignore[attr-defined]
        r = await c.post(
            "/webhooks",
            json={
                "name": "gestión",
                "url": "https://gestion.local/hook",
                "event_types": ["alert.opened"],
            },
        )
        assert r.status_code == 201, r.text
        hook = r.json()
        assert (
            await c.post("/webhooks", json={**hook, "event_types": ["nope"]})
        ).status_code == 422

        from mesa_monitor.api.webhooks import WebhookDispatcher

        dispatcher = WebhookDispatcher(
            app.state.sessionmaker, transport=httpx.MockTransport(receiver)
        )
        await _table_waiting(clean_db, 200)
        await app.state.alert_engine.tick()
        # el hub no corre en este test: se encola la entrega a mano como lo haría el hub
        async with app.state.sessionmaker() as db:
            ev = (
                await db.execute(text("SELECT id FROM events WHERE type = 'alert.opened'"))
            ).scalar_one()
            from mesa_monitor.db.models import EventLog

            await app.state.events_hub._enqueue_webhooks(db, await db.get(EventLog, ev))
        now = datetime.now(UTC)
        await dispatcher.run_once(now)  # 1er intento: 500
        await dispatcher.run_once(now)  # todavía no toca reintentar
        await dispatcher.run_once(now + timedelta(seconds=11))  # reintento: 204
        await dispatcher.stop()

        deliveries = (await c.get(f"/webhooks/{hook['id']}/deliveries")).json()
        assert deliveries[0]["status"] == "success"
        assert deliveries[0]["attempts"] == 2
    assert len(received) == 2
    req = received[-1]
    body = req.content
    assert json.loads(body)["type"] == "alert.opened"
    assert verify(
        hook["secret"], int(req.headers["X-Mesa-Timestamp"]), body, req.headers["X-Mesa-Signature"]
    )
    assert not verify(
        "otro", int(req.headers["X-Mesa-Timestamp"]), body, req.headers["X-Mesa-Signature"]
    )


async def test_api_keys_and_device_tokens(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        key = (await c.post("/api-keys", json={"name": "sistema de gestión"})).json()
        assert key["key"].startswith("mm_")
        listed = (await c.get("/api-keys")).json()
        assert "key" not in listed[0]  # nunca se vuelve a mostrar
        admin_auth = c.headers.pop("Authorization")

        r = await c.get("/state", headers={"X-API-Key": key["key"]})
        assert r.status_code == 200
        assert (
            await c.get("/users", headers={"X-API-Key": key["key"]})
        ).status_code == 403  # viewer

        dev = (
            await c.post(
                "/device-tokens", json={"name": "PC caja"}, headers={"Authorization": admin_auth}
            )
        ).json()
        assert dev["link_path"].startswith("/alertas?token=mmd_")
        dev_auth = {"Authorization": f"Bearer {dev['token']}"}
        assert (await c.get("/alerts", headers=dev_auth)).status_code == 200
        assert (await c.get("/sessions", headers=dev_auth)).status_code == 403  # solo /alertas

        pin = await c.post("/auth/device-pin", json={"pin": dev["pin"]})
        assert pin.status_code == 200
        new_token = pin.json()["token"]
        assert (
            await c.get("/alerts", headers=dev_auth)
        ).status_code == 401  # token viejo invalidado
        assert (
            await c.get("/alerts", headers={"Authorization": f"Bearer {new_token}"})
        ).status_code == 200

        await c.delete(f"/api-keys/{key['id']}", headers={"Authorization": admin_auth})
        assert (await c.get("/state", headers={"X-API-Key": key["key"]})).status_code == 401


async def test_thresholds_validation_and_audit(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        got = (await c.get("/config/thresholds")).json()
        assert any(d["key"] == "alert_sin_atencion_warning_s" for d in got["definitions"])
        ok = await c.put(
            "/config/thresholds",
            json=[
                {"key": "alert_sin_atencion_warning_s", "value": 240},
                {
                    "scope": "sector",
                    "scope_ref": "galeria",
                    "key": "alert_mesa_sucia_s",
                    "value": 600,
                },
            ],
        )
        assert ok.status_code == 200, ok.text
        assert len(ok.json()["items"]) == 2
        bad = await c.put("/config/thresholds", json=[{"key": "inventado", "value": 1}])
        assert bad.status_code == 422
        bad = await c.put(
            "/config/thresholds",
            json=[{"scope": "table", "scope_ref": "tbl_x", "key": "visit_min_s", "value": 3}],
        )
        assert bad.status_code == 422
        sched = await c.put(
            "/config/schedule",
            json={
                "operating_hours": [{"weekday": 4, "opens_at": "08:00", "closes_at": "02:00"}],
                "shifts": [{"name": "noche", "starts_at": "19:00", "ends_at": "02:00"}],
                "business_day_cutoff": "06:00",
            },
        )
        assert sched.status_code == 200, sched.text
        vision = await c.put("/config/vision", json={"model": "xlarge"})
        assert vision.status_code == 422  # PML: prohibido
    engine = create_async_engine(clean_db)
    async with engine.connect() as conn:
        audit = (await conn.execute(select(AuditLog.entity_type))).scalars().all()
    await engine.dispose()
    assert "thresholds" in audit
    assert "schedule" in audit


async def test_login_rate_limited(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        codes = [
            (await c.post("/auth/login", json={"username": "x", "password": "y"})).status_code
            for _ in range(12)
        ]
    assert codes.count(429) >= 1


async def test_sessions_and_state_endpoints(clean_db: str, tmp_path: Path) -> None:
    await _table_waiting(clean_db, 30)
    await _exec(
        clean_db,
        "INSERT INTO staff_visits (id, session_id, table_id, started_at) VALUES ('vis_1', 'ses_t1', 't1', now())",
    )
    async with api(clean_db, tmp_path) as c:
        state = (await c.get("/state")).json()
        (t,) = state["tables"]
        assert t["state"] == "OCUPADA_SIN_ATENDER"
        assert 25 <= t["elapsed_seconds"] <= 60
        sessions = (await c.get("/sessions")).json()
        assert sessions[0]["visits"] == 1
        detail = (await c.get(f"/sessions/{sessions[0]['id']}")).json()
        assert len(detail["visit_list"]) == 1
        assert (await c.get("/tables/t1/sessions")).status_code == 200
        health = (await c.get("/system/health")).json()
        assert health["worker_ok"] is False


async def test_webhook_failed_after_max_attempts(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        app = c.app  # type: ignore[attr-defined]
        hook = (
            await c.post(
                "/webhooks",
                json={"name": "x", "url": "https://x.local/h", "event_types": ["alert.opened"]},
            )
        ).json()
        await c.post(f"/webhooks/{hook['id']}/test")
        from mesa_monitor.api.webhooks import MAX_ATTEMPTS, WebhookDispatcher

        d = WebhookDispatcher(
            app.state.sessionmaker, transport=httpx.MockTransport(lambda r: httpx.Response(503))
        )
        t = datetime.now(UTC)
        for _ in range(MAX_ATTEMPTS):
            await d.run_once(t)
            t += timedelta(hours=2)
        await d.stop()
        async with app.state.sessionmaker() as db:
            row = (await db.execute(select(WebhookDelivery))).scalar_one()
        assert row.status is DeliveryStatus.FAILED
        assert row.attempts == MAX_ATTEMPTS


async def test_alert_screen_settings_follow_threshold(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        assert (await c.get("/alerts/screen-settings")).json() == {"sound_repeat_s": 60.0}
        await c.put("/config/thresholds", json=[{"key": "alert_sound_repeat_s", "value": 20}])
        dev = (await c.post("/device-tokens", json={"name": "PC"})).json()
        c.headers["Authorization"] = f"Bearer {dev['token']}"
        assert (await c.get("/alerts/screen-settings")).json() == {"sound_repeat_s": 20.0}
