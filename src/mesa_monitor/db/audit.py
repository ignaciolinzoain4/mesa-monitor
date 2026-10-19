"""Auditoría (quién cambió qué y cuándo) y notificaciones Postgres (`LISTEN/NOTIFY`)."""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.core.enums import ActorType
from mesa_monitor.db.models import AuditLog

# Canales de NOTIFY. Los payloads son chicos (Postgres limita a 8000 bytes).
CHANNEL_CONFIG = "config_changed"  # {"kind": "zones"|"tables"|"thresholds"|..., ...}
CHANNEL_EVENTS = "mesa_events"  # {"event_id": "..."}  (ver api.events)


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None

    @property
    def label(self) -> str:
        return f"{self.type.value}:{self.id}" if self.id else self.type.value


SYSTEM = Actor(ActorType.SYSTEM, None)


async def record_audit(
    session: AsyncSession,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_type=actor.type,
            actor_id=actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before=_jsonable(before),
            after=_jsonable(after),
        )
    )


async def notify(session: AsyncSession, channel: str, payload: dict[str, Any]) -> None:
    """Se entrega a los que hacen LISTEN cuando la transacción hace commit."""
    data = json.dumps(payload, default=str, separators=(",", ":"))
    if len(data.encode()) > 7900:
        raise ValueError("payload de NOTIFY demasiado grande")
    await session.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": data})


def _jsonable(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    result: dict[str, Any] = json.loads(json.dumps(value, default=str))
    return result
