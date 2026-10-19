"""Horario operativo, turnos y "día de negocio" (el local cierra después de medianoche)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

DEFAULT_CUTOFF = time(6, 0)


@dataclass(frozen=True)
class OpeningSpan:
    weekday: int  # 0 = lunes (del día en que abre)
    opens_at: time
    closes_at: time  # si es <= opens_at, cierra al día siguiente

    def interval_for(self, day: date, tz: ZoneInfo) -> tuple[datetime, datetime]:
        start = datetime.combine(day, self.opens_at, tz)
        end_day = day + timedelta(days=1) if self.closes_at <= self.opens_at else day
        return start, datetime.combine(end_day, self.closes_at, tz)


def business_date(ts: datetime, tz: ZoneInfo, cutoff: time = DEFAULT_CUTOFF) -> date:
    """Fecha de negocio: lo que pasa antes de `cutoff` (ej. 02:00) cuenta para el día anterior."""
    local = ts.astimezone(tz)
    d = local.date()
    return d - timedelta(days=1) if local.time() < cutoff else d


def open_interval(
    ts: datetime, spans: Sequence[OpeningSpan], tz: ZoneInfo
) -> tuple[datetime, datetime] | None:
    """Intervalo de apertura que contiene `ts`, o None si está cerrado.

    Sin horarios configurados se considera siempre abierto (devuelve un intervalo enorme).
    """
    if not spans:
        local = ts.astimezone(tz)
        return local - timedelta(days=3650), local + timedelta(days=3650)
    local = ts.astimezone(tz)
    for back in (0, 1):  # el intervalo pudo empezar ayer (cruza medianoche)
        day = local.date() - timedelta(days=back)
        for s in spans:
            if s.weekday != day.weekday():
                continue
            start, end = s.interval_for(day, tz)
            if start <= local < end:
                return start, end
    return None


def is_open(ts: datetime, spans: Sequence[OpeningSpan], tz: ZoneInfo) -> bool:
    return open_interval(ts, spans, tz) is not None


def operating_hours_between(
    start: datetime, end: datetime, spans: Sequence[OpeningSpan], tz: ZoneInfo
) -> float:
    """Horas de apertura dentro de [start, end) (para rotación y ocupación). Sin horarios: todo."""
    if end <= start:
        return 0.0
    if not spans:
        return (end - start).total_seconds() / 3600
    total = 0.0
    day = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date()
    while day <= last:
        for s in spans:
            if s.weekday != day.weekday():
                continue
            a, b = s.interval_for(day, tz)
            lo, hi = max(a, start), min(b, end)
            if hi > lo:
                total += (hi - lo).total_seconds()
        day += timedelta(days=1)
    return total / 3600


@dataclass(frozen=True)
class ShiftSpan:
    name: str
    starts_at: time
    ends_at: time

    def contains(self, t: time) -> bool:
        if self.starts_at <= self.ends_at:
            return self.starts_at <= t < self.ends_at
        return t >= self.starts_at or t < self.ends_at  # cruza medianoche


def shift_of(ts: datetime, shifts: Sequence[ShiftSpan], tz: ZoneInfo) -> str | None:
    t = ts.astimezone(tz).time()
    return next((s.name for s in shifts if s.contains(t)), None)
