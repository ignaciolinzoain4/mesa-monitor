"""Métricas de atención de mesas (SPEC §9), calculadas en SQL sobre la vista `session_metrics`.

Definiciones (por sesión): T_esp = primera atención − se sentaron; T_est = se fueron − se
sentaron; T_lim = limpia − se fueron; N_vis = visitas; G_max = mayor intervalo sin visita.
Agregados por grupo: promedio, mediana, p90; SLA = % de sesiones con T_esp ≤ U₁ (las que se
fueron sin ser atendidas cuentan como fuera de SLA); R = N_ses / (N_mesas · H_op);
O = Σ T_est / (N_mesas · H_op · 60) · 100. Cada sesión se asigna al grupo del momento en que se
sentaron. Se excluyen las sesiones incompletas (en curso o cerradas por pérdida de visión) y se
informa cuántas.

RESTRICCIÓN (SPEC Anexo A): solo se agrupa por `core.metrics.MetricsGroupBy` (mesa, sector,
hora, día, día de semana, turno). Nunca por persona.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.core.metrics import MetricsGroupBy
from mesa_monitor.core.schedule import OpeningSpan, ShiftSpan, business_date, open_interval
from mesa_monitor.core.thresholds import ThresholdRow, resolve
from mesa_monitor.db.models import OperatingHours, Shift, Table, Threshold

WEEKDAYS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
METRICS = ("t_esp", "t_est", "t_lim", "g_max")
SLOT_MIN = 15


@dataclass
class Filters:
    start: datetime
    end: datetime
    sector: str | None = None
    table: str | None = None  # id o etiqueta
    shift: str | None = None


@dataclass
class Context:
    tz: ZoneInfo
    tables: list[Table]
    hours: list[OpeningSpan]
    shifts: list[ShiftSpan]
    u1_by_table: dict[str, float]
    u1_default: float
    cutoff: Any = None
    table_ids: list[str] = field(default_factory=list)


async def load_context(db: AsyncSession, tz: ZoneInfo, f: Filters) -> Context:
    tables = list((await db.execute(select(Table).order_by(Table.label))).scalars())
    rows = [
        ThresholdRow(r.scope, r.scope_ref, r.key, r.value, r.valid_from, r.valid_to)
        for r in (await db.execute(select(Threshold))).scalars()
    ]
    u1 = {
        t.id: float(resolve(rows, table_id=t.id, sector=t.sector)["alert_sin_atencion_warning_s"])
        for t in tables
    }
    hours = [
        OpeningSpan(h.weekday, h.opens_at, h.closes_at)
        for h in (await db.execute(select(OperatingHours))).scalars()
    ]
    shifts = [
        ShiftSpan(s.name, s.starts_at, s.ends_at)
        for s in (await db.execute(select(Shift).order_by(Shift.starts_at))).scalars()
    ]
    selected = [
        t
        for t in tables
        if t.enabled
        and (f.sector is None or t.sector == f.sector)
        and (f.table is None or f.table in (t.id, t.label))
    ]
    return Context(
        tz=tz,
        tables=tables,
        hours=hours,
        shifts=shifts,
        u1_by_table=u1,
        u1_default=float(resolve(rows)["alert_sin_atencion_warning_s"]),
        table_ids=[t.id for t in selected],
    )


def _shift_case(shifts: list[ShiftSpan], params: dict[str, Any]) -> str:
    if not shifts:
        return "NULL"
    parts = []
    for i, s in enumerate(shifts):
        params[f"sh{i}_a"], params[f"sh{i}_b"], params[f"sh{i}_n"] = s.starts_at, s.ends_at, s.name
        lt = "(m.seated_at AT TIME ZONE :tz)::time"
        if s.starts_at <= s.ends_at:
            cond = f"{lt} >= :sh{i}_a AND {lt} < :sh{i}_b"
        else:
            cond = f"({lt} >= :sh{i}_a OR {lt} < :sh{i}_b)"
        parts.append(f"WHEN {cond} THEN :sh{i}_n")
    return "CASE " + " ".join(parts) + " END"


def _group_expr(group_by: MetricsGroupBy | None, shift_case: str) -> tuple[str, str]:
    """(expresión de agrupación, expresión de etiqueta)."""
    if group_by is None:
        return "'total'", "'Total'"
    return {
        MetricsGroupBy.HOUR: ("EXTRACT(HOUR FROM m.seated_at AT TIME ZONE :tz)::int::text", "NULL"),
        MetricsGroupBy.DAY: ("m.business_date::text", "NULL"),
        MetricsGroupBy.WEEKDAY: (
            "(EXTRACT(ISODOW FROM m.seated_at AT TIME ZONE :tz)::int - 1)::text",
            "NULL",
        ),
        MetricsGroupBy.SHIFT: (f"COALESCE({shift_case}, 'Fuera de turno')", "NULL"),
        MetricsGroupBy.TABLE: ("m.table_id", "m.table_label"),
        MetricsGroupBy.SECTOR: ("m.sector", "NULL"),
    }[group_by]


def _where(f: Filters, ctx: Context, params: dict[str, Any], shift_case: str) -> str:
    params.update(start=f.start, end=f.end, tz=str(ctx.tz))
    clauses = ["m.seated_at >= :start", "m.seated_at < :end"]
    if f.sector:
        clauses.append("m.sector = :sector")
        params["sector"] = f.sector
    if f.table:
        clauses.append("(m.table_id = :tbl OR m.table_label = :tbl)")
        params["tbl"] = f.table
    if f.shift:
        clauses.append(f"{shift_case} = :shift")
        params["shift"] = f.shift
    return " AND ".join(clauses)


def _stat_cols() -> str:
    cols = []
    for m in METRICS:
        cols += [
            f"count(m.{m}) FILTER (WHERE m.complete) AS {m}_n",
            f"avg(m.{m}) FILTER (WHERE m.complete) AS {m}_avg",
            f"percentile_cont(0.5) WITHIN GROUP (ORDER BY m.{m}) FILTER (WHERE m.complete) AS {m}_p50",
            f"percentile_cont(0.9) WITHIN GROUP (ORDER BY m.{m}) FILTER (WHERE m.complete) AS {m}_p90",
        ]
    return ",\n".join(cols)


def _slots(f: Filters, ctx: Context) -> list[datetime]:
    """Inicios de franjas de 15 min en que el local estuvo abierto dentro del período."""
    out = []
    t = f.start.astimezone(UTC).replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % SLOT_MIN)
    end = min(f.end, datetime.now(UTC))
    while t < end:
        if open_interval(t, ctx.hours, ctx.tz) is not None:
            out.append(t)
        t += timedelta(minutes=SLOT_MIN)
    return out


def _slot_key(t: datetime, group_by: MetricsGroupBy | None, ctx: Context) -> str:
    local = t.astimezone(ctx.tz)
    if group_by is None:
        return "total"
    if group_by is MetricsGroupBy.HOUR:
        return str(local.hour)
    if group_by is MetricsGroupBy.WEEKDAY:
        return str(local.weekday())
    if group_by is MetricsGroupBy.DAY:
        return business_date(t, ctx.tz).isoformat()
    if group_by is MetricsGroupBy.SHIFT:
        lt = local.time()
        return next((s.name for s in ctx.shifts if s.contains(lt)), "Fuera de turno")
    return "*"  # mesa / sector: todas las horas del período


async def summary(
    db: AsyncSession, ctx: Context, f: Filters, group_by: MetricsGroupBy | None
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "u_ids": list(ctx.u1_by_table),
        "u_vals": list(ctx.u1_by_table.values()),
        "u1_default": ctx.u1_default,
    }
    shift_case = _shift_case(ctx.shifts, params)
    where = _where(f, ctx, params, shift_case)
    gexpr, lexpr = _group_expr(group_by, shift_case)
    sql = f"""
        WITH u1 AS (
            SELECT * FROM unnest(CAST(:u_ids AS text[]), CAST(:u_vals AS float8[])) AS x(table_id, u1)
        )
        SELECT
            {gexpr} AS g,
            min({lexpr}) AS g_label,
            count(*) FILTER (WHERE m.complete) AS n_ses,
            count(*) FILTER (WHERE m.closed_at IS NULL) AS n_in_progress,
            count(*) FILTER (WHERE m.closed_at IS NOT NULL AND NOT m.complete) AS n_excluded,
            count(*) FILTER (WHERE m.complete AND m.first_attention_at IS NULL) AS n_unattended,
            count(*) FILTER (WHERE m.complete AND m.t_esp <= COALESCE(u1.u1, :u1_default)) AS n_sla,
            avg(m.n_vis) FILTER (WHERE m.complete) AS n_vis_avg,
            COALESCE(sum(m.t_est) FILTER (WHERE m.complete), 0) AS t_est_sum,
            {_stat_cols()}
        FROM session_metrics m
        LEFT JOIN u1 ON u1.table_id = m.table_id
        WHERE {where}
        GROUP BY 1
        ORDER BY 1
    """  # noqa: S608  (las expresiones son fijas; los valores van como parámetros)
    rows = (await db.execute(text(sql), params)).mappings().all()

    slots = _slots(f, ctx)
    slot_hours: dict[str, float] = defaultdict(float)
    for s in slots:
        slot_hours[_slot_key(s, group_by, ctx)] += SLOT_MIN / 60
    total_hours = len(slots) * SLOT_MIN / 60
    sector_tables: dict[str, int] = defaultdict(int)
    for t in ctx.tables:
        if t.id in ctx.table_ids:
            sector_tables[t.sector] += 1
    labels = {t.id: t.label for t in ctx.tables}

    groups = []
    for r in rows:
        key = str(r["g"])
        if group_by is MetricsGroupBy.TABLE:
            n_tables, h_op, label = 1, total_hours, r["g_label"] or labels.get(key, key)
        elif group_by is MetricsGroupBy.SECTOR:
            n_tables, h_op, label = sector_tables.get(key, 0), total_hours, key
        else:
            n_tables, h_op = len(ctx.table_ids), slot_hours.get(key, 0.0)
            label = (
                WEEKDAYS[int(key)]
                if group_by is MetricsGroupBy.WEEKDAY
                else (f"{int(key):02d}:00" if group_by is MetricsGroupBy.HOUR else key)
            )
        n = int(r["n_ses"])
        denom = n_tables * h_op
        groups.append(
            {
                "key": key,
                "label": label,
                "n_sessions": n,
                "n_in_progress": int(r["n_in_progress"]),
                "n_excluded": int(r["n_excluded"]),
                "n_unattended": int(r["n_unattended"]),
                "sla_pct": round(100 * int(r["n_sla"]) / n, 1) if n else None,
                "visits_avg": _round(r["n_vis_avg"]),
                "rotation": round(n / denom, 3) if denom else None,
                "occupancy_pct": round(float(r["t_est_sum"]) / (denom * 3600) * 100, 1)
                if denom
                else None,
                "operating_hours": round(h_op, 2),
                "n_tables": n_tables,
                **{
                    m: {
                        "n": int(r[f"{m}_n"]),
                        "avg": _round(r[f"{m}_avg"]),
                        "median": _round(r[f"{m}_p50"]),
                        "p90": _round(r[f"{m}_p90"]),
                    }
                    for m in METRICS
                },
            }
        )
    if group_by in (MetricsGroupBy.HOUR, MetricsGroupBy.WEEKDAY):
        groups.sort(key=lambda x: int(str(x["key"])))  # "8" antes que "10"
    return {
        "from": f.start,
        "to": f.end,
        "group_by": group_by.value if group_by else None,
        "units": "segundos (t_esp, t_est, t_lim, g_max); sesiones/h por mesa (rotation); % (sla, occupancy)",
        "sla_threshold_seconds": ctx.u1_default,
        "operating_hours": round(total_hours, 2),
        "n_tables": len(ctx.table_ids),
        "groups": groups,
    }


def _round(v: Any) -> float | None:
    return None if v is None else round(float(v), 1)


async def heatmap(db: AsyncSession, ctx: Context, f: Filters) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    shift_case = _shift_case(ctx.shifts, params)
    where = _where(f, ctx, params, shift_case)
    sql = f"""
        SELECT (EXTRACT(ISODOW FROM m.seated_at AT TIME ZONE :tz)::int - 1) AS weekday,
               EXTRACT(HOUR FROM m.seated_at AT TIME ZONE :tz)::int AS hour,
               count(m.t_esp) AS n,
               avg(m.t_esp) AS avg,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY m.t_esp) AS p90
        FROM session_metrics m
        WHERE {where} AND m.complete AND m.t_esp IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
    """  # noqa: S608
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [
        {
            "weekday": r["weekday"],
            "hour": r["hour"],
            "n": r["n"],
            "avg": _round(r["avg"]),
            "p90": _round(r["p90"]),
        }
        for r in rows
    ]


async def distribution(
    db: AsyncSession, ctx: Context, f: Filters, metric: str, bin_s: int
) -> list[dict[str, Any]]:
    if metric not in METRICS:
        raise ValueError("métrica desconocida")
    params: dict[str, Any] = {"bin": bin_s}
    shift_case = _shift_case(ctx.shifts, params)
    where = _where(f, ctx, params, shift_case)
    sql = f"""
        SELECT floor(m.{metric} / :bin)::int AS b, count(*) AS n
        FROM session_metrics m
        WHERE {where} AND m.complete AND m.{metric} IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """  # noqa: S608
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [{"from_s": r["b"] * bin_s, "to_s": (r["b"] + 1) * bin_s, "n": r["n"]} for r in rows]


async def session_rows(db: AsyncSession, ctx: Context, f: Filters) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    shift_case = _shift_case(ctx.shifts, params)
    where = _where(f, ctx, params, shift_case)
    sql = f"""
        SELECT m.table_label AS mesa, m.sector, m.business_date AS fecha,
               m.seated_at AS se_sentaron, m.first_attention_at AS primera_atencion,
               m.vacated_at AS se_fueron, m.cleaned_at AS limpia, m.close_reason AS cierre,
               m.max_people AS personas, m.n_vis AS visitas,
               round(m.t_esp::numeric / 60, 2) AS espera_min,
               round(m.t_est::numeric / 60, 2) AS estadia_min,
               round(m.t_lim::numeric / 60, 2) AS limpieza_min,
               round(m.g_max::numeric / 60, 2) AS mayor_intervalo_sin_visita_min,
               m.complete AS completa
        FROM session_metrics m WHERE {where} ORDER BY m.seated_at
    """  # noqa: S608
    rows = (await db.execute(text(sql), params)).mappings().all()
    return [dict(r) for r in rows]
