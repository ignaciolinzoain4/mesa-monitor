"""Histogramas de color del torso (HSV) y perfil de apariencia del personal (SPEC §7.1–7.2).

Solo numpy: lo usan el worker (por track) y la API (calibración del perfil).
El "perfil" guarda el histograma promedio del personal y el de los clientes; la similitud de
una persona es qué tanto se parece al personal *en relación* a los clientes (0 a 1). Así sirve
tanto para "ropa negra" como para un delantal de color, sin cambiar código.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Any

import numpy as np
from numpy.typing import NDArray

H_BINS, S_BINS, V_BINS = 8, 3, 4
N_BINS = H_BINS * S_BINS * V_BINS

# Región del torso dentro del recuadro de la persona (proporciones).
TORSO_X = (0.25, 0.75)
TORSO_Y = (0.15, 0.50)

Box = Sequence[float]  # x1, y1, x2, y2 en píxeles


def torso_crop(hsv: NDArray[np.uint8], box: Box) -> NDArray[np.uint8]:
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    cx1, cx2 = int(x1 + w * TORSO_X[0]), int(x1 + w * TORSO_X[1])
    cy1, cy2 = int(y1 + h * TORSO_Y[0]), int(y1 + h * TORSO_Y[1])
    img_h, img_w = hsv.shape[:2]
    cx1, cx2 = max(0, cx1), min(img_w, max(cx1 + 1, cx2))
    cy1, cy2 = max(0, cy1), min(img_h, max(cy1 + 1, cy2))
    return hsv[cy1:cy2, cx1:cx2]


def hsv_histogram(hsv_pixels: NDArray[np.uint8]) -> NDArray[np.float64]:
    """Histograma 3D normalizado (suma 1) de píxeles HSV en escala 0–255 (formato Pillow)."""
    px = hsv_pixels.reshape(-1, 3).astype(np.int32)
    if px.size == 0:
        return np.full(N_BINS, 1.0 / N_BINS)
    h = np.minimum(px[:, 0] * H_BINS // 256, H_BINS - 1)
    s = np.minimum(px[:, 1] * S_BINS // 256, S_BINS - 1)
    v = np.minimum(px[:, 2] * V_BINS // 256, V_BINS - 1)
    # Con poca saturación o poca luz el tono no significa nada: va todo al bin de tono 0.
    h = np.where((px[:, 1] < 40) | (px[:, 2] < 40), 0, h)
    idx = (h * S_BINS + s) * V_BINS + v
    hist = np.bincount(idx, minlength=N_BINS).astype(np.float64)
    return hist / hist.sum()


def intersection(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
    return float(np.minimum(a, b).sum())


@dataclass(frozen=True)
class ColorProfile:
    staff: NDArray[np.float64]
    customer: NDArray[np.float64]
    center: float = 0.5  # similitud "neutral" (punto medio entre personal y clientes calibrados)

    def raw_similarity(self, hist: NDArray[np.float64]) -> float:
        s = intersection(hist, self.staff)
        c = intersection(hist, self.customer)
        return 0.5 if s + c <= 1e-9 else s / (s + c)

    def similarity(self, hist: NDArray[np.float64]) -> float:
        """0 = parece cliente, 1 = parece personal, 0.5 = no distingue (centrado en `center`)."""
        raw = self.raw_similarity(hist)
        if raw >= self.center:
            return 0.5 + 0.5 * (raw - self.center) / max(1e-6, 1 - self.center)
        return 0.5 * raw / max(1e-6, self.center)

    def to_json(self) -> dict[str, Any]:
        return {
            "bins": [H_BINS, S_BINS, V_BINS],
            "center": round(self.center, 4),
            "staff": [round(float(x), 6) for x in self.staff],
            "customer": [round(float(x), 6) for x in self.customer],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ColorProfile | None":
        if data.get("bins") != [H_BINS, S_BINS, V_BINS]:
            return None
        return cls(
            np.asarray(data["staff"], dtype=float),
            np.asarray(data["customer"], dtype=float),
            float(data.get("center", 0.5)),
        )


@dataclass(frozen=True)
class Calibration:
    profile: ColorProfile
    threshold: float  # similitud a partir de la cual el color "dice" personal
    separation: float  # d' entre grupos: > 1.5 buena, < 0.8 el color no alcanza
    staff_scores: list[float]
    customer_scores: list[float]
    suggested_color_weight: float

    @property
    def quality(self) -> str:
        if self.separation >= 1.5:
            return "buena"
        if self.separation >= 0.8:
            return "regular"
        return "insuficiente"


def calibrate(
    staff_hists: list[NDArray[np.float64]], customer_hists: list[NDArray[np.float64]]
) -> Calibration:
    if len(staff_hists) < 2 or len(customer_hists) < 2:
        raise ValueError(
            "Marcá al menos 2 personas del personal y 2 clientes (ideal: 3 a 10 de cada uno)"
        )
    raw = ColorProfile(np.mean(staff_hists, axis=0), np.mean(customer_hists, axis=0))
    s_raw = [raw.raw_similarity(h) for h in staff_hists]
    c_raw = [raw.raw_similarity(h) for h in customer_hists]
    spread = (pstdev(s_raw) + pstdev(c_raw)) / 2 or 1e-3
    separation = (fmean(s_raw) - fmean(c_raw)) / spread
    profile = ColorProfile(raw.staff, raw.customer, (fmean(s_raw) + fmean(c_raw)) / 2)
    s_scores = [round(profile.similarity(h), 3) for h in staff_hists]
    c_scores = [round(profile.similarity(h), 3) for h in customer_hists]
    weight = round(min(2.0, max(0.2, separation / 1.5)), 2)
    return Calibration(profile, 0.5, round(separation, 2), s_scores, c_scores, weight)
