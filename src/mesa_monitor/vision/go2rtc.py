"""Integración con go2rtc: generación de su config y cliente HTTP para snapshots.

Por cada cámara se publican dos streams en go2rtc:
- `<id>`       → substream (subtype=1), liviano: lo usa la detección.
- `<id>_main`  → main stream (subtype=0): solo para snapshots de calibración.

Generar la config (la usa el servicio `go2rtc-config` de Docker Compose):
    python -m mesa_monitor.vision.go2rtc \
        --cameras config/local/cameras.yaml --out data/go2rtc/go2rtc.yaml
"""

import argparse
import os
import struct
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx
import yaml

from mesa_monitor.config.cameras import (
    MAIN_STREAM,
    SUBSTREAM,
    CamerasConfigError,
    CamerasFile,
    load_cameras,
)

HEADER = (
    "# GENERADO AUTOMÁTICAMENTE desde cameras.yaml por mesa_monitor.vision.go2rtc.\n"
    "# No editar a mano. Contiene credenciales: no copiar fuera de esta PC.\n"
)


class StreamKind(StrEnum):
    SUB = "sub"
    MAIN = "main"


def stream_name(camera_id: str, kind: StreamKind) -> str:
    return camera_id if kind is StreamKind.SUB else f"{camera_id}_main"


def build_go2rtc_config(cameras: CamerasFile) -> dict[str, object]:
    streams: dict[str, str] = {}
    for cam in cameras.cameras:
        if not cam.enabled:
            continue
        streams[stream_name(cam.id, StreamKind.SUB)] = cameras.stream_url(cam, SUBSTREAM)
        streams[stream_name(cam.id, StreamKind.MAIN)] = cameras.stream_url(cam, MAIN_STREAM)
    return {
        "api": {"listen": ":1984"},
        "rtsp": {"listen": ":8554"},
        "webrtc": {"listen": ":8555"},
        "streams": streams,
    }


def render_go2rtc_config(cameras: CamerasFile) -> str:
    body = yaml.safe_dump(build_go2rtc_config(cameras), sort_keys=False, allow_unicode=True)
    return HEADER + body


def write_go2rtc_config(cameras: CamerasFile, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(render_go2rtc_config(cameras), encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, out)


# --------------------------------------------------------------------------- cliente


class SnapshotError(Exception):
    """No se pudo obtener un frame de la cámara."""


@dataclass(frozen=True)
class Frame:
    jpeg: bytes
    width: int | None
    height: int | None


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Ancho y alto leyendo el marcador SOF del JPEG (sin decodificar la imagen)."""
    if not data.startswith(b"\xff\xd8"):
        return None
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if i + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return width, height
        i += 2 + length
    return None


class Go2rtcClient:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, transport=transport, timeout=15.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def frame(self, camera_id: str, kind: StreamKind = StreamKind.SUB) -> Frame:
        src = stream_name(camera_id, kind)
        try:
            resp = await self._http.get("/api/frame.jpeg", params={"src": src})
        except httpx.HTTPError as exc:
            raise SnapshotError(f"go2rtc no responde: {exc.__class__.__name__}") from exc
        if resp.status_code == 404:
            raise SnapshotError(f"go2rtc no conoce el stream {src!r} (¿config desactualizada?)")
        if resp.status_code != 200:
            raise SnapshotError(f"go2rtc respondió {resp.status_code}")
        # Ojo: si la cámara está caída, go2rtc responde 200 con el cuerpo vacío.
        size = jpeg_size(resp.content)
        if size is None:
            raise SnapshotError("la cámara no entregó imagen (sin señal)")
        return Frame(jpeg=resp.content, width=size[0], height=size[1])


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Genera la config de go2rtc desde cameras.yaml")
    parser.add_argument("--cameras", type=Path, default=Path("config/local/cameras.yaml"))
    parser.add_argument("--out", type=Path, default=Path("data/go2rtc/go2rtc.yaml"))
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="si no existe cameras.yaml, generar una config sin cámaras en vez de fallar",
    )
    args = parser.parse_args(argv)
    if args.allow_missing and not args.cameras.is_file():
        write_go2rtc_config(CamerasFile(), args.out)
        print(f"Aviso: no existe {args.cameras}; go2rtc arranca sin cámaras.")
        return 0
    try:
        cameras = load_cameras(args.cameras)
    except CamerasConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    write_go2rtc_config(cameras, args.out)
    enabled = sum(c.enabled for c in cameras.cameras)
    print(f"Config de go2rtc generada en {args.out} ({enabled} cámaras habilitadas).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
