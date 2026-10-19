"""Difusión de eventos: `LISTEN mesa_events` → clientes WebSocket + entregas de webhooks.

El worker y la API escriben cada evento en la tabla `events` y hacen NOTIFY con su id. Este hub
(uno por proceso de API) los lee y los reparte. Si la conexión LISTEN se cae, reconecta y
reenvía lo que se haya perdido mientras tanto (por fecha).
Supuesto: la API corre en un solo proceso (uvicorn sin --workers), como en el compose.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_CONFIG, CHANNEL_EVENTS
from mesa_monitor.db.models import EventLog, Webhook, WebhookDelivery

log = logging.getLogger(__name__)
HEARTBEAT_S = 15.0


class EventsHub:
    def __init__(self, database_url: str, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._dsn = database_url.replace("postgresql+asyncpg://", "postgresql://")
        self._sm = sessionmaker
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._tasks: list[asyncio.Task[None]] = []
        self._pending: asyncio.Queue[str] = asyncio.Queue()
        self._last_seen: datetime | None = None
        self.on_config_changed: list[Callable[[dict[str, Any]], None]] = []
        self.connected = asyncio.Event()

    # ------------------------------------------------------------------ suscriptores

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def broadcast(self, envelope: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(envelope)
            except asyncio.QueueFull:
                # Cliente demasiado lento: se lo desconecta (reconecta y recibe un snapshot).
                self._subscribers.discard(q)

    def is_subscribed(self, q: asyncio.Queue[dict[str, Any]]) -> bool:
        return q in self._subscribers

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    # ------------------------------------------------------------------ ciclo

    async def _listen(self) -> None:
        while True:
            try:
                conn = await asyncpg.connect(self._dsn)
                await conn.add_listener(CHANNEL_EVENTS, self._on_event)
                await conn.add_listener(CHANNEL_CONFIG, self._on_config)
                self.connected.set()
                log.info("Escuchando eventos (%s, %s)", CHANNEL_EVENTS, CHANNEL_CONFIG)
                await self._catch_up()
                # asyncpg no avisa el corte de la conexión: se sondea.
                while not conn.is_closed():  # noqa: ASYNC110
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("LISTEN falló (%s); reintento en 3 s", exc)
            self.connected.clear()
            await asyncio.sleep(3)

    def _on_event(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        with contextlib.suppress(ValueError, KeyError):
            self._pending.put_nowait(json.loads(payload)["id"])

    def _on_config(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
        for cb in self.on_config_changed:
            cb(data)

    async def _catch_up(self) -> None:
        """Eventos ocurridos mientras no había LISTEN (máx. 10 min)."""
        if self._last_seen is None:
            return
        since = max(self._last_seen, datetime.now(UTC) - timedelta(minutes=10))
        async with self._sm() as db:
            ids = (
                (
                    await db.execute(
                        select(EventLog.id).where(EventLog.ts > since).order_by(EventLog.ts)
                    )
                )
                .scalars()
                .all()
            )
        for i in ids:
            self._pending.put_nowait(i)

    async def _dispatch(self) -> None:
        while True:
            event_id = await self._pending.get()
            try:
                async with self._sm() as db:
                    ev = await db.get(EventLog, event_id)
                    if ev is None:
                        continue
                    self._last_seen = ev.ts
                    self.broadcast(ev.payload)
                    await self._enqueue_webhooks(db, ev)
            except Exception:
                log.exception("Error difundiendo el evento %s", event_id)

    async def _enqueue_webhooks(self, db: AsyncSession, ev: EventLog) -> None:
        hooks = (await db.execute(select(Webhook).where(Webhook.enabled))).scalars().all()
        now = datetime.now(UTC)
        added = False
        for h in hooks:
            if ev.type in h.event_types:
                db.add(
                    WebhookDelivery(
                        webhook_id=h.id,
                        event_id=ev.id,
                        event_type=ev.type,
                        payload=ev.payload,
                        next_attempt_at=now,
                    )
                )
                added = True
        if added:
            await db.commit()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            self.broadcast(
                {
                    "schema_version": 1,
                    "id": new_id("evt"),
                    "type": "heartbeat",
                    "ts": datetime.now(UTC).isoformat(),
                    "data": {},
                }
            )

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._listen(), name="events-listen"),
                asyncio.create_task(self._dispatch(), name="events-dispatch"),
                asyncio.create_task(self._heartbeat(), name="events-heartbeat"),
            ]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._tasks = []
