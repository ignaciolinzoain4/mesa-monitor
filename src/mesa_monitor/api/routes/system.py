"""Salud del sistema (SPEC §11.2): worker (fps, latencia, GPU), cámaras, host."""

from datetime import UTC, datetime
from typing import Any

import psutil
from fastapi import APIRouter, Request
from pydantic import BaseModel

from mesa_monitor import __version__
from mesa_monitor.api.deps import AuthUser, DbSession
from mesa_monitor.api.state_view import camera_states
from mesa_monitor.core.events import CameraStatusData
from mesa_monitor.db.models import AppSetting

router = APIRouter(prefix="/system", tags=["system"])


class SystemHealth(BaseModel):
    api_version: str
    now: datetime
    worker: dict[str, Any] | None
    worker_age_s: float | None
    worker_ok: bool
    cameras: list[CameraStatusData]
    host: dict[str, Any]
    websocket_clients: int


@router.get("/health", response_model=SystemHealth)
async def system_health(request: Request, db: DbSession, _: AuthUser) -> SystemHealth:
    now = datetime.now(UTC)
    row = await db.get(AppSetting, "worker_status")
    worker = row.value if row else None
    age = (now - row.updated_at).total_seconds() if row else None
    mem = psutil.virtual_memory()
    return SystemHealth(
        api_version=__version__,
        now=now,
        worker=worker,
        worker_age_s=round(age, 1) if age is not None else None,
        worker_ok=age is not None and age < 60,
        cameras=await camera_states(db, request.app.state.camera_monitor),
        host={
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": mem.percent,
            "memory_total_gb": round(mem.total / 1024**3, 1),
        },
        websocket_clients=request.app.state.events_hub.subscribers,
    )
