import pytest

from mesa_monitor.vision.benchmark import ModelResult, recommend
from mesa_monitor.vision.hardware import detect_hardware


def _results(**ips: float) -> list[ModelResult]:
    return [ModelResult(size=s, ok=True, images_per_s=v, batch_size=4) for s, v in ips.items()]


def test_gpu_picks_large_when_it_fits() -> None:
    rec = recommend(_results(nano=300, small=200, medium=120, large=60), n_cameras=9, device="cuda")
    # 9 cámaras × 3 fps = 27 img/s ≤ 60 × 0.6 = 36
    assert (rec.model, rec.fps_per_camera, rec.sufficient) == ("large", 3.0, True)


def test_gpu_prefers_bigger_model_at_2fps_over_smaller_at_3fps() -> None:
    rec = recommend(_results(medium=120, large=35), n_cameras=11, device="cuda")
    # large: 11×3=33 > 21, 11×2.5=27.5 > 21, 11×2=22 > 21 → no; medium a 3 fps sí.
    assert (rec.model, rec.fps_per_camera) == ("medium", 3.0)
    rec = recommend(_results(medium=120, large=40), n_cameras=11, device="cuda")
    assert (rec.model, rec.fps_per_camera) == ("large", 2.0)  # 22 ≤ 24


def test_cpu_never_recommends_large() -> None:
    rec = recommend(_results(nano=40, small=30, large=100), n_cameras=9, device="cpu")
    assert rec.model == "small"


def test_slow_cpu_degrades_fps_with_warning() -> None:
    rec = recommend(_results(nano=12, small=6), n_cameras=9, device="cpu")
    # 2 fps necesitaría 18 > 7.2; 1 fps = 9 > 7.2 … nano: 1.5 fps = 13.5 > 7.2, 1 fps = 9 > 7.2
    assert rec.model == "nano"
    assert not rec.sufficient
    assert "no alcanza" in rec.warnings[0]
    rec = recommend(_results(nano=20, small=6), n_cameras=9, device="cpu")
    assert (rec.model, rec.fps_per_camera, rec.sufficient) == ("nano", 1.0, True)
    assert "1 fps" in rec.warnings[0]


def test_failed_models_are_ignored() -> None:
    results = [*_results(nano=100), ModelResult(size="small", ok=False, error="sin pesos")]
    assert recommend(results, n_cameras=4, device="cpu").model == "nano"


def test_no_results_raises() -> None:
    with pytest.raises(ValueError, match="Ningún modelo"):
        recommend([ModelResult(size="nano", ok=False)], n_cameras=1, device="cpu")


def test_detect_hardware_runs() -> None:
    hw = detect_hardware()
    assert hw.ram_gb > 0
    assert hw.best_device in {"cuda", "mps", "cpu"}


def test_tiling_multiplies_cost() -> None:
    results = _results(medium=120, large=60)
    assert recommend(results, n_cameras=9, device="cuda").model == "large"
    tiled = recommend(results, n_cameras=9, device="cuda", tiles_per_frame=4)
    # 9 × 3 × 4 = 108 > 72 (medium) … 9 × 2 × 4 = 72 ≤ 72 → medium a 2 fps
    assert (tiled.model, tiled.fps_per_camera) == ("medium", 2.0)
    assert tiled.required_images_per_s == 72
