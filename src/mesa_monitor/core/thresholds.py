"""Umbrales configurables: valores por defecto (SPEC §8.2 y §10.1) y resolución de overrides.

Prioridad: mesa > sector > global > default. Las franjas horarias (`valid_from`/`valid_to`) ya
están en el modelo de datos: si la hora local cae en la franja, ese valor gana dentro de su scope.
"""

from collections.abc import Iterable
from dataclasses import dataclass, fields
from datetime import time
from typing import Any

from mesa_monitor.core.enums import ThresholdScope
from mesa_monitor.core.state_engine import EngineParams

# key: (default, descripción en español, unidad)
DEFINITIONS: dict[str, tuple[Any, str, str]] = {
    # state engine
    "occupy_confirm_s": (20, "Tiempo sentados para considerar la mesa ocupada", "s"),
    "vacate_confirm_s": (
        120,
        "Tiempo vacía para considerar que se fueron (tolera ir al baño)",
        "s",
    ),
    "visit_min_s": (4, "Duración mínima de una visita del personal", "s"),
    "visit_max_s": (600, "Duración máxima de una visita", "s"),
    "consume_confirm_s": (
        10,
        "Tiempo con comida o bebida en la mesa para pasar a Consumiendo",
        "s",
    ),
    "clean_confirm_s": (15, "Tiempo sin objetos para considerar la mesa limpia", "s"),
    "clean_objects_max": (0, "Objetos tolerados en una mesa limpia", "objetos"),
    "vision_loss_close_s": (900, "Tiempo sin visión que cierra la sesión", "s"),
    # alertas
    "alert_sin_atencion_warning_s": (180, "Sin atención: aviso (U₁)", "s"),
    "alert_sin_atencion_critical_s": (360, "Sin atención: crítico", "s"),
    "alert_sin_visita_s": (900, "Sin visita desde la última atención (U₂)", "s"),
    "alert_mesa_sucia_s": (300, "Mesa sucia (U₃)", "s"),
    "alert_estadia_larga_s": (7200, "Estadía larga (U₄)", "s"),
    "alert_estadia_larga_enabled": (False, "Activar alerta de estadía larga", ""),
    "alert_camara_caida_s": (30, "Cámara sin imagen (U_cam)", "s"),
    "alert_sound_repeat_s": (60, "Repetir el sonido de una alerta no reconocida cada", "s"),
}
DEFAULTS: dict[str, Any] = {k: v[0] for k, v in DEFINITIONS.items()}


@dataclass(frozen=True)
class ThresholdRow:
    scope: ThresholdScope
    scope_ref: str | None
    key: str
    value: Any
    valid_from: time | None = None
    valid_to: time | None = None

    def active_at(self, t: time | None) -> bool:
        if self.valid_from is None or self.valid_to is None:
            return True
        if t is None:
            return False
        if self.valid_from <= self.valid_to:
            return self.valid_from <= t < self.valid_to
        return t >= self.valid_from or t < self.valid_to


def validate_value(key: str, value: Any) -> Any:
    if key not in DEFINITIONS:
        raise ValueError(f"Umbral desconocido: {key}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} debe ser verdadero/falso")
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} debe ser un número")
    if value < 0:
        raise ValueError(f"{key} no puede ser negativo")
    return int(value) if isinstance(default, int) and float(value).is_integer() else float(value)


def resolve(
    rows: Iterable[ThresholdRow],
    *,
    table_id: str | None = None,
    sector: str | None = None,
    local_time: time | None = None,
) -> dict[str, Any]:
    """Valores efectivos para una mesa (o globales si no se pasa mesa/sector)."""
    out = dict(DEFAULTS)
    ranked: list[tuple[int, int, ThresholdRow]] = []
    for r in rows:
        if r.key not in DEFINITIONS or not r.active_at(local_time):
            continue
        if r.scope is ThresholdScope.GLOBAL:
            level = 0
        elif r.scope is ThresholdScope.SECTOR and sector is not None and r.scope_ref == sector:
            level = 1
        elif r.scope is ThresholdScope.TABLE and table_id is not None and r.scope_ref == table_id:
            level = 2
        else:
            continue
        banded = 1 if r.valid_from is not None else 0
        ranked.append((level, banded, r))
    for _, _, r in sorted(ranked, key=lambda x: (x[0], x[1])):
        out[r.key] = r.value
    return out


def engine_params(values: dict[str, Any], base: EngineParams | None = None) -> EngineParams:
    base = base or EngineParams()
    names = {f.name for f in fields(EngineParams)}
    kwargs = {k: v for k, v in values.items() if k in names}
    return EngineParams(**{**base.__dict__, **kwargs})
