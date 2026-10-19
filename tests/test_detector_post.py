"""Post-proceso de detecciones (sin modelo)."""

import numpy as np
import supervision as sv

from mesa_monitor.vision.detector import suppress_contained


def dets(*boxes: tuple[float, float, float, float]) -> sv.Detections:
    return sv.Detections(
        xyxy=np.array(boxes, dtype=float),
        confidence=np.full(len(boxes), 0.9),
        class_id=np.zeros(len(boxes), dtype=int),
    )


def test_half_person_inside_full_box_is_removed() -> None:
    full = (100, 100, 160, 400)
    lower_half = (102, 260, 158, 398)  # lo que deja el borde de un mosaico
    other = (300, 100, 360, 400)
    out = suppress_contained(dets(full, lower_half, other))
    assert sorted(map(tuple, out.xyxy.tolist())) == sorted([full, other])


def test_people_side_by_side_are_kept() -> None:
    a, b = (100, 100, 160, 400), (140, 120, 200, 410)  # se pisan un poco: dos personas
    assert len(suppress_contained(dets(a, b))) == 2
