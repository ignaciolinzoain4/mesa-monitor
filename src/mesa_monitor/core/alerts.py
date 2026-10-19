"""Reglas de alertas (SPEC §10): qué alertas deberían estar activas en este instante. Pura.

La reconciliación con la base (abrir, escalar, resolver) la hace `api.alert_engine`.
Los contadores descuentan el tiempo sin visión (`paused_s`): nunca se alerta sobre una mesa en
SIN_VISION; en su lugar está la alerta CAMARA_CAIDA.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mesa_monitor.core.enums import AlertType, Severity, TableState


@dataclass(frozen=True)
class TableAlertInput:
    table_id: str
    state: TableState
    state_since: datetime | None
    paused_s: float
    seated_at: datetime | None
    first_attention_at: datetime | None
    last_visit_at: datetime | None


@dataclass(frozen=True)
class DesiredAlert:
    alert_type: AlertType
    severity: Severity
    table_id: str | None
    camera_id: str | None
    state: TableState | None
    since: datetime  # desde cuándo corre el contador
    elapsed_s: float
    threshold_s: float

    @property
    def key(self) -> tuple[str, AlertType]:
        return (self.table_id or f"cam:{self.camera_id}", self.alert_type)


def _secs(now: datetime, since: datetime, paused: float = 0.0) -> float:
    return max(0.0, (now - since).total_seconds() - paused)


def table_alerts(t: TableAlertInput, values: dict[str, Any], now: datetime) -> list[DesiredAlert]:
    out: list[DesiredAlert] = []
    if t.state_since is None:
        return out

    def add(kind: AlertType, sev: Severity, since: datetime, elapsed: float, thr: float) -> None:
        out.append(DesiredAlert(kind, sev, t.table_id, None, t.state, since, elapsed, thr))

    if t.state is TableState.OCUPADA_SIN_ATENDER:
        elapsed = _secs(now, t.state_since, t.paused_s)
        warn = float(values["alert_sin_atencion_warning_s"])
        crit = float(values["alert_sin_atencion_critical_s"])
        if elapsed >= crit:
            add(AlertType.SIN_ATENCION, Severity.CRITICAL, t.state_since, elapsed, crit)
        elif elapsed >= warn:
            add(AlertType.SIN_ATENCION, Severity.WARNING, t.state_since, elapsed, warn)

    elif t.state in (TableState.ATENDIDA, TableState.CONSUMIENDO):
        ref = t.last_visit_at or t.first_attention_at or t.state_since
        span = _secs(now, ref)
        elapsed = span - min(t.paused_s, span)
        thr = float(values["alert_sin_visita_s"])
        if elapsed >= thr:
            add(AlertType.SIN_VISITA, Severity.WARNING, ref, elapsed, thr)

    elif t.state is TableState.DESOCUPADA_SUCIA:
        elapsed = _secs(now, t.state_since, t.paused_s)
        thr = float(values["alert_mesa_sucia_s"])
        if elapsed >= thr:
            add(AlertType.MESA_SUCIA, Severity.WARNING, t.state_since, elapsed, thr)

    if (
        values.get("alert_estadia_larga_enabled")
        and t.state in (TableState.OCUPADA_SIN_ATENDER, TableState.ATENDIDA, TableState.CONSUMIENDO)
        and t.seated_at is not None
    ):
        stay = _secs(now, t.seated_at)
        thr = float(values["alert_estadia_larga_s"])
        if stay >= thr:
            add(AlertType.ESTADIA_LARGA, Severity.INFO, t.seated_at, stay, thr)
    return out


def camera_alert(
    camera_id: str, down_since: datetime | None, values: dict[str, Any], now: datetime
) -> DesiredAlert | None:
    if down_since is None:
        return None
    elapsed = _secs(now, down_since)
    thr = float(values["alert_camara_caida_s"])
    if elapsed < thr:
        return None
    return DesiredAlert(
        AlertType.CAMARA_CAIDA, Severity.CRITICAL, None, camera_id, None, down_since, elapsed, thr
    )


SEVERITY_RANK = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
