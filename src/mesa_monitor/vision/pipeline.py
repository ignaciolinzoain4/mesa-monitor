"""Pipeline de una cámara: frame → detección → tracking → color → personal/cliente → mesas.

Lo usan el worker en vivo y `tools/replay.py`, así el replay prueba exactamente el mismo código.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import supervision as sv
from numpy.typing import NDArray
from PIL import Image

from mesa_monitor.core.color import ColorProfile, hsv_histogram, torso_crop
from mesa_monitor.vision.detector import Detector, Tracker, TrackerParams
from mesa_monitor.vision.scene import (
    Box,
    CameraZonesRT,
    MotionTracker,
    PostureParams,
    SceneResult,
    analyze_frame,
)
from mesa_monitor.vision.staff import HeuristicStaffClassifier, StaffWeights


@dataclass
class PipelineStats:
    frames: int = 0
    last_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    last_people: int = 0
    timestamps: list[float] = field(default_factory=list)

    def record(self, latency_ms: float, people: int) -> None:
        self.frames += 1
        self.last_latency_ms = latency_ms
        self.avg_latency_ms = (
            latency_ms if self.frames == 1 else 0.9 * self.avg_latency_ms + 0.1 * latency_ms
        )
        self.last_people = people
        now = time.monotonic()
        self.timestamps = [t for t in self.timestamps if now - t < 10] + [now]

    @property
    def fps(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        span = self.timestamps[-1] - self.timestamps[0]
        return round((len(self.timestamps) - 1) / span, 2) if span > 0 else 0.0


class CameraPipeline:
    def __init__(
        self,
        camera_id: str,
        zones: CameraZonesRT,
        detector: Detector,
        *,
        fps: float,
        tracker: TrackerParams | None = None,
        weights: StaffWeights | None = None,
        profile: ColorProfile | None = None,
        posture: PostureParams | None = None,
        color_every_s: float = 1.0,
    ) -> None:
        self.camera_id = camera_id
        self.zones = zones
        self.detector = detector
        self.tracker = Tracker(tracker or TrackerParams(), fps)
        self.classifier = HeuristicStaffClassifier(weights, profile)
        self.motion = MotionTracker()
        self.profile = profile
        self.posture = posture
        self.color_every_s = color_every_s
        self._color_at: dict[int, float] = {}
        self.stats = PipelineStats()
        self.last_result: SceneResult | None = None
        self.last_frame: NDArray[np.uint8] | None = None
        self.last_tracked: sv.Detections | None = None

    def reconfigure(
        self, zones: CameraZonesRT, weights: StaffWeights, profile: ColorProfile | None
    ) -> None:
        self.zones = zones
        self.classifier.configure(weights, profile)
        self.profile = profile

    def _posture(self, frame: NDArray[np.uint8]) -> PostureParams:
        h, w = frame.shape[:2]
        base = self.posture or PostureParams()
        # Aspecto real de la imagen: si no se configuró explícitamente (substreams "estirados"
        # como el D1 de Dahua), se usa el del frame.
        aspect = base.frame_aspect if self.posture and self.posture.frame_aspect else w / h
        return PostureParams(
            standing_ratio=base.standing_ratio,
            moving_ratio=base.moving_ratio,
            moving_speed=base.moving_speed,
            motion_window_s=base.motion_window_s,
            frame_aspect=aspect,
        )

    def _colors(
        self, frame: NDArray[np.uint8], tracked: sv.Detections, now: float
    ) -> dict[int, float]:
        if self.profile is None or tracked.tracker_id is None:
            return {}
        out: dict[int, float] = {}
        hsv: NDArray[np.uint8] | None = None
        for xyxy, tid in zip(tracked.xyxy, tracked.tracker_id, strict=True):
            t = int(tid)
            if now - self._color_at.get(t, -1e9) < self.color_every_s:
                continue
            if hsv is None:
                hsv = np.asarray(Image.fromarray(frame).convert("HSV"))
            crop = torso_crop(hsv, xyxy.tolist())
            out[t] = self.profile.similarity(hsv_histogram(crop))
            self._color_at[t] = now
        return out

    def process(self, frame: NDArray[np.uint8], ts: datetime) -> SceneResult:
        t0 = time.perf_counter()
        h, w = frame.shape[:2]
        det = self.detector.detect(frame, self.zones.roi())
        tracked = self.tracker.update(det.persons, timestamp=ts.timestamp())
        colors = self._colors(frame, tracked, ts.timestamp())
        persons = [
            Box(x1 / w, y1 / h, x2 / w, y2 / h, float(c), int(tid))
            for (x1, y1, x2, y2), c, tid in zip(
                tracked.xyxy,
                tracked.confidence if tracked.confidence is not None else np.ones(len(tracked)),
                tracked.tracker_id if tracked.tracker_id is not None else [],
                strict=False,
            )
        ]
        objects = [
            Box(x1 / w, y1 / h, x2 / w, y2 / h, 1.0, None, name)
            for (x1, y1, x2, y2), name in zip(det.objects.xyxy, det.object_names, strict=True)
        ]
        result = analyze_frame(
            self.zones,
            persons,
            objects,
            ts,
            self.classifier,
            self._posture(frame),
            colors,
            motion=self.motion,
        )
        self.stats.record((time.perf_counter() - t0) * 1000, len(persons))
        self.last_result, self.last_frame, self.last_tracked = result, frame, tracked
        return result


def annotate(
    frame: NDArray[np.uint8], zones: CameraZonesRT, result: SceneResult
) -> NDArray[np.uint8]:
    """Overlay de debug: zonas, personas (personal en amarillo, clientes en celeste) y conteos.

    Solo para calibrar (apagado por defecto). No se guarda en disco.
    """
    from PIL import ImageDraw

    img = Image.fromarray(frame)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size

    def px(poly: object) -> list[tuple[float, float]]:
        return [(p[0] * w, p[1] * h) for p in poly]  # type: ignore[attr-defined]

    for m in zones.ignore_masks:
        d.polygon(px(m), fill=(248, 113, 113, 70))
    for z in zones.staff_zones:
        d.polygon(px(z), outline=(251, 191, 36, 255), width=2)
    for v in zones.views:
        d.polygon(px(v.seats_polygon), outline=(52, 211, 153, 255), width=1)
        d.polygon(px(v.table_polygon), outline=(56, 189, 248, 255), width=2)
        counts = result.tables.get(v.table_id)
        if counts:
            x, y = px(v.table_polygon)[0]
            label = f"{v.table_id[-6:]} s:{counts.seated_customers}" + (
                " M" if counts.staff_present else ""
            )
            d.text((x + 2, y + 2), label, fill=(255, 255, 255, 255))
    for p in result.people:
        b = p.box
        color = (250, 204, 21, 255) if p.is_staff else (56, 189, 248, 255)
        d.rectangle((b.x1 * w, b.y1 * h, b.x2 * w, b.y2 * h), outline=color, width=2)
        tag = ("MOZO" if p.is_staff else "cli") + (" parado" if p.standing else "")
        d.text((b.x1 * w + 2, b.y1 * h - 10), f"#{b.track_id} {tag}", fill=color)
    return np.asarray(img)
