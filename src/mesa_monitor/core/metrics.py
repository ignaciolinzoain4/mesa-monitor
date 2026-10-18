"""Métricas de atención de mesas (SPEC §9). El cálculo se implementa en F7.

RESTRICCIÓN DE CUMPLIMIENTO (SPEC Anexo A, docs/cumplimiento.md):
- Las métricas se agregan SOLO por mesa, sector, franja horaria, día, día de semana y turno.
- Prohibido: métricas por empleado, rankings de personal, asignación mozo ↔ mesa o cualquier
  vista de "desempeño" individual. No se persiste ninguna identidad de quién hizo una visita.
- Si a futuro el POS informa el mozo asignado a cada mesa, ese dato NO se cruza con métricas
  de cámara sin revisión legal previa.
`tests/test_compliance.py` falla si se agrega una agrupación o columna que identifique personas.
"""

from enum import StrEnum


class MetricsGroupBy(StrEnum):
    """Agrupaciones permitidas para `/metrics/summary?group_by=`. Lista cerrada a propósito."""

    HOUR = "hour"
    DAY = "day"
    WEEKDAY = "weekday"
    SHIFT = "shift"
    TABLE = "table"
    SECTOR = "sector"
