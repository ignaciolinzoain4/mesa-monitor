"""Detección con RF-DETR (pesos COCO, Apache-2.0) con mosaicos y región de interés.

Con cámaras gran angular en altura las personas del fondo quedan chicas: el modelo reduce el
frame a su resolución fija (384–704 px) y las pierde. Por eso se recorta primero la región de
interés (lo que cubren las zonas dibujadas) y se la divide en mosaicos solapados que se procesan
por separado (`supervision.InferenceSlicer`). En un video real de un café pasó de detectar 15 a
41 personas, incluido el mozo que la imagen entera no veía.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, cast

import numpy as np
import supervision as sv
from numpy.typing import NDArray

from mesa_monitor.vision.benchmark import MODEL_SIZES

log = logging.getLogger(__name__)

_MODEL_CLASSES = {
    "nano": "RFDETRNano",
    "small": "RFDETRSmall",
    "medium": "RFDETRMedium",
    "large": "RFDETRLarge",
}
# Objetos de mesa (SPEC §6): vajilla y comida de COCO. Indican "consumiendo" con clientes y
# "sucia" cuando se fueron. Configurable.
DEFAULT_OBJECT_CLASSES = (
    "cup",
    "bowl",
    "wine glass",
    "bottle",
    "fork",
    "knife",
    "spoon",
    "sandwich",
    "pizza",
    "donut",
    "cake",
    "hot dog",
    "banana",
    "apple",
    "orange",
    "broccoli",
    "carrot",
)


@dataclass(frozen=True)
class TilingParams:
    enabled: bool = True
    tile_w: int = 560
    tile_h: int = 600
    overlap: int = 128  # horizontal
    # Vertical: más que el alto de una persona del fondo. Las personas de adelante (más altas que
    # el solapamiento) quedan cortadas en dos mosaicos: las completa `full_pass`.
    overlap_h: int = 200
    full_pass: bool = True  # además de los mosaicos, una pasada sobre la región entera
    batch_size: int = 4
    iou_threshold: float = 0.5


@dataclass(frozen=True)
class DetectorParams:
    model: str = "medium"
    device: str | None = None  # None = automático (cuda > mps > cpu)
    person_confidence: float = 0.4
    object_classes: tuple[str, ...] = DEFAULT_OBJECT_CLASSES
    object_confidence: dict[str, float] = field(default_factory=dict)
    # Mínimo global; cada mesa filtra según su tamaño (`scene.CameraZonesRT.object_min_conf`).
    default_object_confidence: float = 0.25
    tiling: TilingParams = field(default_factory=TilingParams)

    def __post_init__(self) -> None:
        if self.model not in MODEL_SIZES:
            raise ValueError(f"Modelo no permitido: {self.model!r} (solo {', '.join(MODEL_SIZES)})")


@dataclass
class FrameDetections:
    persons: sv.Detections  # en píxeles del frame completo
    objects: sv.Detections
    object_names: list[str]


def _empty() -> sv.Detections:
    return sv.Detections.empty()


class Detector:
    def __init__(self, params: DetectorParams) -> None:
        import rfdetr
        from rfdetr.assets.coco_classes import COCO_CLASSES

        self.params = params
        kwargs: dict[str, Any] = {} if params.device is None else {"device": params.device}
        self.model = getattr(rfdetr, _MODEL_CLASSES[params.model])(**kwargs)
        names: dict[int, str] = (
            dict(COCO_CLASSES) if isinstance(COCO_CLASSES, dict) else dict(enumerate(COCO_CLASSES))
        )
        self._names = names
        self._person_id = next(i for i, n in names.items() if n == "person")
        self._object_ids = {i for i, n in names.items() if n in params.object_classes}
        self._min_conf = min(
            [
                params.person_confidence,
                params.default_object_confidence,
                *params.object_confidence.values(),
            ]
        )

    def _predict(self, image: NDArray[np.uint8]) -> sv.Detections:
        det = self.model.predict(image, threshold=self._min_conf)
        return det if isinstance(det, sv.Detections) else _empty()

    def _predict_batch(self, images: list[NDArray[np.uint8]]) -> list[sv.Detections]:
        out = self.model.predict(images, threshold=self._min_conf)
        return list(out) if isinstance(out, list) else [out]

    def detect(
        self, frame: NDArray[np.uint8], roi: tuple[float, float, float, float] | None = None
    ) -> FrameDetections:
        """`frame`: RGB HxWx3. `roi`: rectángulo normalizado a procesar (None = todo)."""
        h, w = frame.shape[:2]
        x0 = y0 = 0
        crop = frame
        if roi is not None:
            x0, y0 = int(roi[0] * w), int(roi[1] * h)
            x1, y1 = max(x0 + 32, int(roi[2] * w)), max(y0 + 32, int(roi[3] * h))
            crop = frame[y0:y1, x0:x1]

        t = self.params.tiling
        ch, cw = crop.shape[:2]
        if t.enabled and (cw > t.tile_w * 1.15 or ch > t.tile_h * 1.15):
            slicer = sv.InferenceSlicer(
                callback=self._predict_batch if t.batch_size > 1 else self._predict,
                slice_wh=(t.tile_w, t.tile_h),
                overlap_wh=(t.overlap, t.overlap_h),
                iou_threshold=t.iou_threshold,
                thread_workers=1,
                batch_size=t.batch_size,
            )
            det = slicer(crop)
            if t.full_pass:
                # Pasada sobre la región entera: las personas grandes (cerca de la cámara) salen
                # completas; las "medias personas" que dejan los bordes de los mosaicos quedan
                # dentro de esas cajas y `suppress_contained` las saca.
                full = self._predict(np.ascontiguousarray(crop))
                det = sv.Detections.merge([full, det]).with_nms(
                    threshold=t.iou_threshold, class_agnostic=False
                )
        else:
            det = self._predict(np.ascontiguousarray(crop))

        if len(det) and (x0 or y0):
            det.xyxy = det.xyxy + np.array([x0, y0, x0, y0], dtype=det.xyxy.dtype)

        if not len(det):
            return FrameDetections(_empty(), _empty(), [])
        cls = det.class_id if det.class_id is not None else np.full(len(det), -1)
        conf = det.confidence if det.confidence is not None else np.ones(len(det))
        persons = suppress_contained(
            cast(
                sv.Detections,
                det[(cls == self._person_id) & (conf >= self.params.person_confidence)],
            )
        )
        obj_mask = np.array(
            [
                c in self._object_ids
                and s
                >= self.params.object_confidence.get(
                    self._names[c], self.params.default_object_confidence
                )
                for c, s in zip(cls, conf, strict=True)
            ],
            dtype=bool,
        )
        objects = cast(sv.Detections, det[obj_mask])
        names = [
            self._names[int(c)] for c in (objects.class_id if objects.class_id is not None else [])
        ]
        return FrameDetections(persons, objects, names)


def suppress_contained(det: sv.Detections, max_inside: float = 0.85) -> sv.Detections:
    """Saca las cajas que están casi enteras dentro de otra más grande.

    Con mosaicos solapados el detector a veces devuelve a la misma persona dos veces (el cuerpo
    entero y solo el torso): eso duplicaba "mozos" y sumaba un cliente fantasma a la mesa.
    """
    if len(det) < 2:
        return det
    xyxy = det.xyxy.astype(float)
    areas = (xyxy[:, 2] - xyxy[:, 0]).clip(0) * (xyxy[:, 3] - xyxy[:, 1]).clip(0)
    order = np.argsort(-areas)
    keep = np.ones(len(det), dtype=bool)
    for rank, i in enumerate(order):
        if areas[i] <= 0:
            keep[i] = False
            continue
        for j in order[:rank]:
            if not keep[j]:
                continue
            ix = min(xyxy[i, 2], xyxy[j, 2]) - max(xyxy[i, 0], xyxy[j, 0])
            iy = min(xyxy[i, 3], xyxy[j, 3]) - max(xyxy[i, 1], xyxy[j, 1])
            if ix > 0 and iy > 0 and ix * iy / areas[i] >= max_inside:
                keep[i] = False
                break
    return det if keep.all() else cast(sv.Detections, det[keep])


@dataclass(frozen=True)
class TrackerParams:
    algorithm: str = "ocsort"  # "ocsort" (default) | "bytetrack" | "sort"
    lost_track_s: float = 3.0
    min_consecutive_frames: int = 2
    min_iou: float = 0.15  # a 2–3 fps la gente se mueve bastante entre frames
    # Confianza mínima para crear un track. El default de la librería (0.6) dejaba sin trackear a
    # la gente chica del fondo (el detector la ve con 0.4–0.6) y sus mesas figuraban libres.
    high_conf: float = 0.4


class Tracker:
    """Tracker por cámara (Roboflow `trackers`, Apache-2.0). Cámaras fijas: sin compensación de
    movimiento (no BoT-SORT). Los ids son efímeros y nunca se persisten (SPEC Anexo A)."""

    def __init__(self, params: TrackerParams, fps: float) -> None:
        import trackers

        # `trackers` mide el buffer en frames de 30 fps: tiempo máximo = buffer / 30.
        buffer = max(1, round(params.lost_track_s * 30))
        common: dict[str, Any] = {"lost_track_buffer": buffer, "frame_rate": fps}
        if params.algorithm == "ocsort":
            self._t: Any = trackers.OCSORTTracker(
                **common,
                minimum_consecutive_frames=params.min_consecutive_frames,
                minimum_iou_threshold=params.min_iou,
                high_conf_det_threshold=params.high_conf,
            )
        elif params.algorithm == "bytetrack":
            self._t = trackers.ByteTrackTracker(
                **common,
                minimum_consecutive_frames=params.min_consecutive_frames,
                track_activation_threshold=params.high_conf,
                high_conf_det_threshold=params.high_conf,
            )
        elif params.algorithm == "sort":
            self._t = trackers.SORTTracker(
                **common, minimum_consecutive_frames=params.min_consecutive_frames
            )
        else:
            raise ValueError(f"Tracker desconocido: {params.algorithm!r}")

    def update(self, persons: sv.Detections, timestamp: float) -> sv.Detections:
        tracked: sv.Detections = self._t.update(persons, timestamp=timestamp)
        if tracked.tracker_id is None or not len(tracked):
            return _empty()
        return cast(sv.Detections, tracked[tracked.tracker_id >= 0])
