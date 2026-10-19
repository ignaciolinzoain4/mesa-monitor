"""Cliente contra un servidor falso (REST simulado + WebSocket real local)."""

import asyncio
import json
import threading
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import websockets

from mesa_monitor_client import (
    ApiKeyInvalida,
    FirmaInvalida,
    MesaMonitorClient,
    PermisoDenegado,
    ServidorNoDisponible,
    parse_webhook,
)

NOW = datetime.now(UTC).isoformat()
TABLE = {"id": "t12", "label": "12", "sector": "salon"}
ALERT = {
    "alert_id": "alr_1",
    "alert_type": "SIN_ATENCION",
    "severity": "warning",
    "status": "open",
    "table": TABLE,
    "camera_id": None,
    "state": "OCUPADA_SIN_ATENDER",
    "state_since": NOW,
    "elapsed_seconds": 184,
    "threshold_seconds": 180,
    "opened_at": NOW,
}
SNAPSHOT = {
    "tables": [{"table": TABLE, "state": "OCUPADA_SIN_ATENDER", "state_since": NOW, "people": 3}],
    "alerts": [],
    "cameras": [{"camera_id": "cam1", "name": "Salón", "status": "online"}],
}


def env(type_: str, data: Any) -> dict[str, Any]:
    return {"schema_version": 1, "id": f"evt_{type_}", "type": type_, "ts": NOW, "data": data}


def handler(req: httpx.Request) -> httpx.Response:
    if req.headers.get("X-API-Key") == "mm_bad":
        return httpx.Response(401, json={"detail": "API key inválida"})
    p = req.url.path
    if p == "/api/v1/state":
        return httpx.Response(200, json=SNAPSHOT)
    if p == "/api/v1/alerts":
        assert req.url.params["status"] == "active"
        return httpx.Response(200, json=[ALERT])
    if p == "/api/v1/alerts/alr_1/ack":
        return httpx.Response(200, json={**ALERT, "status": "acknowledged", "acknowledged_at": NOW})
    if p == "/api/v1/metrics/summary":
        assert req.url.params["group_by"] == "hour"
        stat = {"n": 1, "avg": 120.0, "median": 120.0, "p90": 120.0}
        group = {
            "key": "20",
            "label": "20:00",
            "n_sessions": 1,
            "n_in_progress": 0,
            "n_excluded": 0,
            "n_unattended": 0,
            "sla_pct": 100.0,
            "visits_avg": 1.0,
            "rotation": 0.5,
            "occupancy_pct": 40.0,
            "t_esp": stat,
            "t_est": stat,
            "t_lim": stat,
            "g_max": stat,
        }
        return httpx.Response(
            200,
            json={
                "group_by": "hour",
                "sla_threshold_seconds": 180,
                "operating_hours": 2,
                "n_tables": 1,
                "groups": [group],
            },
        )
    if p == "/api/v1/users":
        return httpx.Response(403, json={"detail": "Requiere permisos de administrador"})
    return httpx.Response(404)


def make(key: str = "mm_ok") -> MesaMonitorClient:
    return MesaMonitorClient("http://srv.local:8000", key, transport=httpx.MockTransport(handler))


def test_rest_calls_return_typed_models() -> None:
    c = make()
    state = c.get_state()
    assert state.tables[0].table.label == "12"
    (alert,) = c.get_active_alerts()
    assert alert.where == "Mesa 12"
    assert c.ack_alert("alr_1").status.value == "acknowledged"
    m = c.get_metrics(group_by="hour")
    assert m.groups[0].t_esp.avg == 120.0


def test_errors_are_explained_in_spanish() -> None:
    with pytest.raises(ApiKeyInvalida, match="Configuración → Accesos"):
        make("mm_bad").get_active_alerts()
    with pytest.raises(PermisoDenegado):
        make()._request("GET", "/users")
    dead = MesaMonitorClient("http://127.0.0.1:1", "mm_ok", timeout=1)
    with pytest.raises(ServidorNoDisponible, match="esté prendida"):
        dead.get_state()


def test_on_rejects_unknown_event_types() -> None:
    with pytest.raises(Exception, match="desconocido"):
        make().on("alerta.nueva")


def test_realtime_events_reconnection_and_live_cache() -> None:
    """Servidor WS falso: manda snapshot + alerta, se cae, vuelve y manda la resolución."""
    rounds: list[int] = []

    async def serve(ws: Any) -> None:
        assert "token=mm_ok" in ws.request.path
        rounds.append(1)
        await ws.send(json.dumps(env("snapshot", SNAPSHOT)))
        if len(rounds) == 1:
            await ws.send(json.dumps(env("alert.opened", ALERT)))
            await ws.send(json.dumps({"type": "algo.nuevo", "data": {}}))  # desconocido: se ignora
            await asyncio.sleep(0.2)
            return  # se corta la conexión
        await ws.send(
            json.dumps(env("alert.resolved", {**ALERT, "status": "resolved", "resolved_at": NOW}))
        )
        await asyncio.sleep(5)

    ready = threading.Event()
    port: list[int] = []
    loop = asyncio.new_event_loop()

    def server_thread() -> None:
        async def main() -> None:
            async with websockets.serve(serve, "127.0.0.1", 0) as srv:
                port.append(srv.sockets[0].getsockname()[1])
                ready.set()
                await asyncio.sleep(10)

        loop.run_until_complete(main())

    threading.Thread(target=server_thread, daemon=True).start()
    ready.wait(5)
    client = MesaMonitorClient(f"http://127.0.0.1:{port[0]}", "mm_ok", max_backoff=1)
    opened: list[Any] = []
    resolved: list[Any] = []
    everything: list[str] = []
    client.on("alert.opened")(opened.append)

    @client.on("alert.resolved")
    async def on_resolved(ev: Any) -> None:  # también se aceptan funciones async
        resolved.append(ev)

    client.on("*")(lambda ev: everything.append(ev.type))
    client.start_in_background()
    deadline = time.time() + 8
    while time.time() < deadline and not resolved:
        if opened and not client.active_alerts_now() and len(rounds) < 2:
            pass
        time.sleep(0.05)
    client.stop()
    assert opened[0].data.where == "Mesa 12"
    assert resolved, "no llegó la resolución tras reconectar"
    assert len(rounds) >= 2  # reconectó solo
    assert everything.count("snapshot") >= 2
    assert client.active_alerts_now() == []  # el cache local siguió los eventos
    assert client.tables["t12"].people == 3


def test_webhook_parsing_and_signature() -> None:
    import hashlib
    import hmac

    body = json.dumps(env("alert.opened", ALERT)).encode()
    ts = int(time.time())
    sig = "sha256=" + hmac.new(b"secreto", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    ev = parse_webhook({"X-Mesa-Timestamp": str(ts), "X-Mesa-Signature": sig}, body, "secreto")
    assert ev.type == "alert.opened"
    with pytest.raises(FirmaInvalida):
        parse_webhook({"X-Mesa-Timestamp": str(ts), "X-Mesa-Signature": sig}, body, "otro")
    with pytest.raises(FirmaInvalida):
        parse_webhook({}, body, "secreto")
