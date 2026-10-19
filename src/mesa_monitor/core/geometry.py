"""Geometría de polígonos en coordenadas normalizadas 0–1 (pura, sin dependencias)."""

from collections.abc import Sequence

Point = tuple[float, float]
Polygon = Sequence[Sequence[float]]

MIN_POINTS = 3
MAX_POINTS = 64


class InvalidPolygonError(ValueError):
    pass


def validate_polygon(poly: Polygon) -> list[list[float]]:
    """Normaliza y valida: ≥ 3 puntos, coordenadas en [0, 1], área no nula."""
    if not MIN_POINTS <= len(poly) <= MAX_POINTS:
        raise InvalidPolygonError(f"un polígono necesita entre {MIN_POINTS} y {MAX_POINTS} puntos")
    out: list[list[float]] = []
    for p in poly:
        if len(p) != 2:
            raise InvalidPolygonError("cada punto es [x, y]")
        x, y = float(p[0]), float(p[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise InvalidPolygonError("las coordenadas van de 0 a 1 (proporción de la imagen)")
        out.append([round(x, 5), round(y, 5)])
    if area(out) < 1e-6:
        raise InvalidPolygonError("el polígono no tiene área (puntos alineados o repetidos)")
    return out


def area(poly: Polygon) -> float:
    """Área (fórmula del zapatero), siempre positiva."""
    n = len(poly)
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def centroid(poly: Polygon) -> Point:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def contains(poly: Polygon, x: float, y: float) -> bool:
    """Punto dentro del polígono (ray casting). Los bordes cuentan como dentro."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if _on_segment(x, y, x1, y1, x2, y2):
            return True
        if (y1 > y) != (y2 > y):
            x_cross = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < x_cross:
                inside = not inside
    return inside


def _on_segment(px: float, py: float, x1: float, y1: float, x2: float, y2: float) -> bool:
    cross = (px - x1) * (y2 - y1) - (py - y1) * (x2 - x1)
    if abs(cross) > 1e-9:
        return False
    return (
        min(x1, x2) - 1e-9 <= px <= max(x1, x2) + 1e-9
        and min(y1, y2) - 1e-9 <= py <= max(y1, y2) + 1e-9
    )


def expand(poly: Polygon, factor: float) -> list[list[float]]:
    """Agranda el polígono desde su centroide (`factor` 1.2 = 20 % más grande), recortado a [0, 1].

    Se usa para el "polígono de asientos ampliado" donde se detectan las visitas del personal.
    """
    cx, cy = centroid(poly)
    return [
        [
            min(1.0, max(0.0, cx + (p[0] - cx) * factor)),
            min(1.0, max(0.0, cy + (p[1] - cy) * factor)),
        ]
        for p in poly
    ]


def bbox(poly: Polygon) -> tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def box_overlap_fraction(
    box: tuple[float, float, float, float], region: tuple[float, float, float, float]
) -> float:
    """Fracción del área de `box` que cae dentro de `region` (ambos x1, y1, x2, y2)."""
    ix1, iy1 = max(box[0], region[0]), max(box[1], region[1])
    ix2, iy2 = min(box[2], region[2]), min(box[3], region[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    box_area = max(1e-12, (box[2] - box[0]) * (box[3] - box[1]))
    return inter / box_area
