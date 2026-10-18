"""Identificadores públicos con prefijo + ULID (ordenables por tiempo), ej. `alr_01JABC...`."""

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid(timestamp_ms: int | None = None) -> str:
    """ULID de 26 caracteres: 48 bits de timestamp (ms) + 80 bits aleatorios."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    value = (ts << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def new_id(prefix: str) -> str:
    return f"{prefix}_{ulid()}"
