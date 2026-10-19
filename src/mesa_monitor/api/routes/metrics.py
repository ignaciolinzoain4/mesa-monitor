"""Métricas (SPEC §9, §12.1): resumen agrupado, mapa de calor, distribución y export CSV."""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from mesa_monitor.api import metrics_service as ms
from mesa_monitor.api.deps import AuthUser, DbSession
from mesa_monitor.core.metrics import MetricsGroupBy

router = APIRouter(prefix="/metrics", tags=["metrics"])

FromQ = Annotated[
    datetime | None, Query(alias="from", description="Desde (ISO-8601). Default: 7 días")
]
ToQ = Annotated[datetime | None, Query(description="Hasta (ISO-8601). Default: ahora")]


def _filters(
    from_: datetime | None,
    to: datetime | None,
    sector: str | None,
    table: str | None,
    shift: str | None,
) -> ms.Filters:
    end = (to or datetime.now(UTC)).astimezone(UTC)
    start = (from_ or end - timedelta(days=7)).astimezone(UTC)
    if start >= end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="'from' tiene que ser anterior a 'to'"
        )
    if end - start > timedelta(days=400):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Rango máximo: 400 días")
    return ms.Filters(start, end, sector or None, table or None, shift or None)


@router.get("/summary")
async def metrics_summary(
    request: Request,
    db: DbSession,
    _: AuthUser,
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = None,
    shift: str | None = None,
    group_by: MetricsGroupBy | None = None,
) -> dict[str, Any]:
    f = _filters(from_, to, sector, table, shift)
    ctx = await ms.load_context(db, request.app.state.settings.tz, f)
    return await ms.summary(db, ctx, f, group_by)


@router.get("/heatmap")
async def metrics_heatmap(
    request: Request,
    db: DbSession,
    _: AuthUser,
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = None,
    shift: str | None = None,
) -> list[dict[str, Any]]:
    """Espera hasta la primera atención (T_esp) por día de semana × hora."""
    f = _filters(from_, to, sector, table, shift)
    ctx = await ms.load_context(db, request.app.state.settings.tz, f)
    return await ms.heatmap(db, ctx, f)


@router.get("/distribution")
async def metrics_distribution(
    request: Request,
    db: DbSession,
    _: AuthUser,
    metric: Literal["t_esp", "t_est", "t_lim", "g_max"] = "t_esp",
    bin_seconds: int = Query(default=60, ge=10, le=3600),
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = None,
    shift: str | None = None,
) -> list[dict[str, Any]]:
    f = _filters(from_, to, sector, table, shift)
    ctx = await ms.load_context(db, request.app.state.settings.tz, f)
    return await ms.distribution(db, ctx, f, metric, bin_seconds)


def _csv(rows: list[dict[str, Any]], filename: str) -> Response:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()})
    # BOM para que Excel abra bien los acentos
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sessions.csv", response_class=Response)
async def sessions_csv(
    request: Request,
    db: DbSession,
    _: AuthUser,
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = None,
    shift: str | None = None,
) -> Response:
    f = _filters(from_, to, sector, table, shift)
    ctx = await ms.load_context(db, request.app.state.settings.tz, f)
    return _csv(await ms.session_rows(db, ctx, f), "sesiones.csv")


@router.get("/summary.csv", response_class=Response)
async def summary_csv(
    request: Request,
    db: DbSession,
    _: AuthUser,
    from_: FromQ = None,
    to: ToQ = None,
    sector: str | None = None,
    table: str | None = None,
    shift: str | None = None,
    group_by: MetricsGroupBy | None = None,
) -> Response:
    f = _filters(from_, to, sector, table, shift)
    ctx = await ms.load_context(db, request.app.state.settings.tz, f)
    data = await ms.summary(db, ctx, f, group_by)
    rows = []
    for g in data["groups"]:
        row = {
            "grupo": g["label"],
            "sesiones": g["n_sessions"],
            "sla_pct": g["sla_pct"],
            "rotacion": g["rotation"],
            "ocupacion_pct": g["occupancy_pct"],
            "se_fueron_sin_atencion": g["n_unattended"],
            "visitas_promedio": g["visits_avg"],
        }
        for m, name in (
            ("t_esp", "espera"),
            ("t_est", "estadia"),
            ("t_lim", "limpieza"),
            ("g_max", "mayor_intervalo"),
        ):
            for stat in ("avg", "median", "p90"):
                v = g[m][stat]
                row[f"{name}_{stat}_min"] = None if v is None else round(v / 60, 2)
        rows.append(row)
    return _csv(rows, f"resumen-{group_by.value if group_by else 'total'}.csv")
