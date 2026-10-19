"""Contrato de eventos (SPEC §12.2): WebSocket, webhooks y cliente Python usan estos modelos.

Todos los mensajes llevan `schema_version`, `id`, `type`, `ts` (ISO-8601 UTC) y `data`.
`schemas/` contiene el JSON Schema exportado de estos modelos (`tools/export_schemas.py`).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mesa_monitor.core.enums import AlertStatus, AlertType, CameraStatus, Severity, TableState

SCHEMA_VERSION: Literal[1] = 1


class TableRef(BaseModel):
    id: str
    label: str
    sector: str


class TableStateData(BaseModel):
    """Estado vigente de una mesa (snapshot y `table.state_changed`)."""

    table: TableRef
    state: TableState
    state_since: datetime | None
    elapsed_seconds: float | None = Field(
        default=None, description="Segundos en el estado actual, descontando el tiempo sin visión"
    )
    previous_state: TableState | None = None
    reason: str | None = None
    session_id: str | None = None
    seated_at: datetime | None = None
    first_attention_at: datetime | None = None
    last_visit_at: datetime | None = None
    vacated_at: datetime | None = None
    people: int = 0


class StaffVisitData(BaseModel):
    table: TableRef
    session_id: str
    visit_id: str
    started_at: datetime


class AlertData(BaseModel):
    alert_id: str
    alert_type: AlertType
    severity: Severity
    status: AlertStatus
    table: TableRef | None = None
    camera_id: str | None = None
    state: TableState | None = None
    state_since: datetime | None = None
    elapsed_seconds: float | None = None
    threshold_seconds: float
    opened_at: datetime
    acknowledged_at: datetime | None = None
    snoozed_until: datetime | None = None
    resolved_at: datetime | None = None


class CameraStatusData(BaseModel):
    camera_id: str
    name: str
    status: CameraStatus
    detail: str | None = None


class SnapshotData(BaseModel):
    tables: list[TableStateData]
    alerts: list[AlertData]
    cameras: list[CameraStatusData]


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    ts: datetime


class SnapshotEvent(_Envelope):
    type: Literal["snapshot"] = "snapshot"
    data: SnapshotData


class TableStateChangedEvent(_Envelope):
    type: Literal["table.state_changed"] = "table.state_changed"
    data: TableStateData


class TableStaffVisitEvent(_Envelope):
    type: Literal["table.staff_visit"] = "table.staff_visit"
    data: StaffVisitData


class AlertOpenedEvent(_Envelope):
    type: Literal["alert.opened"] = "alert.opened"
    data: AlertData


class AlertUpdatedEvent(_Envelope):
    type: Literal["alert.updated"] = "alert.updated"
    data: AlertData


class AlertResolvedEvent(_Envelope):
    type: Literal["alert.resolved"] = "alert.resolved"
    data: AlertData


class CameraStatusEvent(_Envelope):
    type: Literal["camera.status"] = "camera.status"
    data: CameraStatusData


class HeartbeatEvent(_Envelope):
    type: Literal["heartbeat"] = "heartbeat"
    data: dict[str, str] = Field(default_factory=dict)


AnyEvent = Annotated[
    SnapshotEvent
    | TableStateChangedEvent
    | TableStaffVisitEvent
    | AlertOpenedEvent
    | AlertUpdatedEvent
    | AlertResolvedEvent
    | CameraStatusEvent
    | HeartbeatEvent,
    Field(discriminator="type"),
]

EVENT_MODELS: dict[str, type[_Envelope]] = {
    "snapshot": SnapshotEvent,
    "table.state_changed": TableStateChangedEvent,
    "table.staff_visit": TableStaffVisitEvent,
    "alert.opened": AlertOpenedEvent,
    "alert.updated": AlertUpdatedEvent,
    "alert.resolved": AlertResolvedEvent,
    "camera.status": CameraStatusEvent,
    "heartbeat": HeartbeatEvent,
}
# Tipos que se pueden suscribir por webhook (snapshot y heartbeat son solo del WebSocket).
WEBHOOK_EVENT_TYPES = [t for t in EVENT_MODELS if t not in ("snapshot", "heartbeat")]
