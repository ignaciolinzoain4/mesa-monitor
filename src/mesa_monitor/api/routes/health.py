"""`GET /api/v1/health`: estado del servicio y de la base."""

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from mesa_monitor import __version__

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "error"]
    ts: datetime


@router.get("/health", response_model=HealthOut)
async def get_health(request: Request, response: Response) -> HealthOut:
    engine: AsyncEngine = request.app.state.engine
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        database: Literal["ok", "error"] = "ok"
    except Exception:
        log.exception("La base de datos no responde")
        database = "error"

    if database == "error":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
        ts=datetime.now(UTC),
    )
