"""Modo replay: procesa un video grabado con el mismo pipeline y state engine que el worker.

Sirve para calibrar zonas y umbrales con video real sin tocar el sistema en vivo.

Uso:
    uv run python tools/replay.py VIDEO --zones respaldo.yaml --camera salon-1 [opciones]

- VIDEO: archivo exportado del NVR (.dav de Dahua, .mp4, …). Se guarda FUERA del repo.
- --zones: respaldo YAML (dashboard → Respaldo → Descargar) con las zonas de esa cámara.
- --camera: id de la cámara dentro del respaldo cuyas zonas corresponden al video.

Salida (en --out, por defecto data/replay/<video>/, ignorado por git):
    observations.jsonl  observación por mesa y por frame
    events.jsonl        transiciones, sesiones y visitas
    sessions.csv        una fila por sesión (seated_at, first_attention_at, vacated_at, ...)
    annotated.mp4       (con --annotate) video con el overlay de debug
"""

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from mesa_monitor.core.alerts import TableAlertInput, table_alerts
from mesa_monitor.core.enums import CleanMode, SessionCloseReason, Severity
from mesa_monitor.core.state_engine import (
    EngineParams,
    Event,
    SessionClosed,
    SessionOpened,
    SessionUpdated,
    StateEngine,
    Transition,
    VisitStarted,
)
from mesa_monitor.core.thresholds import DEFAULTS
from mesa_monitor.vision.detector import Detector, DetectorParams, TilingParams, TrackerParams
from mesa_monitor.vision.pipeline import (
    STATE_NAMES,
    CameraPipeline,
    OverlayAlert,
    TableOverlay,
    annotate,
)
from mesa_monitor.vision.scene import CameraTableObs, CameraZonesRT, ViewZone, fuse
from mesa_monitor.vision.sources import file_frames
from mesa_monitor.vision.staff import StaffWeights


def load_zones(path: Path, camera_id: str) -> CameraZonesRT:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cams = doc.get("cameras") or {}
    if camera_id not in cams:
        raise SystemExit(f"La cámara {camera_id!r} no está en {path}. Cámaras: {sorted(cams)}")
    cam = cams[camera_id]
    views = [
        ViewZone(
            table_id=str(v["table"]),
            table_polygon=v["table_polygon"],
            seats_polygon=v["seats_polygon"],
            priority=int(v.get("priority", 0)),
            clean_mode=CleanMode(v.get("clean_mode", CleanMode.OBJECTS_AND_STAFF.value)),
        )
        for v in cam.get("table_views") or []
    ]
    if not views:
        raise SystemExit(f"La cámara {camera_id!r} no tiene mesas dibujadas en {path}")
    return CameraZonesRT(
        camera_id=camera_id,
        views=views,
        staff_zones=[z["polygon"] for z in cam.get("staff_zones") or []],
        ignore_masks=[m["polygon"] for m in cam.get("ignore_masks") or []],
    )


def overlay_state(
    engine: StateEngine, now: datetime
) -> tuple[dict[str, TableOverlay], list[OverlayAlert]]:
    """Estado de cada mesa para dibujar y los avisos que estarían activos (reglas reales)."""
    overlay: dict[str, TableOverlay] = {}
    alerts: list[tuple[float, OverlayAlert]] = []
    for tid, m in engine.machines.items():
        snap = m.snapshot()
        elapsed = None
        if snap.state_since is not None:
            elapsed = (now - snap.state_since).total_seconds() - snap.paused_s
        overlay[tid] = TableOverlay(snap.state.value, elapsed, snap.people_now, tid)
        inp = TableAlertInput(
            tid,
            snap.state,
            snap.state_since,
            snap.paused_s,
            snap.seated_at,
            snap.first_attention_at,
            snap.last_visit_at,
        )
        for a in table_alerts(inp, DEFAULTS, now):
            mins = int(a.elapsed_s // 60)
            what = {
                "SIN_ATENCION": "sin atender",
                "SIN_VISITA": "sin visita del mozo",
                "MESA_SUCIA": "sucia",
            }.get(a.alert_type.value, STATE_NAMES.get(snap.state.value, ""))
            alerts.append(
                (
                    -a.elapsed_s,
                    OverlayAlert(
                        f"Mesa {tid} · {what}",
                        f"hace {mins} min",
                        critical=a.severity is Severity.CRITICAL,
                    ),
                )
            )
    return overlay, [a for _, a in sorted(alerts, key=lambda x: x[0])]


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        d = {k: _jsonable(v) for k, v in asdict(obj).items()}
        d["event"] = type(obj).__name__
        return d
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    return obj


def _parse_params(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        key, _, value = item.partition("=")
        if key not in EngineParams.__dataclass_fields__:
            raise SystemExit(f"Parámetro desconocido: {key}")
        out[key] = float(value)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("video", type=Path)
    ap.add_argument("--zones", type=Path, required=True)
    ap.add_argument("--camera", required=True)
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--model", default="medium", choices=["nano", "small", "medium", "large"])
    ap.add_argument("--device", default=None)
    ap.add_argument("--no-tiling", action="store_true")
    ap.add_argument("--skip", type=float, default=0.0, help="segundos a saltear al inicio")
    ap.add_argument("--seconds", type=float, default=None, help="procesar solo esta duración")
    ap.add_argument("--start", help="hora real del inicio (ISO) si el video no la trae")
    ap.add_argument(
        "--video-tz",
        default="America/Argentina/Cordoba",
        help="zona horaria de la hora grabada en el video (Dahua graba hora local)",
    )
    ap.add_argument(
        "--param",
        action="append",
        default=[],
        help="umbral del state engine, ej. occupy_confirm_s=15",
    )
    ap.add_argument("--annotate", action="store_true", help="guardar video anotado (annotated.mp4)")
    ap.add_argument(
        "--video-fps",
        type=float,
        default=None,
        help="fps del video anotado (ej. 15 = fluido; la detección sigue a --fps)",
    )
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    zones = load_zones(args.zones, args.camera)
    out = args.out or Path("data/replay") / args.video.stem
    out.mkdir(parents=True, exist_ok=True)
    base = EngineParams(**_parse_params(args.param))  # type: ignore[arg-type]
    engine = StateEngine(default_params=base)
    for v in zones.views:
        engine.machine(v.table_id, EngineParams(**{**base.__dict__, "clean_mode": v.clean_mode}))

    detector = Detector(
        DetectorParams(
            model=args.model, device=args.device, tiling=TilingParams(enabled=not args.no_tiling)
        )
    )
    pipe = CameraPipeline(
        args.camera, zones, detector, fps=args.fps, tracker=TrackerParams(), weights=StaffWeights()
    )
    start = datetime.fromisoformat(args.start).astimezone(UTC) if args.start else None
    tz = ZoneInfo(args.video_tz)

    writer = None
    events: list[Event] = []
    t_wall = time.perf_counter()
    n = 0
    last_ts: datetime | None = None
    with (out / "observations.jsonl").open("w") as obs_f, (out / "events.jsonl").open("w") as ev_f:
        render_fps = max(args.fps, args.video_fps or args.fps) if args.annotate else args.fps
        frames = file_frames(
            args.video, render_fps, start, args.seconds, args.skip, pts_tz=ZoneInfo(args.video_tz)
        )
        next_detect: datetime | None = None
        result = None
        overlay: dict[str, TableOverlay] = {}
        alerts: list[OverlayAlert] = []
        clock = ""
        for frame in frames:
            if next_detect is not None and frame.ts < next_detect:
                # Frame intermedio: se dibuja con el último análisis (video fluido).
                if writer is not None and result is not None:
                    writer.write(annotate(frame.image, zones, result, None, overlay, clock, alerts))
                continue
            next_detect = frame.ts + timedelta(seconds=1 / args.fps)
            result = pipe.process(frame.image, frame.ts)
            for v in zones.views:
                obs = fuse(
                    v.table_id,
                    frame.ts,
                    [CameraTableObs(args.camera, v.priority, True, result.tables[v.table_id])],
                )
                obs_f.write(json.dumps(_jsonable(obs)) + "\n")
                for e in engine.observe(obs):
                    events.append(e)
                    ev_f.write(json.dumps(_jsonable(e)) + "\n")
                    if isinstance(e, Transition):
                        hhmm = e.ts.astimezone(tz)
                        prev = STATE_NAMES.get(str(e.from_state), "—")
                        change = f"{prev} → {STATE_NAMES.get(e.to_state.value)}"
                        print(f"  {hhmm:%H:%M:%S}  mesa {e.table_id}: {change} ({e.reason})")
            if args.annotate:
                writer = writer or _VideoWriter(
                    out / "annotated.mp4", render_fps, frame.image.shape
                )
                overlay, alerts = overlay_state(engine, frame.ts)
                clock = frame.ts.astimezone(tz).strftime("%H:%M:%S")
                writer.write(annotate(frame.image, zones, result, None, overlay, clock, alerts))
            n += 1
            last_ts = frame.ts
            if n % 50 == 0:
                rate = n / (time.perf_counter() - t_wall)
                people = pipe.stats.last_people
                local_ts = frame.ts.astimezone(tz)
                print(
                    f"[{local_ts:%H:%M:%S}] {n} frames · {rate:.1f} frames/s · {people} personas",
                    flush=True,
                )
    if last_ts is not None:
        for e in engine.close_all(last_ts, SessionCloseReason.CLOSING_TIME):
            events.append(e)
    if writer:
        writer.close()
    _write_sessions(out / "sessions.csv", events)
    elapsed = time.perf_counter() - t_wall
    rate = n / max(elapsed, 1e-6)
    print(f"\n{n} frames en {elapsed:.0f} s ({rate:.1f} frames/s). Resultados en {out}/")
    return 0


def _write_sessions(path: Path, events: list[Event]) -> None:
    rows: dict[str, dict[str, Any]] = {}
    visits: dict[str, int] = {}
    for e in events:
        if isinstance(e, SessionOpened):
            rows[e.session_id] = {
                "table": e.table_id,
                "session_id": e.session_id,
                "seated_at": e.seated_at,
                "partial": e.partial,
            }
        elif isinstance(e, SessionUpdated) and e.session_id in rows:
            rows[e.session_id].update(
                first_attention_at=e.first_attention_at,
                served_at=e.served_at,
                vacated_at=e.vacated_at,
                max_people=e.max_people,
            )
        elif isinstance(e, VisitStarted):
            visits[e.session_id] = visits.get(e.session_id, 0) + 1
        elif isinstance(e, SessionClosed) and e.session_id in rows:
            rows[e.session_id].update(cleaned_at=e.cleaned_at, close_reason=e.reason.value)
    fields = [
        "table",
        "session_id",
        "seated_at",
        "partial",
        "first_attention_at",
        "served_at",
        "vacated_at",
        "cleaned_at",
        "max_people",
        "visits",
        "close_reason",
        "wait_min",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for sid, r in rows.items():
            r["visits"] = visits.get(sid, 0)
            if r.get("first_attention_at") and not r.get("partial"):
                r["wait_min"] = round(
                    (r["first_attention_at"] - r["seated_at"]).total_seconds() / 60, 2
                )
            w.writerow({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()})


class _VideoWriter:
    def __init__(self, path: Path, fps: float, shape: tuple[int, ...]) -> None:
        from fractions import Fraction

        import av

        self._c = av.open(str(path), "w")
        rate = Fraction(fps).limit_denominator(1000)
        try:  # H.264: se reproduce en cualquier lado (WhatsApp, navegador, celular)
            self._s = self._c.add_stream("libx264", rate=rate)
            self._s.options = {"crf": "22", "preset": "medium"}
        except (av.FFmpegError, ValueError):
            self._s = self._c.add_stream("mpeg4", rate=rate)
            self._s.bit_rate = 4_000_000
        self._s.height, self._s.width = shape[0] - shape[0] % 2, shape[1] - shape[1] % 2
        self._s.pix_fmt = "yuv420p"

    def write(self, img: Any) -> None:
        import av

        frame = av.VideoFrame.from_ndarray(img[: self._s.height, : self._s.width], format="rgb24")
        for p in self._s.encode(frame):
            self._c.mux(p)

    def close(self) -> None:
        for p in self._s.encode():
            self._c.mux(p)
        self._c.close()


if __name__ == "__main__":
    sys.exit(main())
