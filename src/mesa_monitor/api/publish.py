"""Publicación de eventos: bandeja `events` + NOTIFY (los difunde `events_hub`)."""

from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_EVENTS, notify
from mesa_monitor.db.models import EventLog


async def publish(
    db: AsyncSession, event_type: str, data: BaseModel, table_id: str | None = None
) -> dict[str, object]:
    """Guarda el evento (sin commit) y avisa. Devuelve el sobre completo."""
    envelope: dict[str, object] = {
        "schema_version": 1,
        "id": new_id("evt"),
        "type": event_type,
        "ts": datetime.now(UTC).isoformat(),
        "data": data.model_dump(mode="json"),
    }
    db.add(
        EventLog(
            id=str(envelope["id"]),
            type=event_type,
            ts=datetime.fromisoformat(str(envelope["ts"])),
            table_id=table_id,
            payload=envelope,
        )
    )
    await notify(db, CHANNEL_EVENTS, {"id": envelope["id"], "type": event_type})
    return envelope
