"""Webhooks salientes: alta/baja/edición, entregas recientes y envío de prueba (admin)."""

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import AnyHttpUrl, BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import select

from mesa_monitor.api.deps import AdminUser, DbSession
from mesa_monitor.core.enums import DeliveryStatus
from mesa_monitor.core.events import WEBHOOK_EVENT_TYPES
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import record_audit
from mesa_monitor.db.models import Webhook, WebhookDelivery

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    url: AnyHttpUrl
    event_types: list[str] = Field(min_length=1)
    enabled: bool = True

    @field_validator("event_types")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(WEBHOOK_EVENT_TYPES)
        if unknown:
            raise ValueError(f"Tipos de evento desconocidos: {sorted(unknown)}")
        return sorted(set(v))


class WebhookOut(BaseModel):
    id: str
    name: str
    url: str
    event_types: list[str]
    enabled: bool
    created_at: datetime


class WebhookCreated(WebhookOut):
    secret: str = Field(description="Secreto para verificar la firma. Se muestra una sola vez.")


class DeliveryOut(BaseModel):
    id: int
    event_type: str
    status: DeliveryStatus
    attempts: int
    last_status_code: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
    next_attempt_at: datetime | None


def _out(h: Webhook) -> WebhookOut:
    return WebhookOut(
        id=h.id,
        name=h.name,
        url=h.url,
        event_types=h.event_types,
        enabled=h.enabled,
        created_at=h.created_at,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(db: DbSession, _: AdminUser) -> list[WebhookOut]:
    return [_out(h) for h in (await db.execute(select(Webhook).order_by(Webhook.name))).scalars()]


@router.get("/event-types", response_model=list[str])
async def event_types(_: AdminUser) -> list[str]:
    return WEBHOOK_EVENT_TYPES


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(body: WebhookIn, db: DbSession, who: AdminUser) -> WebhookCreated:
    h = Webhook(
        id=new_id("whk"),
        name=body.name,
        url=str(body.url),
        event_types=body.event_types,
        secret=secrets.token_urlsafe(32),
        enabled=body.enabled,
    )
    db.add(h)
    await db.flush()
    await record_audit(
        db, who.actor, "create", "webhook", h.id, after={"url": h.url, "events": h.event_types}
    )
    await db.commit()
    await db.refresh(h)
    return WebhookCreated(**_out(h).model_dump(), secret=h.secret)


@router.put("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: str, body: WebhookIn, db: DbSession, who: AdminUser
) -> WebhookOut:
    h = await db.get(Webhook, webhook_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Webhook inexistente")
    h.name, h.url, h.event_types, h.enabled = (
        body.name,
        str(body.url),
        body.event_types,
        body.enabled,
    )
    await record_audit(db, who.actor, "update", "webhook", h.id, after=body.model_dump(mode="json"))
    await db.commit()
    await db.refresh(h)
    return _out(h)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: str, db: DbSession, who: AdminUser) -> Response:
    h = await db.get(Webhook, webhook_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Webhook inexistente")
    await db.delete(h)
    await record_audit(db, who.actor, "delete", "webhook", webhook_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def deliveries(webhook_id: str, db: DbSession, _: AdminUser) -> list[DeliveryOut]:
    rows = (
        await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.id.desc())
            .limit(100)
        )
    ).scalars()
    return [
        DeliveryOut(
            id=d.id,
            event_type=d.event_type,
            status=d.status,
            attempts=d.attempts,
            last_status_code=d.last_status_code,
            last_error=d.last_error,
            created_at=d.created_at,
            delivered_at=d.delivered_at,
            next_attempt_at=d.next_attempt_at,
        )
        for d in rows
    ]


@router.post("/{webhook_id}/test", response_model=DeliveryOut, status_code=status.HTTP_202_ACCEPTED)
async def send_test_webhook(webhook_id: str, db: DbSession, _: AdminUser) -> DeliveryOut:
    h = await db.get(Webhook, webhook_id)
    if h is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Webhook inexistente")
    now = datetime.now(UTC)
    d = WebhookDelivery(
        webhook_id=h.id,
        event_id=new_id("evt"),
        event_type="heartbeat",
        payload={
            "schema_version": 1,
            "id": new_id("evt"),
            "type": "heartbeat",
            "ts": now.isoformat(),
            "data": {"test": "true"},
        },
        next_attempt_at=now,
        status=DeliveryStatus.PENDING,
        attempts=0,
        created_at=now,
    )
    db.add(d)
    await db.commit()
    return DeliveryOut(
        id=d.id,
        event_type=d.event_type,
        status=d.status,
        attempts=0,
        last_status_code=None,
        last_error=None,
        created_at=now,
        delivered_at=None,
        next_attempt_at=now,
    )
