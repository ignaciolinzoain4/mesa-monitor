"""Modelos SQLAlchemy. Todos los timestamps son `timestamptz` en UTC.

Convenciones:
- Entidades expuestas por la API usan IDs texto con prefijo + ULID (`core.ids.new_id`).
- Tablas de alto volumen (logs, transiciones, entregas) usan bigint autoincremental.
- Polígonos: JSON `[[x, y], ...]` en coordenadas normalizadas 0–1 respecto del frame
  completo de la cámara, así sirven igual para main stream y substream.
"""

from datetime import date, datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mesa_monitor.core.enums import (
    ActorType,
    AlertStatus,
    AlertType,
    CameraStatus,
    CleanMode,
    DeliveryStatus,
    SessionCloseReason,
    Severity,
    StaffZoneKind,
    TableState,
    ThresholdScope,
    UserRole,
)
from mesa_monitor.db.base import Base, TimestampMixin, str_enum, utc_ts

Polygon = list[list[float]]

# --------------------------------------------------------------------------- cámaras y zonas


class Camera(TimestampMixin, Base):
    """Espejo de `cameras.yaml` (la fuente de verdad). Las credenciales nunca llegan a la base."""

    __tablename__ = "cameras"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # slug, ej. "cam1"
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # Nombre de la conexión en cameras.yaml (NULL = fuente literal, ej. video de prueba).
    connection: Mapped[str | None] = mapped_column(String(64))
    channel: Mapped[int | None] = mapped_column(SmallInteger)
    fps_detection: Mapped[float] = mapped_column(Float, server_default=text("3"))
    expected_width: Mapped[int | None] = mapped_column(Integer)
    expected_height: Mapped[int | None] = mapped_column(Integer)


class TableGroup(Base):
    """Grupo de mesas unidas. Sin lógica en el MVP; el modelo queda preparado."""

    __tablename__ = "table_groups"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())


class Table(TimestampMixin, Base):
    __tablename__ = "tables"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(32), unique=True)
    sector: Mapped[str] = mapped_column(String(64), index=True)
    plan_x: Mapped[float] = mapped_column(Float, server_default=text("0"))
    plan_y: Mapped[float] = mapped_column(Float, server_default=text("0"))
    plan_w: Mapped[float] = mapped_column(Float, server_default=text("1"))
    plan_h: Mapped[float] = mapped_column(Float, server_default=text("1"))
    plan_rotation: Mapped[float] = mapped_column(Float, server_default=text("0"))
    capacity: Mapped[int | None] = mapped_column(SmallInteger)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    group_id: Mapped[str | None] = mapped_column(ForeignKey("table_groups.id", ondelete="SET NULL"))


class TableView(TimestampMixin, Base):
    """Cómo ve una cámara a una mesa. Mayor `priority` = vista preferida."""

    __tablename__ = "table_views"
    __table_args__ = (UniqueConstraint("table_id", "camera_id"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    table_polygon: Mapped[Polygon] = mapped_column(JSONB)
    seats_polygon: Mapped[Polygon] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    clean_mode: Mapped[CleanMode] = mapped_column(
        str_enum(CleanMode, "clean_mode"), server_default=CleanMode.OBJECTS_AND_STAFF.value
    )


class StaffZone(TimestampMixin, Base):
    __tablename__ = "staff_zones"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    kind: Mapped[StaffZoneKind] = mapped_column(str_enum(StaffZoneKind, "staff_zone_kind"))
    name: Mapped[str] = mapped_column(String(120))
    polygon: Mapped[Polygon] = mapped_column(JSONB)


class IgnoreMask(TimestampMixin, Base):
    __tablename__ = "ignore_masks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    polygon: Mapped[Polygon] = mapped_column(JSONB)


# --------------------------------------------------------------------------- sesiones y estados


class TableSession(Base):
    """Una ocupación de mesa, desde que se sientan hasta que se limpia (o se cierra)."""

    __tablename__ = "table_sessions"
    __table_args__ = (
        Index("ix_table_sessions_table_id_seated_at", "table_id", "seated_at"),
        Index("ix_table_sessions_business_date", "business_date"),
        # Una sola sesión abierta por mesa.
        Index(
            "uq_table_sessions_one_open_per_table",
            "table_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    business_date: Mapped[date] = mapped_column(Date)
    seated_at: Mapped[datetime] = mapped_column(utc_ts())
    first_attention_at: Mapped[datetime | None] = mapped_column(utc_ts())
    vacated_at: Mapped[datetime | None] = mapped_column(utc_ts())
    cleaned_at: Mapped[datetime | None] = mapped_column(utc_ts())
    closed_at: Mapped[datetime | None] = mapped_column(utc_ts())
    close_reason: Mapped[SessionCloseReason | None] = mapped_column(
        str_enum(SessionCloseReason, "session_close_reason")
    )
    max_people: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    # Segundos acumulados en SIN_VISION durante la sesión (las alertas los descuentan).
    vision_lost_seconds: Mapped[float] = mapped_column(Float, server_default=text("0"))


class StaffVisit(Base):
    __tablename__ = "staff_visits"
    __table_args__ = (Index("ix_staff_visits_session_id_started_at", "session_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("table_sessions.id", ondelete="CASCADE"))
    table_id: Mapped[str] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(utc_ts())
    ended_at: Mapped[datetime] = mapped_column(utc_ts())
    confidence: Mapped[float | None] = mapped_column(Float)


class StateTransition(Base):
    __tablename__ = "state_transitions"
    __table_args__ = (Index("ix_state_transitions_table_id_ts", "table_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    table_id: Mapped[str] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("table_sessions.id", ondelete="SET NULL")
    )
    ts: Mapped[datetime] = mapped_column(utc_ts())
    from_state: Mapped[TableState | None] = mapped_column(str_enum(TableState, "from_state"))
    to_state: Mapped[TableState] = mapped_column(str_enum(TableState, "to_state"))
    reason: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)


# --------------------------------------------------------------------------- alertas y umbrales


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_status_opened_at", "status", "opened_at"),
        CheckConstraint("(table_id IS NULL) <> (camera_id IS NULL)", name="exactly_one_subject"),
        # Deduplicación: una sola alerta activa por (mesa, tipo) y por (cámara, tipo).
        Index(
            "uq_alerts_active_table_type",
            "table_id",
            "alert_type",
            unique=True,
            postgresql_where=text("status <> 'resolved' AND table_id IS NOT NULL"),
        ),
        Index(
            "uq_alerts_active_camera_type",
            "camera_id",
            "alert_type",
            unique=True,
            postgresql_where=text("status <> 'resolved' AND camera_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    alert_type: Mapped[AlertType] = mapped_column(str_enum(AlertType, "alert_type"))
    severity: Mapped[Severity] = mapped_column(str_enum(Severity, "severity"))
    status: Mapped[AlertStatus] = mapped_column(
        str_enum(AlertStatus, "alert_status"), server_default=AlertStatus.OPEN.value
    )
    table_id: Mapped[str | None] = mapped_column(ForeignKey("tables.id", ondelete="CASCADE"))
    camera_id: Mapped[str | None] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("table_sessions.id", ondelete="SET NULL")
    )
    state: Mapped[TableState | None] = mapped_column(str_enum(TableState, "alert_table_state"))
    state_since: Mapped[datetime | None] = mapped_column(utc_ts())
    threshold_seconds: Mapped[float] = mapped_column(Float)
    opened_at: Mapped[datetime] = mapped_column(utc_ts())
    updated_at: Mapped[datetime] = mapped_column(utc_ts())
    acknowledged_at: Mapped[datetime | None] = mapped_column(utc_ts())
    acknowledged_by: Mapped[str | None] = mapped_column(String(120))
    snoozed_until: Mapped[datetime | None] = mapped_column(utc_ts())
    resolved_at: Mapped[datetime | None] = mapped_column(utc_ts())
    resolve_reason: Mapped[str | None] = mapped_column(String(64))


class Threshold(Base):
    """Umbral / parámetro con scope global, por sector o por mesa.

    `valid_from`/`valid_to` preparan el override por franja horaria (NULL = todo el día).
    """

    __tablename__ = "thresholds"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "scope_ref",
            "key",
            "valid_from",
            "valid_to",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("(scope = 'global') = (scope_ref IS NULL)", name="scope_ref_matches_scope"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope: Mapped[ThresholdScope] = mapped_column(str_enum(ThresholdScope, "threshold_scope"))
    scope_ref: Mapped[str | None] = mapped_column(String(64))  # sector o table_id
    key: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSONB)
    valid_from: Mapped[time | None] = mapped_column(Time)
    valid_to: Mapped[time | None] = mapped_column(Time)
    updated_at: Mapped[datetime] = mapped_column(
        utc_ts(), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))


class StaffProfile(Base):
    """Perfil de apariencia de los mozos (histograma HSV del torso) + pesos del clasificador."""

    __tablename__ = "staff_profiles"
    __table_args__ = (
        Index(
            "uq_staff_profiles_single_active",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    color_profile: Mapped[dict[str, Any]] = mapped_column(JSONB)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB)
    threshold: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(120))


class OperatingHours(Base):
    """Horario operativo por día de semana (0 = lunes).

    Si `closes_at` < `opens_at`, el horario cruza la medianoche.
    """

    __tablename__ = "operating_hours"
    __table_args__ = (CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    opens_at: Mapped[time] = mapped_column(Time)
    closes_at: Mapped[time] = mapped_column(Time)


class Shift(Base):
    """Turno (mañana/tarde/noche) para filtrar el análisis."""

    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    starts_at: Mapped[time] = mapped_column(Time)
    ends_at: Mapped[time] = mapped_column(Time)


class AppSetting(Base):
    """Parámetros generales editables (ej. hora de corte del día de negocio)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        utc_ts(), server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str | None] = mapped_column(String(120))


class CameraStatusLog(Base):
    __tablename__ = "camera_status_log"
    __table_args__ = (Index("ix_camera_status_log_camera_id_ts", "camera_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(utc_ts())
    status: Mapped[CameraStatus] = mapped_column(str_enum(CameraStatus, "camera_status"))
    fps: Mapped[float | None] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- acceso


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "user_role"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(utc_ts())


class ApiKey(Base):
    """API key para integraciones. Se guarda solo el hash; `prefix` sirve para identificarla."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "api_key_role"), server_default=UserRole.VIEWER.value
    )
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(120))
    last_used_at: Mapped[datetime | None] = mapped_column(utc_ts())
    revoked_at: Mapped[datetime | None] = mapped_column(utc_ts())


class DeviceToken(Base):
    """Token de dispositivo para las PCs de tiqueo (pantalla /alertas). Token y PIN hasheados."""

    __tablename__ = "device_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    pin_hash: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(utc_ts())
    revoked_at: Mapped[datetime | None] = mapped_column(utc_ts())


class Webhook(TimestampMixin, Base):
    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(Text)
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    # Secreto para firmar HMAC-SHA256. Tiene que poder leerse para firmar.
    secret: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    webhook_id: Mapped[str] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    event_id: Mapped[str] = mapped_column(String(40))
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[DeliveryStatus] = mapped_column(
        str_enum(DeliveryStatus, "delivery_status"), server_default=DeliveryStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(utc_ts())
    last_status_code: Mapped[int | None] = mapped_column(SmallInteger)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(utc_ts())


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity_type_entity_id", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(utc_ts(), server_default=func.now(), index=True)
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    actor_id: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(120))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
