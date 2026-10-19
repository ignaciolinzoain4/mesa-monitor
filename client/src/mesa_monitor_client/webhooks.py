"""Verificación de webhooks enviados por Mesa Monitor (firma HMAC-SHA256)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from mesa_monitor_client.errors import FirmaInvalida
from mesa_monitor_client.models import Event

_adapter: TypeAdapter[Any] = TypeAdapter(Event)


def verify_signature(
    secret: str, timestamp: int, body: bytes, signature: str, tolerance_s: int = 300
) -> bool:
    if abs(time.time() - int(timestamp)) > tolerance_s:
        return False
    expected = (
        "sha256="
        + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature)


def parse_webhook(headers: Mapping[str, str], body: bytes, secret: str) -> Any:
    """Verifica la firma y devuelve el evento. Lanza `FirmaInvalida` si no corresponde."""
    h = {k.lower(): v for k, v in headers.items()}
    try:
        ts = int(h["x-mesa-timestamp"])
        sig = h["x-mesa-signature"]
    except (KeyError, ValueError) as exc:
        raise FirmaInvalida() from exc
    if not verify_signature(secret, ts, body, sig):
        raise FirmaInvalida()
    return _adapter.validate_python(json.loads(body))
