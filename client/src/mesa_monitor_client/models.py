"""Modelos de los eventos y respuestas (contrato `schema_version` 1).

Se mantienen sincronizados con `schemas/` del servidor (un test lo verifica).
Los campos desconocidos se ignoran: un servidor más nuevo no rompe a un cliente viejo.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _M(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TableState(str, Enum):
    LIBRE = "LIBRE"
    OCUPADA_SIN_ATENDER = "OCUPADA_SIN_ATENDER"
    ATENDIDA = "ATENDIDA"
    CONSUMIENDO = "CONSUMIENDO"
    DESOCUPADA_SUCIA = "DESOCUPADA_SUCIA"
    SIN_VISION = "SIN_VISION"
    DESHABILITADA = "DESHABILITADA"


class AlertType(str, Enum):
    SIN_ATENCION = "SIN_ATENCION"
    SIN_VISITA = "SIN_VISITA"
    MESA_SUCIA = "MESA_SUCIA"
    ESTADIA_LARGA = "ESTADIA_LARGA"
    CAMARA_CAIDA = "CAMARA_CAIDA"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class CameraStatus(str, Enum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class TableRef(_M):
    id: str
    label: str
    sector: str


class TableStateData(_M):
    table: TableRef
    state: TableState
    state_since: datetime | None = None
    elapsed_seconds: float | None = None
    previous_state: TableState | None = None
    reason: str | None = None
    session_id: str | None = None
    seated_at: datetime | None = None
    first_attention_at: datetime | None = None
    last_visit_at: datetime | None = None
    served_at: datetime | None = None
    vacated_at: datetime | None = None
    people: int = 0
    partial: bool = False


class StaffVisitData(_M):
    table: TableRef
    session_id: str
    visit_id: str
    started_at: datetime


class AlertData(_M):
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

    @property
    def where(self) -> str:
        """Texto amigable: "Mesa 12" o "Cámara salon-1"."""
        return f"Mesa {self.table.label}" if self.table else f"Cámara {self.camera_id}"


class CameraStatusData(_M):
    camera_id: str
    name: str
    status: CameraStatus
    detail: str | None = None


class SnapshotData(_M):
    tables: list[TableStateData]
    alerts: list[AlertData]
    cameras: list[CameraStatusData]


class _Envelope(_M):
    schema_version: int = 1
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
    data: dict[str, Any] = Field(default_factory=dict)


Event = Annotated[
    Union[
        SnapshotEvent,
        TableStateChangedEvent,
        TableStaffVisitEvent,
        AlertOpenedEvent,
        AlertUpdatedEvent,
        AlertResolvedEvent,
        CameraStatusEvent,
        HeartbeatEvent,
    ],
    Field(discriminator="type"),
]

EVENT_TYPES = [
    "snapshot",
    "table.state_changed",
    "table.staff_visit",
    "alert.opened",
    "alert.updated",
    "alert.resolved",
    "camera.status",
    "heartbeat",
]


class Stat(_M):
    n: int
    avg: float | None
    median: float | None
    p90: float | None


class MetricsGroup(_M):
    key: str
    label: str
    n_sessions: int
    n_in_progress: int
    n_excluded: int
    n_unattended: int
    sla_pct: float | None
    visits_avg: float | None
    rotation: float | None
    occupancy_pct: float | None
    t_esp: Stat
    t_est: Stat
    t_lim: Stat
    g_max: Stat


class MetricsSummary(_M):
    group_by: str | None
    sla_threshold_seconds: float
    operating_hours: float
    n_tables: int
    groups: list[MetricsGroup]
