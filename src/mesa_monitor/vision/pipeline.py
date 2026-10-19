"""Pipeline de una cámara: frame → detección → tracking → color → personal/cliente → mesas.

Lo usan el worker en vivo y `tools/replay.py`, así el replay prueba exactamente el mismo código.
"""

import time
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
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
from mesa_monitor.vision.staff import HeuristicStaffClassifier, StaffWeights, dark_similarity


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
        return replace(base, frame_aspect=aspect)

    def _colors(
        self, frame: NDArray[np.uint8], tracked: sv.Detections, now: float
    ) -> dict[int, float]:
        """Similitud del torso con el personal. Sin perfil calibrado: ropa oscura."""
        if tracked.tracker_id is None:
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
            out[t] = (
                self.profile.similarity(hsv_histogram(crop))
                if self.profile is not None
                else dark_similarity(crop)
            )
            self._color_at[t] = now
        if len(self._color_at) > 2000:  # tracks viejos (la memoria no crece en jornadas largas)
            self._color_at = {k: v for k, v in self._color_at.items() if now - v < 60}
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
        obj_conf = (
            det.objects.confidence
            if det.objects.confidence is not None
            else np.ones(len(det.objects))
        )
        objects = [
            Box(x1 / w, y1 / h, x2 / w, y2 / h, float(c), None, name)
            for (x1, y1, x2, y2), name, c in zip(
                det.objects.xyxy, det.object_names, obj_conf, strict=True
            )
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


# Mismos colores que el dashboard y /alertas (dashboard/src/lib/colors.ts).
STATE_COLORS: dict[str, tuple[int, int, int]] = {
    "LIBRE": (22, 163, 74),
    "OCUPADA_SIN_ATENDER": (220, 38, 38),
    "ATENDIDA": (29, 78, 216),
    "CONSUMIENDO": (8, 145, 178),
    "DESOCUPADA_SUCIA": (168, 85, 247),
    "SIN_VISION": (71, 85, 105),
    "DESHABILITADA": (30, 41, 59),
    "VERIFICANDO": (100, 116, 139),  # solo overlay: arranque, validando el estado inicial
}
STATE_NAMES = {
    "LIBRE": "Libre",
    "OCUPADA_SIN_ATENDER": "Sin atender",
    "ATENDIDA": "Atendidos",
    "CONSUMIENDO": "Consumiendo",
    "DESOCUPADA_SUCIA": "Sucia",
    "SIN_VISION": "Sin visión",
    "DESHABILITADA": "Deshabilitada",
    "VERIFICANDO": "Verificando",
}
LEGEND = ("LIBRE", "OCUPADA_SIN_ATENDER", "ATENDIDA", "CONSUMIENDO", "DESOCUPADA_SUCIA")
STAFF_COLOR = (250, 204, 21)


@dataclass(frozen=True)
class TableOverlay:
    """Estado de una mesa para dibujar (lo que sale del state engine)."""

    state: str
    elapsed_s: float | None
    people: int
    label: str | None = None  # número o nombre visible de la mesa


@dataclass(frozen=True)
class OverlayAlert:
    """Tarjeta de aviso (ej. "Mesa 10 · Sin atender hace 6 min")."""

    title: str
    detail: str
    critical: bool = False


_FONT_CANDIDATES = {
    # (archivo, índice dentro de la colección) — el primero que exista; si no, el de Pillow.
    "bold": [
        ("DejaVuSans-Bold.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 1),
        ("C:/Windows/Fonts/segoeuib.ttf", 0),
        ("arialbd.ttf", 0),
    ],
    "regular": [
        ("DejaVuSans.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
        ("/System/Library/Fonts/HelveticaNeue.ttc", 10),
        ("C:/Windows/Fonts/segoeui.ttf", 0),
        ("arial.ttf", 0),
    ],
}


@lru_cache(maxsize=32)
def _font(size: int, weight: str = "regular") -> Any:
    from PIL import ImageFont

    for path, index in _FONT_CANDIDATES[weight]:
        try:
            return ImageFont.truetype(path, size, index=index)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _fmt_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def annotate(
    frame: NDArray[np.uint8],
    zones: CameraZonesRT,
    result: SceneResult,
    labels: dict[str, str] | None = None,
    states: dict[str, TableOverlay] | None = None,
    clock: str | None = None,
    alerts: list[OverlayAlert] | None = None,
    layout: dict[str, tuple[float, float]] | None = None,
) -> NDArray[np.uint8]:
    """Overlay para el replay y la calibración (en vivo no se guarda en disco).

    `layout` (opcional, se reutiliza entre frames) guarda dónde quedó cada etiqueta: así no
    saltan de lugar cuando cambia el ancho de otra.

    Diseño limpio: cada mesa con un relleno suave de su color de estado y una etiqueta tipo
    píldora (número, estado, cronómetro, personas); el personal con un marcador chico sobre la
    cabeza y una elipse en el piso. Sin cajas de clientes ni contornos de asientos o zonas: eso
    es para calibrar (`states=None` dibuja las zonas para revisarlas).
    """
    from PIL import ImageDraw

    base = Image.fromarray(frame).convert("RGBA")
    w, h = base.size
    k = max(1.0, h / 720)  # escala de tamaños
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    placed: list[tuple[float, float, float, float]] = []

    def px(poly: Any) -> list[tuple[float, float]]:
        return [(p[0] * w, p[1] * h) for p in poly]

    def overlaps(b: tuple[float, float, float, float]) -> bool:
        return any(b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1] for o in placed)

    def overlap_area(b: tuple[float, float, float, float]) -> float:
        return sum(
            max(0.0, min(b[2], o[2]) - max(b[0], o[0]))
            * max(0.0, min(b[3], o[3]) - max(b[1], o[1]))
            for o in placed
        )

    def place(key: str, cx: float, top: float, bw: float, bh: float) -> tuple[float, float]:
        """Ubica una caja cerca de (`cx`, `top`) sin pisar otra ni salirse de la imagen. Prueba
        primero el lugar del frame anterior, después corrimientos verticales y horizontales; si
        no hay lugar libre, el que menos pisa."""

        def clamp(x: float, y: float) -> tuple[float, float]:
            return min(max(4.0, x), w - bw - 4), min(max(4.0, y), h - bh - 4)

        candidates: list[tuple[float, float]] = []
        if layout is not None and key in layout:
            candidates.append(clamp(*layout[key]))
        step = bh + 3 * k
        for dy in (0, -1, 1, -2, 2, -3, 3):
            for dx in (0.0, -0.6, 0.6, -1.1, 1.1):
                candidates.append(clamp(cx - bw / 2 + dx * bw, top + dy * step))
        # `min` es estable: entre los que no pisan nada gana el primero (el del frame anterior).
        best = min(candidates, key=lambda c: overlap_area((c[0], c[1], c[0] + bw, c[1] + bh)))
        x, y = best
        placed.append((x, y, x + bw, y + bh))
        if layout is not None:
            layout[key] = (x, y)
        return x, y

    calibrating = states is None
    if calibrating:
        for m in zones.ignore_masks:
            d.polygon(px(m), fill=(248, 113, 113, 60))
        for z in zones.staff_zones:
            d.polygon(px(z), fill=(250, 204, 21, 40), outline=(250, 204, 21, 220), width=2)
        for v in zones.views:
            d.polygon(px(v.seats_polygon), outline=(56, 189, 248, 160), width=1)

    # Lugar del panel y de los avisos: se reserva antes para que las etiquetas no queden debajo.
    f_clock, f_leg = _font(round(20 * k), "bold"), _font(round(11 * k), "regular")
    f_at, f_ad = _font(round(12 * k), "bold"), _font(round(11 * k), "regular")
    verifying = any(st.state == "VERIFICANDO" for st in (states or {}).values())
    legend_rows = [*LEGEND, *(["VERIFICANDO"] if verifying else []), "MOZO"]
    row_h = 17 * k
    leg_w = max(d.textlength(STATE_NAMES.get(r, "Mozo"), font=f_leg) for r in legend_rows)
    pw = max(leg_w + 30 * k, d.textlength(clock or "", font=f_clock) + 24 * k)
    ph_panel = (34 * k if clock else 10 * k) + len(legend_rows) * row_h + 6 * k
    # Hora y leyenda arriba a la derecha (debajo de la fecha que imprime el grabador); los avisos
    # arriba a la izquierda.
    x0, y0 = w - pw - 10 * k, 0.105 * h
    cards: list[tuple[OverlayAlert, tuple[float, float, float, float]]] = []
    if not calibrating:
        placed.append((x0, y0, x0 + pw, y0 + ph_panel))
        ay = 10 * k
        for a in (alerts or [])[:3]:
            cw = max(d.textlength(a.title, font=f_at), d.textlength(a.detail, font=f_ad)) + 30 * k
            rect = (10 * k, ay, 10 * k + cw, ay + 40 * k)
            cards.append((a, rect))
            placed.append(rect)
            ay += 46 * k

    # 1) Mesas: relleno translúcido + borde fino del color del estado.
    for v in zones.views:
        st = (states or {}).get(v.table_id)
        color = STATE_COLORS.get(st.state, (56, 189, 248)) if st else (56, 189, 248)
        d.polygon(px(v.table_polygon), fill=(*color, 85), outline=(*color, 235), width=round(2 * k))

    # 2) Personal: elipse en el piso + marcador sobre la cabeza.
    staff_tags: list[tuple[float, float]] = []
    for p in result.people:
        b = p.box
        if p.is_staff:
            cx, foot = (b.x1 + b.x2) / 2 * w, b.y2 * h
            rx = max(10 * k, (b.x2 - b.x1) * w * 0.55)
            ry = rx * 0.32
            d.ellipse(
                (cx - rx, foot - ry, cx + rx, foot + ry),
                fill=(*STAFF_COLOR, 70),
                outline=(*STAFF_COLOR, 230),
                width=round(2 * k),
            )
            staff_tags.append((cx, b.y1 * h))
        elif calibrating:
            d.rectangle(
                (b.x1 * w, b.y1 * h, b.x2 * w, b.y2 * h), outline=(186, 230, 253, 140), width=1
            )

    # 3) Etiquetas de mesa (píldoras) por encima de todo lo anterior.
    f_num, f_state = _font(round(13 * k), "bold"), _font(round(12 * k), "bold")
    f_small = _font(round(11 * k), "regular")
    pad, ph = 6 * k, 22 * k
    for v in zones.views:
        st = (states or {}).get(v.table_id)
        name = (st.label if st and st.label else None) or (labels or {}).get(v.table_id, v.table_id)
        poly = px(v.table_polygon)
        cx = sum(q[0] for q in poly) / len(poly)
        top = min(q[1] for q in poly) - ph - 6 * k
        if st is None:
            counts = result.tables.get(v.table_id)
            text = f"{name}  {counts.seated_customers if counts else 0} sent."
            tw = d.textlength(text, font=f_small)
            x, y = place(v.table_id, cx, top, tw + 2 * pad, ph)
            d.rounded_rectangle((x, y, x + tw + 2 * pad, y + ph), ph / 2, fill=(15, 23, 42, 210))
            d.text((x + pad, y + ph / 2), text, font=f_small, fill="white", anchor="lm")
            continue
        # El estado lo dice el color (leyenda arriba a la derecha): la etiqueta lleva número,
        # tiempo en el estado y personas.
        color = STATE_COLORS.get(st.state, (71, 85, 105))
        idle = st.state in ("LIBRE", "DESHABILITADA")
        timer = ""
        if st.state == "VERIFICANDO":
            timer = "…"
        elif st.elapsed_s is not None and not idle:
            timer = _fmt_elapsed(st.elapsed_s)
        people = 0 if idle else st.people
        num_w = max(ph, d.textlength(name, font=f_num) + 10 * k)
        parts_w = 0.0
        if timer:
            parts_w += d.textlength(timer, font=f_state)
        if people:
            parts_w += (8 * k if timer else 0) + 9 * k + d.textlength(str(people), font=f_small)
        bw = 3 * k + num_w + (4 * k + parts_w + pad if parts_w else 0)
        x, y = place(v.table_id, cx, top, bw, ph)
        d.rounded_rectangle((x, y, x + bw, y + ph), ph / 2, fill=(*color, 240))
        # número de mesa en un círculo/óvalo blanco
        nx0, ny0 = x + 3 * k, y + 3 * k
        d.rounded_rectangle(
            (nx0, ny0, nx0 + num_w - 3 * k, y + ph - 3 * k),
            (ph - 6 * k) / 2,
            fill=(255, 255, 255, 245),
        )
        d.text(
            (nx0 + (num_w - 3 * k) / 2, y + ph / 2),
            name,
            font=f_num,
            fill=(*color, 255),
            anchor="mm",
        )
        tx = nx0 + num_w + 4 * k
        if timer:
            d.text((tx, y + ph / 2), timer, font=f_state, fill="white", anchor="lm")
            tx += d.textlength(timer, font=f_state) + 8 * k
        if people:
            # ícono de persona: cabeza + hombros
            hx, hy = tx + 4 * k, y + ph / 2
            d.ellipse(
                (hx - 2.4 * k, hy - 6 * k, hx + 2.4 * k, hy - 1.2 * k), fill=(255, 255, 255, 230)
            )
            d.pieslice(
                (hx - 4.5 * k, hy, hx + 4.5 * k, hy + 9 * k), 180, 360, fill=(255, 255, 255, 230)
            )
            tx += 9 * k
            d.text((tx, y + ph / 2), str(people), font=f_small, fill="white", anchor="lm")

    # 4) Marcadores de personal (después de las mesas: si se pisan, el mozo queda visible).
    f_tag = _font(round(10 * k), "bold")
    for cx, head in staff_tags:
        tw = d.textlength("MOZO", font=f_tag)
        bw, bh = tw + 10 * k, 15 * k
        x, y = cx - bw / 2, max(4.0, head - bh - 7 * k)
        d.rounded_rectangle((x, y, x + bw, y + bh), bh / 2, fill=(*STAFF_COLOR, 245))
        d.polygon(
            [(cx - 4 * k, y + bh - 1), (cx + 4 * k, y + bh - 1), (cx, y + bh + 5 * k)],
            fill=(*STAFF_COLOR, 245),
        )
        d.text((cx, y + bh / 2), "MOZO", font=f_tag, fill=(30, 27, 15), anchor="mm")

    # 5) Panel: hora + leyenda (arriba a la izquierda) y avisos (arriba a la derecha).
    if not calibrating:
        d.rounded_rectangle((x0, y0, x0 + pw, y0 + ph_panel), 10 * k, fill=(2, 6, 23, 185))
        y = y0 + 8 * k
        if clock:
            d.text((x0 + 12 * k, y), clock, font=f_clock, fill="white")
            y += 30 * k
        for r in legend_rows:
            cy = y + row_h / 2
            col = STAFF_COLOR if r == "MOZO" else STATE_COLORS[r]
            d.ellipse((x0 + 12 * k, cy - 5 * k, x0 + 22 * k, cy + 5 * k), fill=(*col, 255))
            d.text(
                (x0 + 28 * k, cy),
                STATE_NAMES.get(r, "Mozo"),
                font=f_leg,
                fill=(226, 232, 240),
                anchor="lm",
            )
            y += row_h
        for a, (ax, ay, bx, by) in cards:
            accent = (220, 38, 38) if a.critical else (245, 158, 11)
            d.rounded_rectangle((ax, ay, bx, by), 9 * k, fill=(255, 255, 255, 235))
            d.rounded_rectangle((ax, ay, ax + 6 * k, by), 3 * k, fill=(*accent, 255))
            d.text((ax + 16 * k, ay + 7 * k), a.title, font=f_at, fill=(15, 23, 42))
            d.text((ax + 16 * k, ay + 22 * k), a.detail, font=f_ad, fill=(71, 85, 105))

    out = Image.alpha_composite(base, layer).convert("RGB")
    return np.asarray(out)
