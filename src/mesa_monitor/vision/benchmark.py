"""Benchmark de RF-DETR y recomendación de modelo + fps por cámara (SPEC §16).

`recommend()` es pura (testeable sin torch). `measure_model()` necesita el extra `vision`.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Solo tamaños con pesos Apache-2.0 (XL/2XL son PML: prohibidos por SPEC §3.2).
MODEL_SIZES = ("nano", "small", "medium", "large")
_MODEL_CLASSES = {
    "nano": "RFDETRNano",
    "small": "RFDETRSmall",
    "medium": "RFDETRMedium",
    "large": "RFDETRLarge",
}
# Preferencia por dispositivo: con GPU, modelos grandes; en CPU, Small/Nano (SPEC §16).
PREFERENCE = {
    "cuda": ("large", "medium", "small", "nano"),
    "mps": ("medium", "small", "nano"),
    "cpu": ("small", "nano"),
}
GOOD_FPS = (3.0, 2.5, 2.0)
DEGRADED_FPS = (1.5, 1.0)


@dataclass
class ModelResult:
    size: str
    ok: bool
    images_per_s: float = 0.0
    batch_size: int = 1
    latency_ms: float | None = None
    error: str | None = None


@dataclass
class Recommendation:
    model: str
    fps_per_camera: float
    device: str
    n_cameras: int
    required_images_per_s: float
    capacity_images_per_s: float
    utilization: float
    batch_size: int
    sufficient: bool
    tiles_per_frame: float = 1.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommend(
    results: Sequence[ModelResult],
    *,
    n_cameras: int,
    device: str,
    target_fps: float = 3.0,
    headroom: float = 0.6,
    tiles_per_frame: float = 1.0,
) -> Recommendation:
    """Elige el modelo más preciso que sostenga ≥ 2 fps en todas las cámaras.

    `headroom` es la fracción de la capacidad medida que se usa para detección: el resto queda
    para decodificar video, tracking, clasificación y picos.
    `tiles_per_frame`: con detección por mosaicos cada frame cuesta varias inferencias. Con
    cámaras gran angular en altura hace falta (en un video real pasó de 15 a 41 personas).
    """
    if n_cameras < 1:
        raise ValueError("n_cameras debe ser ≥ 1")
    by_size = {r.size: r for r in results if r.ok and r.images_per_s > 0}
    prefs = [s for s in PREFERENCE.get(device, PREFERENCE["cpu"]) if s in by_size]
    if not prefs:
        prefs = [s for s in MODEL_SIZES if s in by_size]
    if not prefs:
        raise ValueError("Ningún modelo se pudo medir: revisá los errores del benchmark.")

    good = tuple(f for f in GOOD_FPS if f <= target_fps) or (target_fps,)

    def build(size: str, fps: float, warnings: list[str]) -> Recommendation:
        r = by_size[size]
        required = n_cameras * fps * tiles_per_frame
        return Recommendation(
            model=size,
            fps_per_camera=fps,
            device=device,
            n_cameras=n_cameras,
            required_images_per_s=round(required, 2),
            capacity_images_per_s=round(r.images_per_s, 2),
            utilization=round(required / r.images_per_s, 2),
            batch_size=r.batch_size,
            sufficient=required <= r.images_per_s * headroom,
            tiles_per_frame=tiles_per_frame,
            warnings=warnings,
        )

    def fits(size: str, fps: float) -> bool:
        return n_cameras * fps * tiles_per_frame <= by_size[size].images_per_s * headroom

    for size in prefs:
        for fps in good:
            if fits(size, fps):
                return build(size, fps, [])

    cheapest = sorted(by_size, key=lambda s: by_size[s].images_per_s, reverse=True)[0]
    for fps in DEGRADED_FPS:
        if fits(cheapest, fps):
            return build(
                cheapest,
                fps,
                [
                    f"Con este equipo solo se sostienen {fps:g} fps por cámara. Funciona, pero las "
                    "visitas cortas de los mozos se pueden perder. Conviene una GPU NVIDIA."
                ],
            )
    rec = build(cheapest, DEGRADED_FPS[-1], [])
    rec.warnings.append(
        f"El equipo no alcanza para {n_cameras} cámaras ni a 1 fps con el modelo más liviano. "
        "Reducí cámaras, usá una GPU NVIDIA o exportá el modelo a ONNX/TensorRT."
    )
    return rec


def measure_model(
    size: str,
    frames: Sequence[Any],
    *,
    device: str | None = None,
    batch_sizes: Sequence[int] = (1, 4),
    min_seconds: float = 5.0,
    threshold: float = 0.4,
) -> ModelResult:
    """Mide imágenes/s de un tamaño de RF-DETR. `frames`: arrays RGB HxWx3 o PIL.Image."""
    try:
        import rfdetr

        cls = getattr(rfdetr, _MODEL_CLASSES[size])
        kwargs: dict[str, Any] = {} if device is None else {"device": device}
        model = cls(**kwargs)
    except Exception as exc:
        return ModelResult(size=size, ok=False, error=f"No se pudo cargar: {exc}")

    best = ModelResult(size=size, ok=False, error="sin mediciones")
    for bs in batch_sizes:
        batch = [frames[i % len(frames)] for i in range(bs)]
        try:
            for _ in range(2):  # warmup
                model.predict(batch if bs > 1 else batch[0], threshold=threshold)
            n_calls = 0
            start = time.perf_counter()
            while True:
                model.predict(batch if bs > 1 else batch[0], threshold=threshold)
                n_calls += 1
                elapsed = time.perf_counter() - start
                if elapsed >= min_seconds and n_calls >= 3:
                    break
        except Exception as exc:
            log.warning("RF-DETR %s con batch %d falló: %s", size, bs, exc)
            if not best.ok:
                best.error = str(exc)
            continue
        ips = bs * n_calls / elapsed
        if ips > best.images_per_s:
            best = ModelResult(
                size=size,
                ok=True,
                images_per_s=round(ips, 2),
                batch_size=bs,
                latency_ms=round(1000 * elapsed / n_calls, 1),
            )
    return best
