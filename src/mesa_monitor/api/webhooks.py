"""Webhooks salientes (SPEC §12.3): firma HMAC-SHA256, reintentos con backoff exponencial y
registro de entregas fallidas.

Cada entrega lleva:
- `X-Mesa-Event`: tipo de evento.
- `X-Mesa-Delivery`: id de la entrega (para detectar duplicados).
- `X-Mesa-Timestamp`: segundos epoch.
- `X-Mesa-Signature`: `sha256=<hex>` = HMAC-SHA256(secreto, f"{timestamp}.{body}").
"""

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.enums import DeliveryStatus
from mesa_monitor.db.models import Webhook, WebhookDelivery

log = logging.getLogger(__name__)
BACKOFF_S = [10, 30, 120, 600, 1800, 3600]  # reintentos; después queda "failed"
MAX_ATTEMPTS = len(BACKOFF_S) + 1


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(
    secret: str, timestamp: int, body: bytes, signature: str, tolerance_s: int = 300
) -> bool:
    """Para el receptor (lo usa el cliente Python y la guía de integración)."""
    if abs(time.time() - timestamp) > tolerance_s:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature)


class WebhookDispatcher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        transport: httpx.AsyncBaseTransport | None = None,
        poll_s: float = 2.0,
    ) -> None:
        self._sm = sessionmaker
        self._http = httpx.AsyncClient(transport=transport, timeout=10.0, follow_redirects=False)
        self.poll_s = poll_s
        self._task: asyncio.Task[None] | None = None

    async def run_once(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        sent = 0
        async with self._sm() as db:
            due = (
                await db.execute(
                    select(WebhookDelivery, Webhook)
                    .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
                    .where(
                        WebhookDelivery.status == DeliveryStatus.PENDING,
                        WebhookDelivery.next_attempt_at <= now,
                    )
                    .order_by(WebhookDelivery.id)
                    .limit(50)
                    .with_for_update(skip_locked=True, of=WebhookDelivery)
                )
            ).all()
            for delivery, hook in due:
                await self._attempt(delivery, hook, now)
                sent += 1
            await db.commit()
        return sent

    async def _attempt(self, d: WebhookDelivery, hook: Webhook, now: datetime) -> None:
        body = json.dumps(d.payload, separators=(",", ":"), ensure_ascii=False).encode()
        ts = int(now.timestamp())
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "MesaMonitor-Webhook/1",
            "X-Mesa-Event": d.event_type,
            "X-Mesa-Delivery": str(d.id),
            "X-Mesa-Timestamp": str(ts),
            "X-Mesa-Signature": sign(hook.secret, ts, body),
        }
        d.attempts += 1
        try:
            resp = await self._http.post(hook.url, content=body, headers=headers)
            d.last_status_code = resp.status_code
            ok = 200 <= resp.status_code < 300
            d.last_error = None if ok else f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            ok = False
            d.last_error = f"{exc.__class__.__name__}: {exc}"[:500]
        if ok:
            d.status = DeliveryStatus.SUCCESS
            d.delivered_at = now
            d.next_attempt_at = None
        elif d.attempts >= MAX_ATTEMPTS:
            d.status = DeliveryStatus.FAILED
            d.next_attempt_at = None
            log.warning(
                "Webhook %s: entrega %s falló definitivamente (%s)", hook.name, d.id, d.last_error
            )
        else:
            d.next_attempt_at = now + timedelta(seconds=BACKOFF_S[d.attempts - 1])

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("Error enviando webhooks")
            await asyncio.sleep(self.poll_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="webhooks")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._http.aclose()
