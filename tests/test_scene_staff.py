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
    assert res.tables["t1"] == TableCounts(
        seated_customers=2, staff_present=False, object_count=1, present_customers=2
    )
    # t2 está en modo "solo visita": no cuenta objetos
    assert res.tables["t2"].object_count is None
    assert len(res.people) == 2  # la persona dentro de la máscara se descarta


def test_waiter_becomes_staff_and_is_detected_near_table() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=2))
    results = []
    for i in range(10):  # el mozo trabaja en la barra y va a la mesa t1, parado
        ts = T0 + timedelta(seconds=i)
        pos = standing(0.1, 0.9, 7) if i < 5 else standing(0.36, 0.42, 7)
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


def f(
    i: float,
    standing: bool,
    zone: bool = False,
    table: str | None = None,
    tid: int = 1,
    color: float | None = None,
    xy: tuple[float, float] | None = None,
) -> PersonFeatures:
    x, y = xy or (None, None)
    return PersonFeatures(tid, T0 + timedelta(seconds=i), standing, table, zone, color, x, y)


def test_classifier_hysteresis_requires_consecutive_frames() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=3))
    # en la barra 3 s (permanencia) → puntaje alto; 3 frames seguidos para confirmar
    out = [clf.classify(f(i, True, zone=True)) for i in range(6)]
    assert out == [False, False, False, False, False, True]
    # un frame sentado (fuera de una mesa) no lo vuelve cliente
    assert clf.classify(f(6, False)) is True


def test_long_seated_person_is_customer() -> None:
    clf = HeuristicStaffClassifier(StaffWeights(confirm_frames=1))
    for i in range(0, 200, 5):
        out = clf.classify(f(i, False, table="t1", tid=2, color=0.9))
    assert out is False


def test_customer_passing_or_paying_at_the_bar_is_not_staff() -> None:
    clf = HeuristicStaffClassifier()
    # pasa por la barra 2 s: no llega a la permanencia
    assert not any(clf.classify(f(i, True, zone=True, tid=3)) for i in range(3))
    # se queda pagando, con ropa clara: la barra sola no alcanza
    assert not any(clf.classify(f(i, True, zone=True, tid=4, color=0.0)) for i in range(30))


def test_seated_customer_never_becomes_staff_even_with_dark_clothes() -> None:
    clf = HeuristicStaffClassifier()
    for i in range(10):
        clf.classify(f(i, False, table="t1", tid=5, color=1.0))
    # la postura lo da "parado" un rato (se acomoda, gesticula) en su mesa: sigue siendo cliente
    out = [clf.classify(f(10 + i, True, table="t1", tid=5, color=1.0)) for i in range(20)]
    assert not any(out)


def test_waiter_keeps_label_after_leaving_the_bar() -> None:
    clf = HeuristicStaffClassifier()
    for i in range(6):
        clf.classify(f(i, True, zone=True, tid=6, color=1.0))
    # sale de la barra y camina por el salón (parado, sin mesa fija)
    assert all(clf.classify(f(6 + i, True, tid=6, color=1.0)) for i in range(60))


def test_new_track_inherits_waiter_after_identity_switch() -> None:
    clf = HeuristicStaffClassifier()
    for i in range(6):
        clf.begin_frame({6}, T0 + timedelta(seconds=i))
        clf.classify(f(i, True, zone=True, tid=6, color=1.0, xy=(0.10, 0.50)))
    # el tracker lo pierde y aparece como track 9 al lado, 1 s después
    clf.begin_frame({9}, T0 + timedelta(seconds=7))
    assert clf.classify(f(7, True, tid=9, color=1.0, xy=(0.12, 0.52)))
    # un track nuevo lejos no hereda nada
    clf.begin_frame({9, 11}, T0 + timedelta(seconds=8))
    assert not clf.classify(f(8, True, tid=11, xy=(0.7, 0.7)))


def test_dark_customer_walking_to_a_table_is_not_staff() -> None:
    clf = HeuristicStaffClassifier()
    out = [clf.classify(f(i, True, tid=12, color=1.0)) for i in range(15)]
    assert not any(out)


def test_tracks_survive_short_gaps_and_are_forgotten_later() -> None:
    clf = HeuristicStaffClassifier()
    for i in range(6):
        clf.classify(f(i, True, zone=True, tid=5))
    # el detector lo pierde 2 s: conserva la clasificación de personal
    clf.forget(set(), T0 + timedelta(seconds=7))
    assert clf.classify(f(8, True, zone=True, tid=5))
    clf.forget(set(), T0 + timedelta(seconds=30))
    assert clf.debug_score(5) is None


def test_staff_between_two_tables_visits_only_the_nearest() -> None:
    zones = CameraZonesRT(
        camera_id="cam1",
        views=[
            ViewZone("a", SQ(0.30, 0.40, 0.40, 0.50), SQ(0.25, 0.35, 0.45, 0.60)),
            ViewZone("b", SQ(0.55, 0.40, 0.65, 0.50), SQ(0.48, 0.35, 0.68, 0.60)),
        ],
    )
    assert set(zones.near_table(0.46, 0.55)) == {"a", "b"}
    assert zones.nearest_table(0.46, 0.55) == "a"


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


def test_still_customer_seen_as_standing_never_becomes_staff() -> None:
    """Con la cámara alta, una clienta sentada de costado se ve "alargada" (parada) todo el
    tiempo: quieta en su silla y con ropa oscura no puede terminar como mozo."""
    clf = HeuristicStaffClassifier()
    out = [
        clf.classify(f(i / 3, True, table="t1", tid=20, color=0.9, xy=(0.41, 0.55)))
        for i in range(3 * 120)
    ]
    assert not any(out)


def test_waiter_walking_the_floor_is_recognized_without_the_bar() -> None:
    """Track nuevo (sin historia en la barra): de pie, con ropa oscura y recorriendo el salón."""
    clf = HeuristicStaffClassifier()
    out = [
        clf.classify(f(i / 3, True, tid=21, color=1.0, xy=(0.2 + i * 0.003, 0.6)))
        for i in range(3 * 20)
    ]
    assert out[-1] is True
    assert not any(out[: 3 * 10])  # no antes de llevar un rato de pie


def test_track_id_switch_does_not_carry_the_customer_lock() -> None:
    """El tracker le pasa el id de un cliente sentado al mozo que camina al lado."""
    clf = HeuristicStaffClassifier()
    for i in range(30):
        clf.classify(f(i / 3, False, table="t1", tid=15, color=1.0, xy=(0.55, 0.74)))
    out = [
        clf.classify(f(10 + i / 3, True, tid=15, color=1.0, xy=(0.40 + i * 0.003, 0.6)))
        for i in range(3 * 20)
    ]
    assert out[-1] is True
