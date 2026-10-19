"""Evaluación de precisión (SPEC §18, F9): eventos detectados vs. anotaciones manuales.

Anotaciones: CSV con columnas `table_label,event,timestamp_video` donde
- `event` ∈ {seated, first_attention, staff_visit, vacated, cleaned}
- `timestamp_video` = posición en el video: `HH:MM:SS`, `MM:SS` o segundos (desde el inicio).

Detecciones: el `events.jsonl` que genera `tools/replay.py` para ese mismo video.

    uv run python tools/evaluate.py data/replay/VIDEO/events.jsonl anotaciones.csv \\
        --video-start "2026-10-18T18:00:00-03:00"

`--video-start` es la hora real del primer frame del video (la que muestra el sello del NVR).
Si se omite, se toma la del primer evento del replay que tenga la marca `video_start`, o la
primera observación (`observations.jsonl` en la misma carpeta).

Cómo se empareja: por mesa y tipo de evento, cada anotación con la detección más cercana dentro de
la tolerancia (uno a uno, de menor a mayor distancia). Se reporta el error medio absoluto (s),
mediana, máximo, y para `staff_visit` precisión, recall y F1.
"""

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

EVENTS = ("seated", "first_attention", "staff_visit", "vacated", "cleaned")
TOLERANCE_S = {
    "seated": 120,
    "first_attention": 90,
    "staff_visit": 30,
    "vacated": 240,
    "cleaned": 180,
}


def parse_video_time(raw: str) -> float:
    raw = raw.strip()
    if ":" not in raw:
        return float(raw)
    parts = [float(p) for p in raw.split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


@dataclass(frozen=True)
class Mark:
    table: str
    event: str
    t: float  # segundos desde el inicio del video


def load_annotations(path: Path) -> list[Mark]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            ev = (row.get("event") or "").strip()
            if ev not in EVENTS:
                raise SystemExit(
                    f"{path}:{i}: evento desconocido {ev!r} (válidos: {', '.join(EVENTS)})"
                )
            out.append(
                Mark(row["table_label"].strip(), ev, parse_video_time(row["timestamp_video"]))
            )
    return out


def load_detections(path: Path, video_start: datetime) -> list[Mark]:
    """Convierte el events.jsonl del replay en marcas comparables (segundos desde el inicio)."""
    out: list[Mark] = []
    first_attention_done: set[str] = set()

    def rel(iso: str) -> float:
        return (datetime.fromisoformat(iso) - video_start).total_seconds()

    for line in path.read_text(encoding="utf-8").splitlines():
        e: dict[str, Any] = json.loads(line)
        kind = e.get("event")
        table = e.get("table_id", "")
        if kind == "SessionOpened":
            out.append(Mark(table, "seated", rel(e["seated_at"])))
        elif kind == "VisitStarted":
            out.append(Mark(table, "staff_visit", rel(e["started_at"])))
        elif kind == "SessionUpdated":
            sid = e["session_id"]
            if e.get("first_attention_at") and sid not in first_attention_done:
                first_attention_done.add(sid)
                out.append(Mark(table, "first_attention", rel(e["first_attention_at"])))
            if e.get("vacated_at") and (sid + ":v") not in first_attention_done:
                first_attention_done.add(sid + ":v")
                out.append(Mark(table, "vacated", rel(e["vacated_at"])))
        elif kind == "SessionClosed" and e.get("cleaned_at"):
            out.append(Mark(table, "cleaned", rel(e["cleaned_at"])))
    return out


@dataclass
class Result:
    errors: list[float] = field(default_factory=list)
    matched: int = 0
    false_negatives: int = 0  # anotado y no detectado
    false_positives: int = 0  # detectado y no anotado

    @property
    def precision(self) -> float | None:
        d = self.matched + self.false_positives
        return self.matched / d if d else None

    @property
    def recall(self) -> float | None:
        d = self.matched + self.false_negatives
        return self.matched / d if d else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p and r else None


def evaluate(
    annotations: list[Mark],
    detections: list[Mark],
    tolerance: dict[str, float] | None = None,
    window: tuple[float, float] | None = None,
) -> dict[str, Result]:
    tol = {**TOLERANCE_S, **(tolerance or {})}
    if window is not None:  # solo se evalúa el tramo anotado
        lo, hi = window
        detections = [d for d in detections if lo - 60 <= d.t <= hi + 60]
    results: dict[str, Result] = {ev: Result() for ev in EVENTS}
    groups: dict[tuple[str, str], tuple[list[Mark], list[Mark]]] = defaultdict(lambda: ([], []))
    for a in annotations:
        groups[(a.table, a.event)][0].append(a)
    for d in detections:
        groups[(d.table, d.event)][1].append(d)
    for (_, ev), (ann, det) in groups.items():
        pairs = sorted(
            ((abs(a.t - d.t), i, j) for i, a in enumerate(ann) for j, d in enumerate(det)),
            key=lambda x: x[0],
        )
        used_a: set[int] = set()
        used_d: set[int] = set()
        for dist, i, j in pairs:
            if dist > tol[ev] or i in used_a or j in used_d:
                continue
            used_a.add(i)
            used_d.add(j)
            results[ev].errors.append(det[j].t - ann[i].t)
            results[ev].matched += 1
        results[ev].false_negatives += len(ann) - len(used_a)
        results[ev].false_positives += len(det) - len(used_d)
    return results


def fmt(v: float | None, pct: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.0f} %" if pct else f"{v:.1f}"


def report(results: dict[str, Result], annotated_minutes: float) -> str:
    lines = [
        f"Evaluación sobre {annotated_minutes:.0f} min de video anotado",
        "",
        f"{'evento':<16}{'anot.':>6}{'detec.':>7}{'error medio (s)':>17}{'mediana':>9}{'máx':>7}{'sesgo':>8}{'precisión':>11}{'recall':>8}",
    ]
    for ev, r in results.items():
        ann = r.matched + r.false_negatives
        det = r.matched + r.false_positives
        abs_err = [abs(e) for e in r.errors]
        lines.append(
            f"{ev:<16}{ann:>6}{det:>7}"
            f"{fmt(statistics.fmean(abs_err) if abs_err else None):>17}"
            f"{fmt(statistics.median(abs_err) if abs_err else None):>9}"
            f"{fmt(max(abs_err) if abs_err else None):>7}"
            f"{fmt(statistics.fmean(r.errors) if r.errors else None):>8}"
            f"{fmt(r.precision, True):>11}{fmt(r.recall, True):>8}"
        )
    v = results["staff_visit"]
    lines += [
        "",
        f"Visitas del personal: {v.matched} acertadas, {v.false_positives} falsas (detectadas sin "
        f"haber ocurrido), {v.false_negatives} perdidas (ocurrieron y no se detectaron). F1 = {fmt(v.f1, True)}.",
        "Sesgo > 0 = el sistema detecta más tarde que la realidad (normal: hay antirrebote).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("events", type=Path, help="events.jsonl generado por tools/replay.py")
    ap.add_argument("annotations", type=Path, help="CSV table_label,event,timestamp_video")
    ap.add_argument("--video-start", help="hora real del primer frame (ISO con zona horaria)")
    ap.add_argument("--json", type=Path, help="guardar el resultado también en JSON")
    args = ap.parse_args(argv)

    if args.video_start:
        start = datetime.fromisoformat(args.video_start)
    else:
        obs = args.events.parent / "observations.jsonl"
        if not obs.is_file():
            raise SystemExit(
                "Pasá --video-start (no encontré observations.jsonl junto a events.jsonl)"
            )
        with obs.open() as f:
            start = datetime.fromisoformat(json.loads(f.readline())["ts"])
    if start.tzinfo is None:
        raise SystemExit("--video-start necesita zona horaria, ej. 2026-10-18T18:00:00-03:00")

    annotations = load_annotations(args.annotations)
    if not annotations:
        raise SystemExit("El CSV de anotaciones está vacío")
    detections = load_detections(args.events, start)
    lo = min(a.t for a in annotations)
    hi = max(a.t for a in annotations)
    results = evaluate(annotations, detections, window=(lo, hi))
    text = report(results, (hi - lo) / 60)
    print(text)
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    ev: {
                        "matched": r.matched,
                        "false_positives": r.false_positives,
                        "false_negatives": r.false_negatives,
                        "mean_abs_error_s": statistics.fmean([abs(e) for e in r.errors])
                        if r.errors
                        else None,
                        "bias_s": statistics.fmean(r.errors) if r.errors else None,
                        "precision": r.precision,
                        "recall": r.recall,
                        "f1": r.f1,
                    }
                    for ev, r in results.items()
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
