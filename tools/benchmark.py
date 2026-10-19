"""Benchmark de hardware y recomendación de configuración (SPEC §16).

Detecta GPU/CPU/RAM, mide los fps reales de cada tamaño de RF-DETR con frames de las cámaras
(vía go2rtc) y escribe la recomendación en `config/local/recommended.yaml`.

Uso (en la PC del local, dentro de Docker):
    docker compose run --rm worker-gpu python tools/benchmark.py      # o worker-cpu
Fuera de Docker:
    uv run python tools/benchmark.py --go2rtc-url http://localhost:1984
Opciones útiles: --sizes nano,small  --images carpeta/  --cameras 9  --seconds 10
                 --tiles-per-frame 4   (detección por mosaicos; 1 = imagen entera)
"""

import argparse
import asyncio
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from mesa_monitor.config.cameras import CamerasConfigError, load_cameras
from mesa_monitor.vision.benchmark import MODEL_SIZES, measure_model, recommend
from mesa_monitor.vision.go2rtc import Go2rtcClient, SnapshotError, StreamKind
from mesa_monitor.vision.hardware import detect_hardware


def _decode(jpeg: bytes) -> Any:
    from PIL import Image

    return Image.open(io.BytesIO(jpeg)).convert("RGB")


async def _frames_from_go2rtc(url: str, camera_ids: list[str]) -> list[Any]:
    client = Go2rtcClient(url)
    frames = []
    try:
        for cid in camera_ids:
            try:
                frames.append(_decode((await client.frame(cid, StreamKind.SUB)).jpeg))
                print(f"  frame de {cid}: ok")
            except SnapshotError as exc:
                print(f"  frame de {cid}: {exc}")
    finally:
        await client.aclose()
    return frames


def _frames_from_dir(path: Path) -> list[Any]:
    from PIL import Image

    files = sorted(p for p in path.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    return [Image.open(p).convert("RGB") for p in files[:16]]


def _synthetic_frames(n: int = 4) -> list[Any]:
    import numpy as np

    rng = np.random.default_rng(0)
    return [rng.integers(0, 255, (480, 704, 3), dtype=np.uint8) for _ in range(n)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path("config/local/cameras.yaml"))
    parser.add_argument("--go2rtc-url", default="http://go2rtc:1984")
    parser.add_argument("--images", type=Path, help="carpeta con frames de ejemplo (jpg/png)")
    parser.add_argument(
        "--cameras", type=int, help="cantidad de cámaras (default: las habilitadas)"
    )
    parser.add_argument("--sizes", default=",".join(MODEL_SIZES))
    parser.add_argument(
        "--seconds", type=float, default=5.0, help="segundos de medición por modelo"
    )
    parser.add_argument(
        "--tiles-per-frame",
        type=float,
        default=4.0,
        help="mosaicos por frame (cámaras gran angular en altura: ~4; imagen entera: 1)",
    )
    parser.add_argument("--target-fps", type=float, default=3.0)
    parser.add_argument("--device", help="forzar dispositivo (cuda, mps, cpu)")
    parser.add_argument("--out", type=Path, default=Path("config/local/recommended.yaml"))
    args = parser.parse_args(argv)

    print("== Hardware")
    hw = detect_hardware()
    print(json.dumps(hw.to_dict(), indent=2, ensure_ascii=False))
    device = args.device or hw.best_device

    camera_ids: list[str] = []
    try:
        cams = load_cameras(args.config)
        camera_ids = [c.id for c in cams.cameras if c.enabled]
    except CamerasConfigError as exc:
        print(f"Aviso: {exc}")
    n_cameras = args.cameras or len(camera_ids) or 9

    print("\n== Frames de prueba")
    frames: list[Any] = []
    if args.images:
        frames = _frames_from_dir(args.images)
    elif camera_ids:
        frames = asyncio.run(_frames_from_go2rtc(args.go2rtc_url, camera_ids))
    if not frames:
        print("  Sin frames reales: se usan imágenes sintéticas (sirve para medir velocidad).")
        frames = _synthetic_frames()

    print(f"\n== RF-DETR en {device} ({n_cameras} cámaras)")
    results = []
    for size in [s.strip() for s in args.sizes.split(",") if s.strip()]:
        if size not in MODEL_SIZES:
            print(f"  {size}: tamaño no permitido (solo {', '.join(MODEL_SIZES)})")
            continue
        print(f"  midiendo {size}…", flush=True)
        r = measure_model(
            size,
            frames,
            device=device,
            batch_sizes=(1, min(4, n_cameras)),
            min_seconds=args.seconds,
        )
        results.append(r)
        if r.ok:
            print(f"    {r.images_per_s} img/s (batch {r.batch_size}, {r.latency_ms} ms/llamada)")
        else:
            print(f"    ERROR: {r.error}")

    try:
        rec = recommend(
            results,
            n_cameras=n_cameras,
            device=device,
            target_fps=args.target_fps,
            tiles_per_frame=args.tiles_per_frame,
        )
    except ValueError as exc:
        print(f"\nNo se pudo recomendar: {exc}")
        return 1

    print("\n== Recomendación")
    print(f"  Modelo: RF-DETR {rec.model}  |  {rec.fps_per_camera:g} fps por cámara  |  {device}")
    print(f"  Mosaicos por frame: {rec.tiles_per_frame:g}")
    print(f"  Uso estimado de la capacidad: {rec.utilization:.0%}")
    for w in rec.warnings + hw.notes:
        print(f"  ⚠ {w}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "recommendation": rec.to_dict(),
        "results": [r.__dict__ for r in results],
        "hardware": hw.to_dict(),
    }
    args.out.write_text(
        "# Generado por tools/benchmark.py. El worker lee `recommendation`.\n"
        + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nRecomendación guardada en {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
