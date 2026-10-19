"""Pipeline de una cámara: frame → detección → tracking → color → personal/cliente → mesas.

Lo usan el worker en vivo y `tools/replay.py`, así el replay prueba exactamente el mismo código.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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


# Mismos colores que el dashboard y /alertas.
STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "LIBRE": (22, 101, 52),
    "OCUPADA_SIN_ATENDER": (180, 83, 9),
    "ATENDIDA": (29, 78, 216),
    "DESOCUPADA_SUCIA": (126, 34, 206),
    "SIN_VISION": (71, 85, 105),
    "DESHABILITADA": (30, 41, 59),
}
STATE_NAMES = {
    "LIBRE": "Libre",
    "OCUPADA_SIN_ATENDER": "Sin atender",
    "ATENDIDA": "Atendida",
    "DESOCUPADA_SUCIA": "Sucia",
    "SIN_VISION": "Sin visión",
    "DESHABILITADA": "Deshabilitada",
}


@dataclass(frozen=True)
class TableOverlay:
    """Estado de una mesa para dibujar (lo que sale del state engine)."""

    state: str
    elapsed_s: float | None
    people: int


def annotate(
    frame: NDArray[np.uint8],
    zones: CameraZonesRT,
    result: SceneResult,
    labels: dict[str, str] | None = None,
    states: dict[str, TableOverlay] | None = None,
    clock: str | None = None,
) -> NDArray[np.uint8]:
    """Overlay de debug: zonas, personas (personal en amarillo, clientes en celeste) y, si se
    pasan `states`, cada mesa pintada con su estado, cronómetro y personas.

    Solo para calibrar y para el replay (apagado por defecto). En vivo no se guarda en disco.
    """
    from PIL import ImageDraw, ImageFont

    img = Image.fromarray(frame)
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    scale = max(1.0, h / 720)
    font = ImageFont.load_default(size=round(13 * scale))
    font_big = ImageFont.load_default(size=round(16 * scale))
    top = 36 * scale if states is not None else 4.0
    placed: list[tuple[float, float, float, float]] = []

    def px(poly: object) -> list[tuple[float, float]]:
        return [(p[0] * w, p[1] * h) for p in poly]  # type: ignore[attr-defined]

    def label_box(x: float, y: float, text: str, fill: tuple[int, int, int, int], f: Any) -> None:
        """Etiqueta con fondo; se corre para no salirse de la imagen ni pisar otra."""
        bb = d.textbbox((0, 0), text, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = min(max(4.0, x), w - tw - 8)
        y = max(top, y)
        for _ in range(12):
            box = (x - 3, y - 2, x + tw + 3, y + th + 6)
            hit = next(
                (
                    b
                    for b in placed
                    if box[0] < b[2] and box[2] > b[0] and box[1] < b[3] and box[3] > b[1]
                ),
                None,
            )
            if hit is None:
                break
            y = hit[1] - th - 10  # subir por encima de la que molesta
            if y < top:
                y = hit[3] + 2  # sin lugar arriba: abajo
        box = (x - 3, y - 2, x + tw + 3, y + th + 6)
        placed.append(box)
        d.rectangle(box, fill=fill)
        d.text((x, y), text, fill=(255, 255, 255, 255), font=f)

    for m in zones.ignore_masks:
        d.polygon(px(m), fill=(248, 113, 113, 60))
    for z in zones.staff_zones:
        d.polygon(px(z), outline=(251, 191, 36, 255), width=2)
    for v in zones.views:
        st = (states or {}).get(v.table_id)
        color = STATE_COLORS.get(st.state, (56, 189, 248)) if st else (56, 189, 248)
        d.polygon(px(v.seats_polygon), outline=(*color, 200), width=1)
        d.polygon(
            px(v.table_polygon), fill=(*color, 90) if st else None, outline=(*color, 255), width=3
        )
    for p in result.people:
        b = p.box
        box_xy = (b.x1 * w, b.y1 * h, b.x2 * w, b.y2 * h)
        if p.is_staff:
            d.rectangle(box_xy, outline=(250, 204, 21, 255), width=3)
        else:  # clientes: discretos, para que resalten mesas y personal
            d.rectangle(box_xy, outline=(186, 230, 253, 110), width=1)
    for v in zones.views:  # etiquetas al final: siempre por encima de las cajas
        st = (states or {}).get(v.table_id)
        name = (labels or {}).get(v.table_id, v.table_id)
        xs = [q[0] for q in px(v.table_polygon)]
        ys = [q[1] for q in px(v.table_polygon)]
        x, y = min(xs), min(ys) - 22 * scale
        if st:
            color = STATE_COLORS.get(st.state, (56, 189, 248))
            timer = ""
            if st.elapsed_s is not None and st.state not in ("LIBRE", "DESHABILITADA"):
                mins, secs = divmod(int(st.elapsed_s), 60)
                timer = f" {mins}:{secs:02d}"
            state_name = STATE_NAMES.get(st.state, st.state)
            label_box(
                x, y, f"{name} · {state_name}{timer} · {st.people} pers.", (*color, 230), font_big
            )
        else:
            counts = result.tables.get(v.table_id)
            seated = counts.seated_customers if counts else 0
            label_box(x, y, f"{name} s:{seated}", (15, 23, 42, 200), font)
    for p in result.people:
        if p.is_staff:
            label_box(p.box.x1 * w, p.box.y1 * h - 18 * scale, "MOZO", (161, 98, 7, 230), font)
    if states is not None:
        d.rectangle((0, 0, w, 30 * scale), fill=(2, 6, 23, 210))
        x = 8.0
        if clock:
            d.text((x, 6 * scale), clock, fill=(255, 255, 255, 255), font=font_big)
            x += d.textlength(clock, font=font_big) + 16 * scale
        for key in ("LIBRE", "OCUPADA_SIN_ATENDER", "ATENDIDA", "DESOCUPADA_SUCIA"):
            d.rectangle((x, 9 * scale, x + 12 * scale, 21 * scale), fill=(*STATE_COLORS[key], 255))
            d.text(
                (x + 16 * scale, 7 * scale), STATE_NAMES[key], fill=(226, 232, 240, 255), font=font
            )
            x += 16 * scale + d.textlength(STATE_NAMES[key], font=font) + 14 * scale
        d.rectangle(
            (x, 9 * scale, x + 12 * scale, 21 * scale), outline=(250, 204, 21, 255), width=2
        )
        d.text((x + 16 * scale, 7 * scale), "Mozo", fill=(226, 232, 240, 255), font=font)
    return np.asarray(img)
