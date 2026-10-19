"""Caché en memoria de los últimos snapshots servidos (para calibrar sobre la misma imagen).

Solo en RAM, pocos elementos y por poco tiempo: no se guarda ninguna imagen en disco (SPEC §17).
"""

import time
from collections import OrderedDict

from mesa_monitor.core.ids import new_id


class SnapshotCache:
    def __init__(self, max_items: int = 20, ttl_s: float = 15 * 60) -> None:
        self._items: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self.max_items = max_items
        self.ttl_s = ttl_s

    def put(self, jpeg: bytes) -> str:
        sid = new_id("snp")
        self._items[sid] = (time.monotonic(), jpeg)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)
        return sid

    def get(self, sid: str) -> bytes | None:
        item = self._items.get(sid)
        if item is None:
            return None
        ts, jpeg = item
        if time.monotonic() - ts > self.ttl_s:
            del self._items[sid]
            return None
        return jpeg
