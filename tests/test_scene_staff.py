"""Análisis de escena, clasificador de personal y perfil de color (sin modelos)."""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from mesa_monitor.core.color import calibrate, hsv_histogram
from mesa_monitor.core.enums import CleanMode
from mesa_monitor.vision.scene import (
    Box,
    CameraTableObs,
    CameraZonesRT,
    PostureParams,
    TableCounts,
    ViewZone,
    analyze_frame,
    fuse,
    is_standing,
)
from mesa_monitor.vision.staff import HeuristicStaffClassifier, PersonFeatures, StaffWeights

T0 = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
SQ = lambda x1, y1, x2, y2: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]  # noqa: E731

ZONES = CameraZonesRT(
    camera_id="cam1",
    views=[
        ViewZone("t1", SQ(0.10, 0.10, 0.30, 0.30), SQ(0.05, 0.05, 0.35, 0.40)),
        ViewZone(
            "t2",
            SQ(0.60, 0.60, 0.80, 0.80),
            SQ(0.55, 0.55, 0.85, 0.90),
            clean_mode=CleanMode.STAFF_VISIT_ONLY,
        ),
    ],
    staff_zones=[SQ(0.0, 0.8, 0.2, 1.0)],
    ignore_masks=[SQ(0.9, 0.0, 1.0, 0.1)],
)
POSTURE = PostureParams(standing_ratio=1.6, frame_aspect=1.0)


def seated(cx: float, y2: float, tid: int) -> Box:
    return Box(cx - 0.05, y2 - 0.06, cx + 0.05, y2, track_id=tid)  # alto/ancho 0.6 → sentado


def standing(cx: float, y2: float, tid: int) -> Box:
    return Box(cx - 0.03, y2 - 0.2, cx + 0.03, y2, track_id=tid)  # alto/ancho 3.3 → parado


def test_posture_corrects_for_frame_aspect() -> None:
    b = Box(0.4, 0.4, 0.5, 0.55)  # 0.15 / 0.10 en coordenadas normalizadas
    assert is_standing(b, PostureParams(standing_ratio=1.4, frame_aspect=1.0))
    # en un frame 16:9 la caja es 1.78 veces más ancha en píxeles → sentado
    assert not is_standing(b, PostureParams(standing_ratio=1.4, frame_aspect=16 / 9))


def test_roi_covers_zones() -> None:
    roi = ZONES.roi(margin=0)
    assert roi is not None
    assert roi[0] == pytest.approx(0.0)
    assert roi[3] == pytest.approx(1.0)


def test_counts_seated_customers_objects_and_ignores_masks() -> None:
    clf = HeuristicStaffClassifier()
    res = analyze_frame(
        ZONES,
        persons=[seated(0.15, 0.35, 1), seated(0.25, 0.35, 2), seated(0.95, 0.08, 3)],
        objects=[Box(0.15, 0.15, 0.18, 0.18, label="cup"), Box(0.5, 0.5, 0.52, 0.52, label="cup")],
        ts=T0,
        classifier=clf,
        posture=POSTURE,
    )
    assert res.tables["t1"] == TableCounts(seated_customers=2, staff_present=False, object_count=1)
    # t2 está en modo "solo visita": no cuenta objetos
    assert res.tables["t2"].object_count is None
    assert len(res.people) == 2  # la persona dentro de la máscara se descarta


def test_waiter_becomes_staff_and_is_detected_near_table() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=2))
    results = []
    for i in range(6):  # el mozo pasa por la barra y va a la mesa t1, parado
        ts = T0 + timedelta(seconds=i)
        pos = standing(0.1, 0.9, 7) if i < 2 else standing(0.36, 0.42, 7)
        results.append(analyze_frame(ZONES, [pos, seated(0.15, 0.35, 1)], [], ts, clf, POSTURE))
    last = results[-1]
    assert last.tables["t1"].staff_present is True
    assert last.tables["t1"].seated_customers == 1  # el cliente sigue contado, el mozo no


def test_standing_customer_is_not_staff_without_other_evidence() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=2, threshold=1.5))
    for i in range(10):
        r = analyze_frame(
            ZONES, [standing(0.36, 0.42, 9)], [], T0 + timedelta(seconds=i), clf, POSTURE
        )
    assert r.tables["t1"].staff_present is False


def test_classifier_hysteresis_requires_consecutive_frames() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=3, threshold=1.0))

    def f(i: int, standing: bool, zone: bool) -> PersonFeatures:
        return PersonFeatures(1, T0 + timedelta(seconds=i), standing, None, zone, None)

    assert clf.classify(f(0, True, True)) is False
    assert clf.classify(f(1, True, True)) is False
    assert clf.classify(f(2, True, True)) is True
    # un frame sentado no lo vuelve cliente (histéresis + memoria de la zona de staff)
    assert clf.classify(f(3, False, False)) is True


def test_long_seated_person_is_customer() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=1))
    for i in range(0, 200, 5):
        out = clf.classify(PersonFeatures(2, T0 + timedelta(seconds=i), False, "t1", False, 0.9))
    assert out is False


def test_tracks_are_forgotten() -> None:
    clf = HeuristicStaffClassifier()
    clf.classify(PersonFeatures(5, T0, True, None, False, None))
    clf.forget(set())
    assert clf.debug_score(5) is None


def test_fuse_prefers_highest_priority_healthy_view() -> None:
    a = CameraTableObs("cam1", 5, False, TableCounts(9, False, 0))
    b = CameraTableObs("cam2", 3, True, TableCounts(2, True, 1))
    c = CameraTableObs("cam3", 1, True, TableCounts(4, False, 0))
    obs = fuse("t1", T0, [a, b, c])
    assert (obs.seated_customers, obs.staff_present, obs.view_healthy) == (2, True, True)
    assert fuse("t1", T0, [a]).view_healthy is False


def _hsv_patch(h: int, s: int, v: int, n: int = 400) -> np.ndarray:
    rng = np.random.default_rng(h + s + v)
    base = np.array([h, s, v], dtype=np.int32)
    noise = rng.integers(-10, 10, size=(n, 3))
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def test_color_calibration_black_vs_colorful() -> None:
    staff = [hsv_histogram(_hsv_patch(0, 20, 25 + i)) for i in range(4)]  # ropa negra
    customers = [hsv_histogram(_hsv_patch(h, 180, 200)) for h in (20, 90, 150, 220)]
    cal = calibrate(staff, customers)
    assert cal.quality == "buena"
    assert cal.profile.similarity(hsv_histogram(_hsv_patch(0, 15, 30))) > cal.threshold
    assert cal.profile.similarity(hsv_histogram(_hsv_patch(120, 170, 190))) < cal.threshold
    restored = type(cal.profile).from_json(cal.profile.to_json())
    assert restored is not None


def test_calibration_needs_samples() -> None:
    with pytest.raises(ValueError, match="al menos 2"):
        calibrate([hsv_histogram(_hsv_patch(0, 0, 0))], [])
