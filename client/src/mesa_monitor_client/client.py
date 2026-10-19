"""Cliente de Mesa Monitor: REST (sync y async) + eventos en tiempo real por WebSocket."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import websockets
from pydantic import TypeAdapter

from mesa_monitor_client.errors import (
    ApiKeyInvalida,
    ErrorDelServidor,
    MesaMonitorError,
    PermisoDenegado,
    ServidorNoDisponible,
)
from mesa_monitor_client.models import (
    EVENT_TYPES,
    AlertData,
    CameraStatusData,
    Event,
    MetricsSummary,
    SnapshotData,
    TableStateData,
)

log = logging.getLogger("mesa_monitor_client")
_event_adapter: TypeAdapter[Any] = TypeAdapter(Event)
Handler = Callable[[Any], Any]


def _iso(v: datetime | str | None) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else v


class MesaMonitorClient:
    """Conexión a un servidor Mesa Monitor.

    Ejemplo:
        client = MesaMonitorClient(base_url="http://192.168.1.50:8000", api_key="mm_...")
        estado = client.get_state()
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        max_backoff: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url or not api_key:
            raise MesaMonitorError("Faltan base_url y/o api_key (guardalos en el archivo .env)")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_backoff = max_backoff
        self._headers = {"X-API-Key": api_key, "User-Agent": "mesa-monitor-client/0.1"}
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers=self._headers,
            timeout=timeout,
            transport=transport,
        )
        self._async_transport = async_transport
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.connected = False
        # Estado vivo (se actualiza solo con los eventos): útil para apps sin callbacks.
        self.tables: dict[str, TableStateData] = {}
        self.alerts: dict[str, AlertData] = {}
        self.cameras: dict[str, CameraStatusData] = {}

    # ------------------------------------------------------------------ REST

    def _raise(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except (ValueError, AttributeError):
            detail = resp.text[:200]
        if resp.status_code == 401:
            raise ApiKeyInvalida()
        if resp.status_code == 403:
            raise PermisoDenegado(detail)
        raise ErrorDelServidor(resp.status_code, detail)

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ServidorNoDisponible(self.base_url, exc.__class__.__name__) from exc
        self._raise(resp)
        return resp.json() if resp.content else None

    async def _arequest(self, method: str, path: str, **kw: Any) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=f"{self.base_url}/api/v1",
                headers=self._headers,
                timeout=self.timeout,
                transport=self._async_transport,
            ) as http:
                resp = await http.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise ServidorNoDisponible(self.base_url, exc.__class__.__name__) from exc
        self._raise(resp)
        return resp.json() if resp.content else None

    def health(self) -> dict[str, Any]:
        data: dict[str, Any] = self._request("GET", "/health")
        return data

    def get_state(self) -> SnapshotData:
        """Todas las mesas (estado y tiempos), alertas activas y cámaras."""
        return SnapshotData.model_validate(self._request("GET", "/state"))

    async def aget_state(self) -> SnapshotData:
        return SnapshotData.model_validate(await self._arequest("GET", "/state"))

    def get_tables(self) -> list[dict[str, Any]]:
        data: list[dict[str, Any]] = self._request("GET", "/tables")
        return data

    def get_active_alerts(self) -> list[AlertData]:
        return [
            AlertData.model_validate(a)
            for a in self._request("GET", "/alerts", params={"status": "active"})
        ]

    async def aget_active_alerts(self) -> list[AlertData]:
        rows = await self._arequest("GET", "/alerts", params={"status": "active"})
        return [AlertData.model_validate(a) for a in rows]

    def get_alerts(
        self, *, from_: datetime | str | None = None, to: datetime | str | None = None
    ) -> list[AlertData]:
        params = {"status": "all", "from": _iso(from_), "to": _iso(to)}
        rows = self._request("GET", "/alerts", params={k: v for k, v in params.items() if v})
        return [AlertData.model_validate(a) for a in rows]

    def ack_alert(self, alert_id: str) -> AlertData:
        """Marca la alerta como "vista" (deja de sonar en las pantallas)."""
        return AlertData.model_validate(self._request("POST", f"/alerts/{alert_id}/ack"))

    async def aack_alert(self, alert_id: str) -> AlertData:
        return AlertData.model_validate(await self._arequest("POST", f"/alerts/{alert_id}/ack"))

    def snooze_alert(self, alert_id: str, minutes: int = 5) -> AlertData:
        return AlertData.model_validate(
            self._request("POST", f"/alerts/{alert_id}/snooze", json={"minutes": minutes})
        )

    def get_sessions(
        self,
        *,
        from_: datetime | str | None = None,
        to: datetime | str | None = None,
        sector: str | None = None,
        table: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"from": _iso(from_), "to": _iso(to), "sector": sector, "table": table}
        data: list[dict[str, Any]] = self._request(
            "GET", "/sessions", params={k: v for k, v in params.items() if v}
        )
        return data

    def get_metrics(
        self,
        *,
        from_: datetime | str | None = None,
        to: datetime | str | None = None,
        group_by: str | None = None,
        sector: str | None = None,
        table: str | None = None,
        shift: str | None = None,
    ) -> MetricsSummary:
        """Métricas de atención. `group_by`: hour, day, weekday, shift, table o sector.

        Los tiempos (t_esp, t_est, t_lim, g_max) vienen en SEGUNDOS: dividir por 60 para minutos.
        """
        params = {
            "from": _iso(from_),
            "to": _iso(to),
            "group_by": group_by,
            "sector": sector,
            "table": table,
            "shift": shift,
        }
        return MetricsSummary.model_validate(
            self._request("GET", "/metrics/summary", params={k: v for k, v in params.items() if v})
        )

    async def aget_metrics(self, **kw: Any) -> MetricsSummary:
        params = {k: _iso(v) if k in ("from_", "to") else v for k, v in kw.items() if v}
        if "from_" in params:
            params["from"] = params.pop("from_")
        return MetricsSummary.model_validate(
            await self._arequest("GET", "/metrics/summary", params=params)
        )

    def close(self) -> None:
        self.stop()
        self._http.close()

    def __enter__(self) -> MesaMonitorClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ tiempo real

    def on(self, event_type: str) -> Callable[[Handler], Handler]:
        """Registra una función para un tipo de evento ("alert.opened", ...) o "*" para todos.

        La función recibe el evento (con `.type`, `.ts` y `.data`). Puede ser normal o async.
        """
        if event_type != "*" and event_type not in EVENT_TYPES:
            raise MesaMonitorError(
                f"Tipo de evento desconocido: {event_type}. Válidos: {EVENT_TYPES}"
            )

        def deco(fn: Handler) -> Handler:
            self._handlers[event_type].append(fn)
            return fn

        return deco

    def active_alerts_now(self) -> list[AlertData]:
        """Alertas activas según los eventos recibidos (sin pedirle nada al servidor)."""
        rank = {"critical": 2, "warning": 1, "info": 0}
        with self._lock:
            alerts = list(self.alerts.values())
        return sorted(alerts, key=lambda a: (-rank[a.severity.value], a.opened_at))

    def _apply(self, ev: Any) -> None:
        with self._lock:
            if ev.type == "snapshot":
                self.tables = {t.table.id: t for t in ev.data.tables}
                self.alerts = {a.alert_id: a for a in ev.data.alerts}
                self.cameras = {c.camera_id: c for c in ev.data.cameras}
            elif ev.type == "table.state_changed":
                self.tables[ev.data.table.id] = ev.data
            elif ev.type in ("alert.opened", "alert.updated"):
                self.alerts[ev.data.alert_id] = ev.data
            elif ev.type == "alert.resolved":
                self.alerts.pop(ev.data.alert_id, None)
            elif ev.type == "camera.status":
                self.cameras[ev.data.camera_id] = ev.data

    async def _dispatch(self, ev: Any) -> None:
        for fn in [*self._handlers.get(ev.type, []), *self._handlers.get("*", [])]:
            try:
                result = fn(ev)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                log.exception("Error en la función registrada para %s", ev.type)

    def _ws_url(self) -> str:
        url = self.base_url.replace("https://", "wss://").replace("http://", "ws://")
        return f"{url}/api/v1/ws?token={self.api_key}"

    async def run_forever(self) -> None:
        """Escucha eventos para siempre, reconectando solo (para apps async)."""
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._ws_url(), open_timeout=self.timeout, ping_interval=20
                ) as ws:
                    self.connected = True
                    backoff = 1.0
                    log.info("Conectado a Mesa Monitor (%s)", self.base_url)
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=45)
                        except TimeoutError:
                            log.warning("Sin noticias del servidor en 45 s: reconectando")
                            break
                        try:
                            ev = _event_adapter.validate_python(json.loads(raw))
                        except Exception:
                            log.debug("Evento ignorado (formato desconocido): %.200s", raw)
                            continue
                        self._apply(ev)
                        await self._dispatch(ev)
            except websockets.InvalidStatus as exc:
                code = getattr(getattr(exc, "response", None), "status_code", 0)
                if code in (401, 403):
                    log.error(str(ApiKeyInvalida()))
            except (OSError, websockets.WebSocketException, TimeoutError) as exc:
                if self.connected:
                    log.warning(
                        "Se cortó la conexión con Mesa Monitor (%s)", exc.__class__.__name__
                    )
                else:
                    log.warning(str(ServidorNoDisponible(self.base_url, exc.__class__.__name__)))
            self.connected = False
            if self._stop.wait(backoff):
                break
            log.info("Reintentando en %.0f s…", backoff)
            backoff = min(backoff * 2, self.max_backoff)
        self.connected = False

    def start_in_background(self) -> threading.Thread:
        """Escucha eventos en un hilo aparte (para apps sync: Flask, Django, Tkinter, Streamlit,
        scripts). Las funciones registradas con `on()` se llaman desde ese hilo."""
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(
            target=lambda: asyncio.run(self.run_forever()), name="mesa-monitor-ws", daemon=True
        )
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
