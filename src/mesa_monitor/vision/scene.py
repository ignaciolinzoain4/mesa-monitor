"""De detecciones de una cámara a observaciones por mesa (SPEC §6, pasos 3–8).

Sin dependencias de visión pesadas: recibe cajas ya trackeadas en coordenadas normalizadas.

Fusión multi-cámara (SPEC §5.2): para cada mesa se usa la vista de **mayor prioridad cuya
cámara está sana** (frames recientes). Si ninguna está sana, la observación sale con
`view_healthy=False` y la mesa pasa a SIN_VISION. Ante empate de prioridad gana la cámara de id
menor (determinista). La oclusión parcial no se detecta todavía: una vista con la cámara sana
se considera sana.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from mesa_monitor.core.enums import CleanMode
from mesa_monitor.core.geometry import area, bbox, contains, expand
from mesa_monitor.core.state_engine import Observation
from mesa_monitor.vision.staff import PersonFeatures, StaffClassifier

Poly = Sequence[Sequence[float]]


@dataclass(frozen=True)
class ViewZone:
    table_id: str
    table_polygon: Poly
    seats_polygon: Poly
    priority: int = 0
    clean_mode: CleanMode = CleanMode.OBJECTS_AND_STAFF


@dataclass
class CameraZonesRT:
    """Zonas de una cámara preparadas para consultas rápidas."""

    camera_id: str
    views: list[ViewZone] = field(default_factory=list)
    staff_zones: list[Poly] = field(default_factory=list)
    ignore_masks: list[Poly] = field(default_factory=list)
    seats_expand: float = 1.25

    def __post_init__(self) -> None:
        self._expanded = {
            v.table_id: expand(v.seats_polygon, self.seats_expand) for v in self.views
        }
        # Asientos más chicos primero: si dos áreas se pisan, gana la más específica.
        self._by_area = sorted(self.views, key=lambda v: area(v.seats_polygon))

    def roi(self, margin: float = 0.05) -> tuple[float, float, float, float] | None:
        """Rectángulo que cubre todas las zonas (para no procesar el resto de la imagen)."""
        polys = [
            *(v.seats_polygon for v in self.views),
            *self._expanded.values(),
            *self.staff_zones,
        ]
        if not polys:
            return None
        boxes = [bbox(p) for p in polys]
        return (
            max(0.0, min(b[0] for b in boxes) - margin),
            max(0.0, min(b[1] for b in boxes) - margin),
            min(1.0, max(b[2] for b in boxes) + margin),
            min(1.0, max(b[3] for b in boxes) + margin),
        )

    def masked(self, x: float, y: float) -> bool:
        return any(contains(m, x, y) for m in self.ignore_masks)

    def table_at(self, x: float, y: float) -> str | None:
        for v in self._by_area:
            if contains(v.seats_polygon, x, y):
                return v.table_id
        return None

    def near_table(self, x: float, y: float) -> list[str]:
        """Mesas cuyo polígono de asientos *ampliado* contiene el punto."""
        return [tid for tid, poly in self._expanded.items() if contains(poly, x, y)]

    def in_staff_zone(self, x: float, y: float) -> bool:
        return any(contains(z, x, y) for z in self.staff_zones)


@dataclass(frozen=True)
class Box:
    """Caja normalizada 0–1."""

    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float = 1.0
    track_id: int | None = None
    label: str = "person"

    @property
    def anchor(self) -> tuple[float, float]:
        """Punto de anclaje: centro inferior (los pies / el asiento)."""
        return (self.x1 + self.x2) / 2, self.y2

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2


@dataclass(frozen=True)
class PostureParams:
    """Postura sin keypoints: proporción alto/ancho en píxeles reales + movimiento.

    Con cámaras en altura una persona sentada se ve casi tan alargada como una parada (en un
    video real: sentados 1.3–2.8, parados 2.9–3.9), pero los sentados casi no se mueven. Por eso:
    parado = proporción ≥ `standing_ratio`, o proporción ≥ `moving_ratio` y desplazándose.
    """

    standing_ratio: float = 2.6
    moving_ratio: float = 1.8
    moving_speed: float = (
        0.02  # desplazamiento del punto de anclaje (fracción de imagen) por segundo
    )
    motion_window_s: float = 3.0
    frame_aspect: float = 16 / 9  # ancho/alto del frame en píxeles reales (corrige substreams D1)


def aspect_ratio(box: Box, frame_aspect: float) -> float:
    w = max(1e-6, (box.x2 - box.x1) * frame_aspect)
    return (box.y2 - box.y1) / w


def is_standing(box: Box, p: PostureParams, speed: float | None = None) -> bool:
    ratio = aspect_ratio(box, p.frame_aspect)
    if ratio >= p.standing_ratio:
        return True
    return speed is not None and speed >= p.moving_speed and ratio >= p.moving_ratio


class MotionTracker:
    """Velocidad de cada track (memoria corta, solo en RAM)."""

    def __init__(self) -> None:
        self._hist: dict[int, list[tuple[float, float, float]]] = {}

    def speed(self, track_id: int, ts: datetime, x: float, y: float, window_s: float) -> float:
        t = ts.timestamp()
        h = self._hist.setdefault(track_id, [])
        h.append((t, x, y))
        while h and t - h[0][0] > window_s:
            h.pop(0)
        if len(h) < 2 or h[-1][0] - h[0][0] < 0.5:
            return 0.0
        (t0, x0, y0), (t1, x1, y1) = h[0], h[-1]
        return float(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / (t1 - t0))

    def forget(self, active: set[int], now: datetime | None = None, after_s: float = 10.0) -> None:
        t = now.timestamp() if now else None
        for tid, h in list(self._hist.items()):
            if tid in active:
                continue
            if t is None or not h or t - h[-1][0] > after_s:
                del self._hist[tid]


@dataclass(frozen=True)
class PersonResult:
    box: Box
    standing: bool
    table_id: str | None
    is_staff: bool


@dataclass(frozen=True)
class TableCounts:
    seated_customers: int
    staff_present: bool
    object_count: int | None


@dataclass
class SceneResult:
    people: list[PersonResult]
    tables: dict[str, TableCounts]


def analyze_frame(
    zones: CameraZonesRT,
    persons: Iterable[Box],
    objects: Iterable[Box],
    ts: datetime,
    classifier: StaffClassifier,
    posture: PostureParams,
    color_similarity: dict[int, float] | None = None,
    objects_enabled: bool = True,
    motion: MotionTracker | None = None,
) -> SceneResult:
    people: list[PersonResult] = []
    active: set[int] = set()
    for b in persons:
        ax, ay = b.anchor
        if zones.masked(ax, ay):
            continue
        speed = (
            motion.speed(b.track_id, ts, ax, ay, posture.motion_window_s)
            if motion is not None and b.track_id is not None
            else None
        )
        standing = is_standing(b, posture, speed)
        table = zones.table_at(ax, ay)
        staff = False
        if b.track_id is not None:
            active.add(b.track_id)
            staff = classifier.classify(
                PersonFeatures(
                    track_id=b.track_id,
                    ts=ts,
                    standing=standing,
                    table_id=table,
                    in_staff_zone=zones.in_staff_zone(ax, ay),
                    color_similarity=(color_similarity or {}).get(b.track_id),
                )
            )
        people.append(PersonResult(b, standing, table, staff))
    classifier.forget(active, ts)
    if motion is not None:
        motion.forget(active, ts)

    tables: dict[str, TableCounts] = {}
    obj_list = [o for o in objects if not zones.masked(*o.center)]
    for v in zones.views:
        seated = sum(
            1 for p in people if p.table_id == v.table_id and not p.standing and not p.is_staff
        )
        staff_here = any(
            p.is_staff and p.standing and v.table_id in zones.near_table(*p.box.anchor)
            for p in people
        )
        n_obj: int | None = None
        if objects_enabled and v.clean_mode is not CleanMode.STAFF_VISIT_ONLY:
            n_obj = sum(1 for o in obj_list if contains(v.table_polygon, *o.center))
        tables[v.table_id] = TableCounts(seated, staff_here, n_obj)
    return SceneResult(people, tables)


@dataclass(frozen=True)
class CameraTableObs:
    camera_id: str
    priority: int
    healthy: bool
    counts: TableCounts | None


def fuse(table_id: str, ts: datetime, candidates: Sequence[CameraTableObs]) -> Observation:
    """Elige la vista de mayor prioridad sana (ver docstring del módulo)."""
    healthy = [c for c in candidates if c.healthy and c.counts is not None]
    if not healthy:
        return Observation(table_id, ts, 0, False, None, view_healthy=False)
    best = sorted(healthy, key=lambda c: (-c.priority, c.camera_id))[0]
    assert best.counts is not None
    return Observation(
        table_id,
        ts,
        best.counts.seated_customers,
        best.counts.staff_present,
        best.counts.object_count,
        view_healthy=True,
    )
