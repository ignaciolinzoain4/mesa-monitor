"""Distinción personal / cliente (SPEC §7).

`StaffClassifier` es la interfaz; `HeuristicStaffClassifier` es la implementación del MVP:
un puntaje ponderado por track con histéresis. Sin dependencias pesadas (testeable).

Cumplimiento (SPEC Anexo A): los `track_id` viven solo en memoria, se olvidan al perder el
track y nunca se persisten. El clasificador no identifica a nadie: solo responde "¿es personal?".
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from mesa_monitor.core.color import ColorProfile


@dataclass(frozen=True)
class StaffWeights:
    standing: float = 1.0  # parado suma
    seated: float = -1.5  # sentado resta fuerte
    staff_zone: float = 1.5  # pasó por barra/pase/caja en los últimos `memory_s`
    multi_table: float = 1.0  # visitó ≥ 2 mesas distintas en los últimos `memory_s`
    long_seated: float = -2.0  # sentado mucho tiempo en una sola mesa
    color: float = 1.0  # similitud del torso con el perfil de personal (−1 a +1 escalado)
    threshold: float = 1.0
    hysteresis: float = 0.4
    confirm_frames: int = 3
    memory_s: float = 300
    long_seated_s: float = 120
    table_dwell_s: float = 2.0  # permanencia mínima para contar que "visitó" una mesa

    @classmethod
    def from_json(
        cls, data: dict[str, Any] | None, threshold: float | None = None
    ) -> "StaffWeights":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        if threshold is not None:
            known["threshold"] = threshold
        return cls(**known)


@dataclass(frozen=True)
class PersonFeatures:
    track_id: int
    ts: datetime
    standing: bool
    table_id: str | None  # mesa cuyos asientos contienen a la persona
    in_staff_zone: bool
    color_similarity: float | None  # None si no se calculó en este frame


class StaffClassifier(Protocol):
    def classify(self, f: PersonFeatures) -> bool: ...  # True = personal

    def forget(self, active_track_ids: set[int], now: datetime | None = None) -> None: ...


@dataclass
class _Track:
    first_seen: datetime
    last_seen: datetime
    is_staff: bool = False
    streak: int = 0
    score: float = 0.0
    last_staff_zone: datetime | None = None
    tables: deque[tuple[datetime, str]] = field(default_factory=lambda: deque(maxlen=64))
    at_table: str | None = None
    at_table_since: datetime | None = None
    seated_table: str | None = None
    seated_since: datetime | None = None
    color: float | None = None  # promedio exponencial


class HeuristicStaffClassifier:
    def __init__(
        self, weights: StaffWeights | None = None, profile: ColorProfile | None = None
    ) -> None:
        self.weights = weights or StaffWeights()
        self.profile = profile
        self.forget_after_s = 10.0
        self._tracks: dict[int, _Track] = {}

    def configure(self, weights: StaffWeights, profile: ColorProfile | None) -> None:
        self.weights, self.profile = weights, profile

    def score(self, f: PersonFeatures) -> float:
        t = self._tracks[f.track_id]
        w = self.weights
        memory = timedelta(seconds=w.memory_s)
        s = w.standing if f.standing else w.seated
        if t.last_staff_zone and f.ts - t.last_staff_zone <= memory:
            s += w.staff_zone
        recent = {tid for (ts, tid) in t.tables if f.ts - ts <= memory}
        if len(recent) >= 2 and f.standing:
            s += w.multi_table
        if t.seated_since and (f.ts - t.seated_since).total_seconds() >= w.long_seated_s:
            s += w.long_seated
        if t.color is not None and self.profile is not None:
            s += w.color * (t.color - 0.5) * 2
        return s

    def classify(self, f: PersonFeatures) -> bool:
        t = self._tracks.get(f.track_id)
        if t is None:
            t = self._tracks[f.track_id] = _Track(first_seen=f.ts, last_seen=f.ts)
        t.last_seen = f.ts
        if f.in_staff_zone:
            t.last_staff_zone = f.ts
        # Una mesa cuenta como visitada si la persona permanece ahí `table_dwell_s` (evita que el
        # punto de anclaje oscilando entre asientos vecinos parezca "visitar dos mesas").
        if f.table_id != t.at_table:
            t.at_table, t.at_table_since = f.table_id, f.ts
        elif (
            f.table_id
            and t.at_table_since is not None
            and (f.ts - t.at_table_since).total_seconds() >= self.weights.table_dwell_s
            and (not t.tables or t.tables[-1][1] != f.table_id)
        ):
            t.tables.append((f.ts, f.table_id))
        if not f.standing and f.table_id:
            if t.seated_table != f.table_id:
                t.seated_table, t.seated_since = f.table_id, f.ts
        else:
            t.seated_table, t.seated_since = None, None
        if f.color_similarity is not None:
            t.color = (
                f.color_similarity if t.color is None else 0.7 * t.color + 0.3 * f.color_similarity
            )

        t.score = self.score(f)
        w = self.weights
        want = t.is_staff
        if not t.is_staff and t.score >= w.threshold + w.hysteresis / 2:
            want = True
        elif t.is_staff and t.score <= w.threshold - w.hysteresis / 2:
            want = False
        if want != t.is_staff:
            t.streak += 1
            if t.streak >= w.confirm_frames:
                t.is_staff, t.streak = want, 0
        else:
            t.streak = 0
        return t.is_staff

    def forget(self, active_track_ids: set[int], now: datetime | None = None) -> None:
        """Olvida tracks que no se ven hace `forget_after_s`. Un track que el detector pierde
        por uno o dos frames conserva su clasificación (si no, el mozo "parpadea" a cliente)."""
        for tid, t in list(self._tracks.items()):
            if tid in active_track_ids:
                continue
            if now is None or (now - t.last_seen).total_seconds() > self.forget_after_s:
                del self._tracks[tid]

    def debug_score(self, track_id: int) -> float | None:
        t = self._tracks.get(track_id)
        return None if t is None else round(t.score, 2)
