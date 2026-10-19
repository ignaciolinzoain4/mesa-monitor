"""F7: las métricas coinciden con un cálculo manual sobre datos de prueba."""

from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.helpers import api

pytestmark = pytest.mark.db

D = "2026-10-10"
PERIOD = {"from": f"{D}T20:00:00Z", "to": f"{D}T24:00:00Z".replace("24:00:00", "23:59:59")}

SESSIONS = [
    # id, mesa, sentados, atención, se fueron, limpia, cerrada, motivo
    ("a1", "A", "20:00", "20:02", "20:40", "20:45", "20:45", "cleaned"),
    ("a2", "A", "21:00", "21:05", "21:30", None, "21:31", "reoccupied"),
    ("b1", "B", "20:30", None, "20:50", "20:55", "20:55", "cleaned"),  # se fueron sin atención
    ("b2", "B", "22:00", "22:04", "23:00", None, "23:00", "vision_lost"),  # excluida
    ("c1", "C", "20:15", "20:16", "21:15", "21:20", "21:20", "cleaned"),
    ("c2", "C", "23:50", None, None, None, None, None),  # en curso
]
VISITS = [("a1", "A", "20:02"), ("a1", "A", "20:10"), ("a2", "A", "21:05"), ("c1", "C", "20:16")]


def ts(hhmm: str | None) -> datetime | None:
    return datetime.fromisoformat(f"{D}T{hhmm}:00+00:00") if hhmm else None


async def seed(db_url: str) -> None:
    engine = create_async_engine(db_url)
    insert_session = text(
        "INSERT INTO table_sessions (id, table_id, business_date, seated_at, first_attention_at,"
        " vacated_at, cleaned_at, closed_at, close_reason)"
        " VALUES (:id, :t, :d, :s, :a, :v, :cl, :c, :r)"
    )
    insert_visit = text(
        "INSERT INTO staff_visits (id, session_id, table_id, started_at) VALUES (:i, :s, :t, :ts)"
    )
    async with engine.begin() as c:
        for tid, sector in (("A", "salon"), ("B", "salon"), ("C", "galeria")):
            await c.execute(
                text("INSERT INTO tables (id, label, sector) VALUES (:i, :i, :s)"),
                {"i": tid, "s": sector},
            )
        for sid, tid, seated, att, vac, clean, closed, reason in SESSIONS:
            await c.execute(
                insert_session,
                {
                    "id": sid,
                    "t": tid,
                    "d": date.fromisoformat(D),
                    "s": ts(seated),
                    "a": ts(att),
                    "v": ts(vac),
                    "cl": ts(clean),
                    "c": ts(closed),
                    "r": reason,
                },
            )
        for i, (sid, tid, start) in enumerate(VISITS):
            await c.execute(insert_visit, {"i": f"v{i}", "s": sid, "t": tid, "ts": ts(start)})
    await engine.dispose()


async def test_summary_matches_manual_calculation(clean_db: str, tmp_path: Path) -> None:
    await seed(clean_db)
    async with api(clean_db, tmp_path, timezone="UTC") as c:
        total = (await c.get("/metrics/summary", params=PERIOD)).json()
        (g,) = total["groups"]
        # completas: a1, a2, b1, c1  (b2 excluida por visión, c2 en curso)
        assert (g["n_sessions"], g["n_excluded"], g["n_in_progress"], g["n_unattended"]) == (
            4,
            1,
            1,
            1,
        )
        # T_esp = [120, 300, 60] → promedio 160, mediana 120, p90 = 120 + 0.8·(300−120) = 264
        assert g["t_esp"] == {"n": 3, "avg": 160.0, "median": 120.0, "p90": 264.0}
        # T_est = [2400, 1800, 1200, 3600]; T_lim = [300, 300, 300]
        assert g["t_est"]["avg"] == 2250.0
        assert g["t_lim"] == {"n": 3, "avg": 300.0, "median": 300.0, "p90": 300.0}
        # G_max: a1 = max(0, 480, 1800) = 1800; a2 = 1500; c1 = 3540 (20:16 → 21:15)
        assert g["g_max"]["n"] == 3
        assert g["g_max"]["avg"] == pytest.approx((1800 + 1500 + 3540) / 3, abs=0.1)
        # SLA (U₁ = 180 s): a1 (120) y c1 (60) → 2/4; b1 sin atención cuenta como fuera de SLA
        assert g["sla_pct"] == 50.0
        # sin horario cargado: abierto todo el período (~4 h) · 3 mesas
        hours = total["operating_hours"]
        assert hours == pytest.approx(4, abs=0.25)
        assert g["rotation"] == pytest.approx(4 / (3 * hours), abs=1e-3)
        assert g["occupancy_pct"] == pytest.approx(9000 / (3 * hours * 3600) * 100, abs=0.1)
        assert g["visits_avg"] == pytest.approx(round((2 + 1 + 0 + 1) / 4, 1))

        by_table = {
            x["key"]: x
            for x in (
                await c.get("/metrics/summary", params={**PERIOD, "group_by": "table"})
            ).json()["groups"]
        }
        assert by_table["A"]["n_sessions"] == 2
        assert by_table["A"]["t_esp"]["avg"] == 210.0
        assert by_table["A"]["sla_pct"] == 50.0
        assert by_table["A"]["rotation"] == pytest.approx(2 / hours, abs=1e-3)

        by_sector = {
            x["key"]: x
            for x in (
                await c.get("/metrics/summary", params={**PERIOD, "group_by": "sector"})
            ).json()["groups"]
        }
        assert by_sector["salon"]["n_sessions"] == 3
        assert by_sector["salon"]["n_tables"] == 2
        assert by_sector["galeria"]["n_sessions"] == 1

        by_hour = {
            x["key"]: x
            for x in (
                await c.get("/metrics/summary", params={**PERIOD, "group_by": "hour"})
            ).json()["groups"]
        }
        assert by_hour["20"]["n_sessions"] == 3  # a1, b1, c1
        assert by_hour["20"]["operating_hours"] == 1.0
        assert by_hour["20"]["rotation"] == pytest.approx(3 / 3, abs=1e-3)
        assert by_hour["21"]["n_sessions"] == 1
        keys = [
            x["key"]
            for x in (
                await c.get("/metrics/summary", params={**PERIOD, "group_by": "hour"})
            ).json()["groups"]
        ]
        assert keys == sorted(keys, key=int)

        # Excepción por mesa: U₁ de la mesa A = 400 s → a2 (300 s) entra en SLA → 3/4
        r = await c.put(
            "/config/thresholds",
            json=[
                {
                    "scope": "table",
                    "scope_ref": "A",
                    "key": "alert_sin_atencion_warning_s",
                    "value": 400,
                }
            ],
        )
        assert r.status_code == 200
        (g2,) = (await c.get("/metrics/summary", params=PERIOD)).json()["groups"]
        assert g2["sla_pct"] == 75.0

        heat = (await c.get("/metrics/heatmap", params=PERIOD)).json()
        cells = {(h["weekday"], h["hour"]): h for h in heat}
        assert cells[(5, 20)]["n"] == 2  # 2026-10-10 es sábado (5): a1 y c1 tienen T_esp
        dist = (await c.get("/metrics/distribution", params={**PERIOD, "bin_seconds": 60})).json()
        assert {d["from_s"]: d["n"] for d in dist} == {60: 1, 120: 1, 300: 1}

        csv_text = (await c.get("/metrics/sessions.csv", params=PERIOD)).text
        assert csv_text.startswith("﻿mesa,sector,fecha")
        assert csv_text.count("\n") == 1 + 6
        summary_csv = (
            await c.get("/metrics/summary.csv", params={**PERIOD, "group_by": "table"})
        ).text
        assert "espera_p90_min" in summary_csv


async def test_shift_filter_and_operating_hours(clean_db: str, tmp_path: Path) -> None:
    await seed(clean_db)
    async with api(clean_db, tmp_path, timezone="UTC") as c:
        await c.put(
            "/config/schedule",
            json={
                "operating_hours": [{"weekday": 5, "opens_at": "20:00", "closes_at": "22:00"}],
                "shifts": [
                    {"name": "temprano", "starts_at": "20:00", "ends_at": "20:30"},
                    {"name": "tarde", "starts_at": "20:30", "ends_at": "23:59"},
                ],
                "business_day_cutoff": "06:00",
            },
        )
        total = (await c.get("/metrics/summary", params=PERIOD)).json()
        assert total["operating_hours"] == 2.0
        early = (await c.get("/metrics/summary", params={**PERIOD, "shift": "temprano"})).json()[
            "groups"
        ][0]
        assert early["n_sessions"] == 2  # a1 20:00 y c1 20:15
        by_shift = {
            x["key"]: x
            for x in (
                await c.get("/metrics/summary", params={**PERIOD, "group_by": "shift"})
            ).json()["groups"]
        }
        assert by_shift["temprano"]["operating_hours"] == 0.5


async def test_invalid_ranges(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        r = await c.get(
            "/metrics/summary",
            params={"from": "2026-10-10T10:00:00Z", "to": "2026-10-10T09:00:00Z"},
        )
        assert r.status_code == 422
        assert (await c.get("/metrics/summary", params={"group_by": "employee"})).status_code == 422
