"""Enumeraciones del dominio. Los valores son parte del contrato público de la API."""

from enum import StrEnum


class TableState(StrEnum):
    LIBRE = "LIBRE"
    OCUPADA_SIN_ATENDER = "OCUPADA_SIN_ATENDER"
    ATENDIDA = "ATENDIDA"
    CONSUMIENDO = "CONSUMIENDO"
    DESOCUPADA_SUCIA = "DESOCUPADA_SUCIA"
    SIN_VISION = "SIN_VISION"
    DESHABILITADA = "DESHABILITADA"


class SessionCloseReason(StrEnum):
    CLEANED = "cleaned"
    REOCCUPIED = "reoccupied"
    VISION_LOST = "vision_lost"
    CLOSING_TIME = "closing_time"
    DISABLED = "disabled"
    NO_SERVICE = "no_service"  # se fueron enseguida sin atención ni consumo (no ensucia métricas)


class CleanMode(StrEnum):
    OBJECTS_AND_STAFF = "objects_and_staff"
    STAFF_VISIT_ONLY = "staff_visit_only"


class StaffZoneKind(StrEnum):
    BAR = "bar"
    KITCHEN_PASS = "kitchen_pass"  # noqa: S105  (pase de cocina, no es una contraseña)
    CASHIER = "cashier"
    OTHER = "other"


class AlertType(StrEnum):
    SIN_ATENCION = "SIN_ATENCION"
    SIN_VISITA = "SIN_VISITA"
    MESA_SUCIA = "MESA_SUCIA"
    ESTADIA_LARGA = "ESTADIA_LARGA"
    CAMARA_CAIDA = "CAMARA_CAIDA"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"


class ThresholdScope(StrEnum):
    GLOBAL = "global"
    SECTOR = "sector"
    TABLE = "table"


class CameraStatus(StrEnum):
    ONLINE = "online"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class UserRole(StrEnum):
    ADMIN = "admin"
    VIEWER = "viewer"


class ActorType(StrEnum):
    USER = "user"
    API_KEY = "api_key"
    DEVICE = "device"
    SYSTEM = "system"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
