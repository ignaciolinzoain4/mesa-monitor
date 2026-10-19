"""Recolección opcional de recortes de personas (SPEC §7.3) para entrenar a futuro un
clasificador personal/cliente (`ModelStaffClassifier`).

APAGADA por defecto. Si se activa (Configuración → Visión): guarda como mucho un recorte por
persona cada `every_s` segundos, con una etiqueta AUTOMÁTICA (a revisar a mano) y sin ningún dato
de identidad. Se borran solos a los `retention_days` días. Carpeta fuera del repo (volumen Docker).
Su uso tiene que estar informado a los empleados (docs/cumplimiento.md).
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from mesa_monitor.core.ids import new_id
from mesa_monitor.vision.scene import SceneResult

log = logging.getLogger(__name__)


class CropCollector:
    def __init__(self, directory: Path, retention_days: float = 7, every_s: float = 10) -> None:
        self.dir = directory
        self.retention_days = retention_days
        self.every_s = every_s
        self._last: dict[tuple[str, int], float] = {}
        self._last_cleanup = 0.0

    def collect(
        self, camera_id: str, frame: NDArray[np.uint8], result: SceneResult, ts: datetime
    ) -> int:
        h, w = frame.shape[:2]
        now = time.monotonic()
        saved = 0
        day_dir = self.dir / ts.strftime("%Y-%m-%d")
        for p in result.people:
            tid = p.box.track_id
            if tid is None or now - self._last.get((camera_id, tid), -1e9) < self.every_s:
                continue
            self._last[(camera_id, tid)] = now
            x1, y1 = max(0, int(p.box.x1 * w)), max(0, int(p.box.y1 * h))
            x2, y2 = min(w, int(p.box.x2 * w)), min(h, int(p.box.y2 * h))
            if x2 - x1 < 16 or y2 - y1 < 32:
                continue
            day_dir.mkdir(parents=True, exist_ok=True)
            name = new_id("crop")
            Image.fromarray(frame[y1:y2, x1:x2]).save(day_dir / f"{name}.jpg", quality=85)
            meta = {
                "camera_id": camera_id,
                "ts": ts.isoformat(),
                "auto_label": "staff" if p.is_staff else "customer",  # a revisar a mano
                "standing": p.standing,
                "table_id": p.table_id,
            }
            (day_dir / f"{name}.json").write_text(json.dumps(meta), encoding="utf-8")
            saved += 1
        if now - self._last_cleanup > 3600:
            self._last_cleanup = now
            self.cleanup()
        return saved

    def cleanup(self) -> int:
        """Borra recortes más viejos que la retención."""
        if not self.dir.is_dir():
            return 0
        limit = time.time() - self.retention_days * 86400
        removed = 0
        for f in self.dir.rglob("crop_*"):
            if f.stat().st_mtime < limit:
                f.unlink(missing_ok=True)
                removed += 1
        for d in sorted(self.dir.glob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        return removed
