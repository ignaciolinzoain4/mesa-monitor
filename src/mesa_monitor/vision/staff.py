"""Distinción personal / cliente (SPEC §7).

`StaffClassifier` es la interfaz; `HeuristicStaffClassifier` es la implementación del MVP:
un puntaje ponderado por track con histéresis (estar parado, llevar rato de pie, haber trabajado
en la barra, recorrer varias mesas, ropa) más tres reglas duras aprendidas con video real:

- **Sentado en una mesa = cliente.** Quien queda sentado en los asientos de una mesa
  `seated_lock_s` deja de poder ser personal mientras dure su track (salvo que después se pare
  y trabaje un rato detrás de la barra). Evita que un cliente que gesticula o se acomoda
  "parezca" mozo y le marque visitas a la mesa de al lado.
- **La barra pesa solo si la persona se queda.** Pasar por la zona de personal (un cliente que
  paga o pregunta algo) no alcanza: hay que estar parado ahí `staff_zone_dwell_s`.
- **Traspaso de identidad.** Cuando el tracker pierde a un mozo y aparece un track nuevo en el
  mismo lugar enseguida (oclusiones, cruces con otra persona), el nuevo hereda la clasificación.

Sin perfil de color calibrado, el color usa la "ropa oscura" del torso (los mozos de negro):
ver `dark_similarity`. Sin dependencias pesadas (testeable).

Cumplimiento (SPEC Anexo A): los `track_id` viven solo en memoria, se olvidan al perder el
track y nunca se persisten. El clasificador no identifica a nadie: solo responde "¿es personal?".
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from mesa_monitor.core.color import ColorProfile


@dataclass(frozen=True)
class StaffWeights:
    standing: float = 0.5  # parado suma (los clientes también se paran: poco peso)
    seated: float = -1.5  # sentado resta fuerte
    staff_zone: float = 1.2  # estuvo `staff_zone_dwell_s` en barra/pase/caja hace ≤ `memory_s`
    multi_table: float = 0.8  # se paró en ≥ `multi_table_min` mesas distintas hace ≤ `memory_s`
    long_standing: float = 0.8  # lleva `long_standing_s` de pie y recorrió el salón
    long_seated: float = -2.0  # sentado mucho tiempo en una sola mesa
    color: float = 0.8  # similitud del torso con el perfil de personal (−1 a +1 escalado)
    threshold: float = 1.5
    hysteresis: float = 0.4
    confirm_frames: int = 2
    memory_s: float = 300
    long_seated_s: float = 120
    long_standing_s: float = 8
    long_standing_range: float = 0.06  # desplazamiento mínimo de pie (fracción de la imagen)
    table_dwell_s: float = 2.0  # permanencia mínima para contar que "visitó" una mesa
    multi_table_min: int = 3
    staff_zone_dwell_s: float = 3.0
    seated_lock_s: float = 6.0  # sentado en una mesa esto → cliente (x3 si ya era personal)
    still_lock_s: float = 45.0  # quieto en una silla (aunque se vea "parado") → cliente
    handoff_s: float = 4.0  # un track nuevo hereda al mozo perdido hace ≤ esto …
    handoff_dist: float = 0.06  # … si aparece a esta distancia (fracción de la imagen)
    jump_dist: float = 0.05  # el mismo track_id salta esto en < 1 s: el tracker cambió de persona

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
    x: float | None = None  # punto de anclaje (para el traspaso de identidad)
    y: float | None = None
    still: bool = False  # no se desplaza (ver `scene.PostureParams.still_speed`)
    top: float | None = (
        None  # borde superior de la caja (la cabeza: no salta si se tapan las piernas)
    )


class StaffClassifier(Protocol):
    def begin_frame(self, active_track_ids: set[int], now: datetime) -> None: ...

    def classify(self, f: PersonFeatures) -> bool: ...  # True = personal

    def forget(self, active_track_ids: set[int], now: datetime | None = None) -> None: ...

    def table_dwell_s(self, track_id: int, now: datetime) -> float: ...


# Ropa oscura sin perfil calibrado: fracción de píxeles oscuros del torso.
DARK_V = 70  # brillo (0–255) por debajo del cual un píxel es "oscuro"
DARK_LOW, DARK_HIGH = 0.3, 0.6  # fracción → similitud 0 … 1


def dark_similarity(torso_hsv: NDArray[np.uint8]) -> float:
    """0 = ropa clara (no parece mozo de negro), 1 = torso oscuro, 0.5 = no se sabe."""
    px = torso_hsv.reshape(-1, 3)
    if len(px) < 20:
        return 0.5
    dark = float((px[:, 2] < DARK_V).mean())
    return float(min(1.0, max(0.0, (dark - DARK_LOW) / (DARK_HIGH - DARK_LOW))))


@dataclass
class _Track:
    first_seen: datetime
    last_seen: datetime
    is_staff: bool = False
    streak: int = 0
    score: float = 0.0
    last_staff_zone: datetime | None = None
    zone_since: datetime | None = None
    tables: deque[tuple[datetime, str]] = field(default_factory=lambda: deque(maxlen=64))
    at_table: str | None = None
    at_table_since: datetime | None = None
    seated_table: str | None = None
    seated_since: datetime | None = None
    customer_lock: bool = False
    standing_since: datetime | None = None
    not_standing: int = 0  # frames seguidos sin estar parado
    stand_box: tuple[float, float, float, float] | None = None  # extensión recorrida de pie
    not_seated: int = 0  # frames seguidos fuera de una silla (tolerancia del reloj de sentado)
    color: float | None = None  # promedio exponencial
    xy: tuple[float, float] | None = None


class HeuristicStaffClassifier:
    def __init__(
        self, weights: StaffWeights | None = None, profile: ColorProfile | None = None
    ) -> None:
        self.weights = weights or StaffWeights()
        self.profile = profile
        self.forget_after_s = 10.0
        self._tracks: dict[int, _Track] = {}
        self._active: set[int] = set()

    def configure(self, weights: StaffWeights, profile: ColorProfile | None) -> None:
        self.weights, self.profile = weights, profile

    def begin_frame(self, active_track_ids: set[int], now: datetime) -> None:
        """Tracks presentes en este frame (los demás pueden ceder su identidad a uno nuevo)."""
        self._active = set(active_track_ids)

    def score(self, f: PersonFeatures) -> float:
        t = self._tracks[f.track_id]
        w = self.weights
        memory = timedelta(seconds=w.memory_s)
        if not f.standing:
            s = w.seated
        elif f.still and f.table_id:
            s = 0.0  # "parado" pero quieto en una silla: la postura no dice nada
        else:
            s = w.standing
        if t.last_staff_zone and f.ts - t.last_staff_zone <= memory:
            s += w.staff_zone
        recent = {tid for (ts, tid) in t.tables if f.ts - ts <= memory}
        if len(recent) >= w.multi_table_min and f.standing:
            s += w.multi_table
        # De pie hace rato *en el salón* (en la barra ya cuenta `staff_zone`; así un cliente que
        # espera para pagar no suma dos veces).
        if (
            f.standing
            and not f.in_staff_zone
            and t.standing_since
            and (f.ts - t.standing_since).total_seconds() >= w.long_standing_s
            and t.stand_box is not None
            and max(t.stand_box[2] - t.stand_box[0], t.stand_box[3] - t.stand_box[1])
            >= w.long_standing_range
        ):
            s += w.long_standing
        if t.seated_since and (f.ts - t.seated_since).total_seconds() >= w.long_seated_s:
            s += w.long_seated
        if t.color is not None:
            s += w.color * (t.color - 0.5) * 2
        return s

    def _handoff(self, f: PersonFeatures) -> _Track | None:
        """Mozo perdido hace poco cerca de donde aparece este track nuevo."""
        if f.x is None or f.y is None:
            return None
        w = self.weights
        best: tuple[float, int] | None = None
        for tid, t in self._tracks.items():
            if not t.is_staff or tid in self._active or t.xy is None:
                continue
            gap = (f.ts - t.last_seen).total_seconds()
            if gap <= 0 or gap > w.handoff_s:
                continue
            d = ((t.xy[0] - f.x) ** 2 + (t.xy[1] - f.y) ** 2) ** 0.5
            if d <= w.handoff_dist and (best is None or d < best[0]):
                best = (d, tid)
        return None if best is None else self._tracks.pop(best[1])

    def classify(self, f: PersonFeatures) -> bool:
        w = self.weights
        t = self._tracks.get(f.track_id)
        if t is None:
            prev = self._handoff(f)
            if prev is not None:
                t = prev
                t.streak, t.customer_lock = 0, False
                t.seated_table = t.seated_since = None
            else:
                t = _Track(first_seen=f.ts, last_seen=f.ts)
            self._tracks[f.track_id] = t
        elif (
            t.xy is not None
            and f.x is not None
            and f.y is not None
            and (f.ts - t.last_seen).total_seconds() < 1.0
            and abs(f.x - t.xy[0]) > w.jump_dist
        ):
            # El tracker le pasó este id a otra persona (cruce): no hereda nada del anterior
            # (ni "cliente sentado" ni "mozo").
            t = self._tracks[f.track_id] = _Track(first_seen=f.ts, last_seen=f.ts)
        t.last_seen = f.ts
        if f.x is not None and f.y is not None:
            t.xy = (f.x, f.y)

        if f.standing:
            t.standing_since, t.not_standing = t.standing_since or f.ts, 0
            # Recorrido de pie medido con el centro horizontal y la cabeza: el punto de los pies
            # salta cuando una mesa o una silla tapa las piernas.
            hy = f.top if f.top is not None else f.y
            if f.x is not None and hy is not None:
                b = t.stand_box or (f.x, hy, f.x, hy)
                t.stand_box = (min(b[0], f.x), min(b[1], hy), max(b[2], f.x), max(b[3], hy))
        else:
            t.not_standing += 1
            if t.not_standing >= 3:  # un frame dudoso no corta "lleva rato parado"
                t.standing_since, t.stand_box = None, None

        # Barra/pase/caja: cuenta si se queda parado ahí, no si pasa o se acerca a pagar.
        if f.in_staff_zone and f.standing:
            t.zone_since = t.zone_since or f.ts
            if (f.ts - t.zone_since).total_seconds() >= w.staff_zone_dwell_s:
                t.last_staff_zone = f.ts
                t.customer_lock = False
        else:
            t.zone_since = None

        # Una mesa cuenta como visitada si la persona permanece ahí `table_dwell_s` (evita que el
        # punto de anclaje oscilando entre asientos vecinos parezca "visitar dos mesas").
        if f.table_id != t.at_table:
            t.at_table, t.at_table_since = f.table_id, f.ts
        elif (
            f.table_id
            and t.at_table_since is not None
            and (f.ts - t.at_table_since).total_seconds() >= w.table_dwell_s
            and (not t.tables or t.tables[-1][1] != f.table_id)
        ):
            t.tables.append((f.ts, f.table_id))
        # Sentado (o quieto, aunque la cámara lo vea alargado) en los asientos de una mesa.
        if f.table_id and (not f.standing or f.still):
            t.not_seated = 0
            if t.seated_table != f.table_id:
                t.seated_table, t.seated_since = f.table_id, f.ts
            seated_for = (f.ts - (t.seated_since or f.ts)).total_seconds()
            lock_after = w.still_lock_s if f.standing else w.seated_lock_s
            if seated_for >= lock_after * (3 if t.is_staff else 1):
                t.customer_lock = True
        else:
            t.not_seated += 1
            if t.not_seated >= 3:  # un frame con la caja cortada no reinicia el reloj
                t.seated_table, t.seated_since = None, None
        if f.color_similarity is not None:
            t.color = (
                f.color_similarity if t.color is None else 0.7 * t.color + 0.3 * f.color_similarity
            )

        t.score = self.score(f)
        if t.customer_lock:
            t.is_staff, t.streak = False, 0
            return False
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

    def table_dwell_s(self, track_id: int, now: datetime) -> float:
        """Segundos que el track lleva en los asientos de su mesa actual (0 si no está en una)."""
        t = self._tracks.get(track_id)
        if t is None or t.at_table is None or t.at_table_since is None:
            return 0.0
        return (now - t.at_table_since).total_seconds()

    def debug_score(self, track_id: int) -> float | None:
        t = self._tracks.get(track_id)
        return None if t is None else round(t.score, 2)
