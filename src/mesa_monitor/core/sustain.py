"""Detector de "condición sostenida en el tiempo" con tolerancia a huecos breves.

La detección por cámara parpadea: una persona sentada puede no detectarse en 1–2 frames, o un
cliente con campera negra puede parecer mozo un instante. `Sustain` mide desde cuándo una
condición se cumple y tolera huecos de hasta `gap_s` segundos sin reiniciar la cuenta.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime  # último instante en que la condición se cumplió

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


class Sustain:
    def __init__(self, gap_s: float) -> None:
        self.gap_s = gap_s
        self.start: datetime | None = None
        self.last_true: datetime | None = None
        self.true_samples = 0
        self.samples = 0

    def update(self, cond: bool, ts: datetime) -> Interval | None:
        """Actualiza con una muestra. Devuelve el intervalo si la condición se acaba de cortar."""
        if cond:
            if self.start is None:
                self.start = ts
                self.true_samples = 0
                self.samples = 0
            self.last_true = ts
            self.true_samples += 1
            self.samples += 1
            return None
        if self.start is None or self.last_true is None:
            return None
        self.samples += 1
        if (ts - self.last_true).total_seconds() > self.gap_s:
            ended = Interval(self.start, self.last_true)
            self.reset()
            return ended
        return None

    @property
    def active(self) -> bool:
        return self.start is not None

    def held_for(self) -> float:
        """Segundos entre el inicio y la última muestra verdadera (0 si no está activa)."""
        if self.start is None or self.last_true is None:
            return 0.0
        return (self.last_true - self.start).total_seconds()

    def held(self, seconds: float) -> bool:
        return self.active and self.held_for() >= seconds

    @property
    def ratio(self) -> float:
        """Fracción de muestras verdaderas desde el inicio (se usa como confianza)."""
        return self.true_samples / self.samples if self.samples else 0.0

    def reset(self) -> None:
        self.start = None
        self.last_true = None
        self.true_samples = 0
        self.samples = 0
