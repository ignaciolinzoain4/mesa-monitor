"""Cliente liviano de Mesa Monitor (estado de mesas, alertas y métricas)."""

from mesa_monitor_client.client import MesaMonitorClient
from mesa_monitor_client.errors import (
    ApiKeyInvalida,
    ErrorDelServidor,
    FirmaInvalida,
    MesaMonitorError,
    PermisoDenegado,
    ServidorNoDisponible,
)
from mesa_monitor_client.models import AlertData, MetricsSummary, SnapshotData, TableStateData
from mesa_monitor_client.webhooks import parse_webhook, verify_signature

__version__ = "0.1.0"
__all__ = [
    "AlertData",
    "ApiKeyInvalida",
    "ErrorDelServidor",
    "FirmaInvalida",
    "MesaMonitorClient",
    "MesaMonitorError",
    "MetricsSummary",
    "PermisoDenegado",
    "ServidorNoDisponible",
    "SnapshotData",
    "TableStateData",
    "parse_webhook",
    "verify_signature",
]
