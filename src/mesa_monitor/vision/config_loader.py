"""Carga de la configuración del worker desde la base (+ recommended.yaml del benchmark)."""

import logging
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mesa_monitor.core.color import ColorProfile
from mesa_monitor.core.schedule import DEFAULT_CUTOFF, OpeningSpan
from mesa_monitor.core.state_engine import EngineParams, with_clean_mode
from mesa_monitor.core.thresholds import ThresholdRow, engine_params, resolve
from mesa_monitor.db.models import (
    AppSetting,
    Camera,
    IgnoreMask,
    OperatingHours,
    StaffProfile,
    StaffZone,
    Table,
    TableView,
    Threshold,
)
from mesa_monitor.vision.detector import DetectorParams, TilingParams, TrackerParams
from mesa_monitor.vision.persistence import TableMeta
from mesa_monitor.vision.scene import CameraZonesRT, PostureParams, ViewZone
from mesa_monitor.vision.staff import StaffWeights

log = logging.getLogger(__name__)


@dataclass
class CameraCfg:
    id: str
    name: str
    fps: float


@dataclass
class WorkerConfig:
    cameras: dict[str, CameraCfg] = field(default_factory=dict)
    zones: dict[str, CameraZonesRT] = field(default_factory=dict)
    tables: dict[str, TableMeta] = field(default_factory=dict)
    enabled_tables: set[str] = field(default_factory=set)
    params: dict[str, EngineParams] = field(default_factory=dict)
    weights: StaffWeights = field(default_factory=StaffWeights)
    profile: ColorProfile | None = None
    hours: list[OpeningSpan] = field(default_factory=list)
    cutoff: time = DEFAULT_CUTOFF
    detector: DetectorParams = field(default_factory=DetectorParams)
    tracker: TrackerParams = field(default_factory=TrackerParams)
    posture: PostureParams | None = None
    seats_expand: float = 1.25
    debug_overlay: bool = False

    def views_of(self, table_id: str) -> list[tuple[str, ViewZone]]:
        return [
            (cid, v) for cid, z in self.zones.items() for v in z.views if v.table_id == table_id
        ]


def _detector_from(settings: dict[str, Any], recommended: dict[str, Any] | None) -> DetectorParams:
    vision = settings.get("vision") or {}
    rec = (recommended or {}).get("recommendation") or {}
    tiling = vision.get("tiling") or {}
    model = vision.get("model") or rec.get("model") or "medium"
    return DetectorParams(
        model=model,
        device=vision.get("device"),
        person_confidence=float(vision.get("person_confidence", 0.4)),
        object_confidence={
            str(k): float(v) for k, v in (vision.get("object_confidence") or {}).items()
        },
        tiling=TilingParams(
            enabled=bool(tiling.get("enabled", True)),
            tile_w=int(tiling.get("tile_w", 560)),
            tile_h=int(tiling.get("tile_h", 600)),
            overlap=int(tiling.get("overlap", 96)),
            batch_size=int(tiling.get("batch_size", 4)),
        ),
    )


def read_recommended(config_dir: Path) -> dict[str, Any] | None:
    path = config_dir / "recommended.yaml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        log.warning("No se pudo leer %s", path)
        return None


async def load_worker_config(
    sm: async_sessionmaker[AsyncSession], tz: ZoneInfo, config_dir: Path
) -> WorkerConfig:
    cfg = WorkerConfig()
    async with sm() as db:
        settings = {s.key: s.value for s in (await db.execute(select(AppSetting))).scalars()}
        rec = read_recommended(config_dir)
        rec_fps = ((rec or {}).get("recommendation") or {}).get("fps_per_camera")
        for c in (await db.execute(select(Camera).where(Camera.enabled))).scalars():
            fps = min(c.fps_detection, float(rec_fps)) if rec_fps else c.fps_detection
            cfg.cameras[c.id] = CameraCfg(c.id, c.name, fps)
        tables = (await db.execute(select(Table))).scalars().all()
        for t in tables:
            cfg.tables[t.id] = TableMeta(t.id, t.label, t.sector)
            if t.enabled:
                cfg.enabled_tables.add(t.id)
        vision = settings.get("vision") or {}
        cfg.seats_expand = float(vision.get("seats_expand", 1.25))
        cfg.debug_overlay = bool(vision.get("debug_overlay", False))
        if "standing_ratio" in vision or "frame_aspect" in vision:
            cfg.posture = PostureParams(
                standing_ratio=float(vision.get("standing_ratio", 1.6)),
                frame_aspect=float(vision.get("frame_aspect", 16 / 9)),
            )
        tr = vision.get("tracker") or {}
        cfg.tracker = TrackerParams(
            algorithm=str(tr.get("algorithm", "ocsort")),
            lost_track_s=float(tr.get("lost_track_s", 3.0)),
            min_consecutive_frames=int(tr.get("min_consecutive_frames", 2)),
            min_iou=float(tr.get("min_iou", 0.15)),
        )
        cfg.detector = _detector_from(settings, rec)

        views = (await db.execute(select(TableView))).scalars().all()
        staff = (await db.execute(select(StaffZone))).scalars().all()
        masks = (await db.execute(select(IgnoreMask))).scalars().all()
        for cid in cfg.cameras:
            cfg.zones[cid] = CameraZonesRT(
                camera_id=cid,
                views=[
                    ViewZone(v.table_id, v.table_polygon, v.seats_polygon, v.priority, v.clean_mode)
                    for v in views
                    if v.camera_id == cid and v.table_id in cfg.enabled_tables
                ],
                staff_zones=[z.polygon for z in staff if z.camera_id == cid],
                ignore_masks=[m.polygon for m in masks if m.camera_id == cid],
                seats_expand=cfg.seats_expand,
            )

        rows = [
            ThresholdRow(r.scope, r.scope_ref, r.key, r.value, r.valid_from, r.valid_to)
            for r in (await db.execute(select(Threshold))).scalars()
        ]
        for t in tables:
            params = engine_params(resolve(rows, table_id=t.id, sector=t.sector))
            # El modo de limpieza sale de la vista de mayor prioridad de la mesa.
            tv = sorted((v for v in views if v.table_id == t.id), key=lambda v: -v.priority)
            if tv:
                params = with_clean_mode(params, tv[0].clean_mode)
            cfg.params[t.id] = params

        prof = (
            await db.execute(select(StaffProfile).where(StaffProfile.active))
        ).scalar_one_or_none()
        if prof is not None:
            cfg.profile = ColorProfile.from_json(prof.color_profile)
            cfg.weights = StaffWeights.from_json(prof.weights, prof.threshold)
        cfg.hours = [
            OpeningSpan(h.weekday, h.opens_at, h.closes_at)
            for h in (await db.execute(select(OperatingHours))).scalars()
        ]
        cutoff = settings.get("business_day_cutoff")
        if isinstance(cutoff, str):
            try:
                cfg.cutoff = time.fromisoformat(cutoff)
            except ValueError:
                log.warning("business_day_cutoff inválido: %r", cutoff)
    return cfg
