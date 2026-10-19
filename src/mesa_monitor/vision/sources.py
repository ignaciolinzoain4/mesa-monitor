"""Fuentes de video: RTSP en vivo (vía go2rtc) y archivos (modo replay). PyAV (BSD).

`RtspSource` decodifica en un hilo y guarda solo el último frame (si la detección se atrasa,
se descartan frames viejos en vez de acumular latencia). Reconecta con backoff exponencial.
"""

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import av
import numpy as np
from numpy.typing import NDArray

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    ts: datetime
    image: NDArray[np.uint8]  # RGB


class RtspSource:
    def __init__(self, camera_id: str, url: str, max_backoff_s: float = 30.0) -> None:
        self.camera_id = camera_id
        self.url = url
        self.max_backoff_s = max_backoff_s
        self._lock = threading.Lock()
        self._latest: Frame | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.frames_decoded = 0
        self.last_error: str | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name=f"rtsp-{self.camera_id}", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> Frame | None:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                with av.open(
                    self.url,
                    options={"rtsp_transport": "tcp", "stimeout": "5000000", "timeout": "5000000"},
                    timeout=10,
                ) as container:
                    stream = container.streams.video[0]
                    stream.thread_type = "AUTO"
                    log.info("Cámara %s: conectada", self.camera_id)
                    backoff = 1.0
                    self.last_error = None
                    for packet in container.demux(stream):
                        if self._stop.is_set():
                            return
                        for f in packet.decode():
                            img = np.asarray(f.to_ndarray(format="rgb24"), dtype=np.uint8)
                            with self._lock:
                                self._latest = Frame(datetime.now(UTC), img)
                            self.frames_decoded += 1
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                log.warning(
                    "Cámara %s: sin señal (%s). Reintento en %.0f s",
                    self.camera_id,
                    self.last_error,
                    backoff,
                )
            if self._stop.wait(backoff):
                return
            backoff = min(backoff * 2, self.max_backoff_s)


def _first_pts_epoch(path: Path) -> float | None:
    with av.open(str(path)) as c:
        for f in c.decode(video=0):
            return float(f.time) if f.time is not None else None
    return None


def file_frames(
    path: Path,
    fps: float,
    start: datetime | None = None,
    max_seconds: float | None = None,
    skip_seconds: float = 0.0,
    pts_tz: ZoneInfo | None = None,
) -> Iterator[Frame]:
    """Frames de un archivo muestreados a `fps`, con timestamps reales.

    Los videos exportados de un NVR Dahua (.dav) traen la hora como pts (epoch), pero en **hora
    local** guardada como si fuera UTC: con `pts_tz` se interpreta en esa zona horaria. Si el
    video no trae hora, se usa `start` (o ahora) + el tiempo del video.
    """
    first = _first_pts_epoch(path)
    epoch_pts = first is not None and first > 1e9
    base = start or datetime.now(UTC)
    step = 1.0 / fps
    next_t: float | None = None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for f in container.decode(stream):
            if f.time is None:
                continue
            rel = f.time - (first or 0.0)
            if rel < skip_seconds:
                continue
            if max_seconds is not None and rel - skip_seconds > max_seconds:
                return
            if next_t is not None and rel < next_t:
                continue
            next_t = rel + step if next_t is None else next_t + step
            if next_t < rel:  # el video tiene un salto: realinear
                next_t = rel + step
            if epoch_pts and start is None:
                wall = datetime.fromtimestamp(float(f.time), UTC)
                ts = wall.replace(tzinfo=pts_tz).astimezone(UTC) if pts_tz else wall
            else:
                ts = base + timedelta(seconds=rel)
            yield Frame(ts, np.asarray(f.to_ndarray(format="rgb24"), dtype=np.uint8))


class Pacer:
    """Marca el ritmo de procesamiento (fps objetivo) sin acumular atraso."""

    def __init__(self, fps: float) -> None:
        self.period = 1.0 / fps
        self._next = time.monotonic()

    def wait(self) -> None:
        now = time.monotonic()
        if self._next > now:
            time.sleep(self._next - now)
            self._next += self.period
        else:
            self._next = now + self.period
