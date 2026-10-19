"""Genera datos de DEMOSTRACIÓN (sintéticos) para probar el dashboard de análisis.

NO son datos reales. Las sesiones creadas llevan el prefijo `demo_` y se borran con --clear.
Usa las mesas existentes (o crea 12 mesas de ejemplo si no hay ninguna).

    uv run python tools/demo_data.py --days 14
    uv run python tools/demo_data.py --clear
"""

import argparse
import asyncio
import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from mesa_monitor.config import get_settings
from mesa_monitor.core.enums import SessionCloseReason
from mesa_monitor.core.schedule import business_date
from mesa_monitor.db.models import StaffVisit, Table, TableSession
from mesa_monitor.db.session import make_engine, make_sessionmaker

PEAKS = {9: 0.6, 10: 0.8, 13: 1.0, 14: 0.9, 18: 0.7, 19: 0.9, 20: 1.0, 21: 1.0, 22: 0.7}


async def clear(sm) -> int:  # type: ignore[no-untyped-def]
    async with sm() as db:
        await db.execute(delete(StaffVisit).where(StaffVisit.id.like("demo_%")))
        res = await db.execute(delete(TableSession).where(TableSession.id.like("demo_%")))
        await db.commit()
        return int(res.rowcount or 0)  # type: ignore[attr-defined]


async def generate(days: int, seed: int) -> None:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    engine = make_engine(settings.database_url)
    sm = make_sessionmaker(engine)
    rng = random.Random(seed)  # noqa: S311  (datos de prueba)
    await clear(sm)
    async with sm() as db:
        tables = list((await db.execute(select(Table).where(Table.enabled))).scalars())
        if not tables:
            for i in range(1, 13):
                db.add(
                    Table(
                        id=f"demo_t{i}",
                        label=str(i),
                        sector="salon" if i <= 8 else "galeria",
                        plan_x=2 + ((i - 1) % 6) * 8,
                        plan_y=4 + ((i - 1) // 6) * 10,
                        plan_w=5,
                        plan_h=5,
                    )
                )
            await db.flush()
            tables = list((await db.execute(select(Table))).scalars())
        n = 0
        visits: list[StaffVisit] = []
        today = datetime.now(tz).date()
        for d in range(days, 0, -1):
            day = today - timedelta(days=d)
            for t in tables:
                cursor = datetime.combine(day, time(8, rng.randint(0, 59)), tz)
                close = datetime.combine(day, time(23, 30), tz)
                slow = rng.random() < 0.2  # algunas mesas peor ubicadas
                while cursor < close:
                    load = PEAKS.get(cursor.hour, 0.35)
                    if rng.random() > load:
                        cursor += timedelta(minutes=rng.randint(10, 40))
                        continue
                    seated = cursor
                    unattended = rng.random() < 0.03
                    wait = rng.lognormvariate(
                        4.6 + 0.6 * load + (0.4 if slow else 0), 0.5
                    )  # ~100–400 s
                    stay = rng.uniform(20, 90) * 60
                    first = None if unattended else seated + timedelta(seconds=wait)
                    vacated = seated + timedelta(
                        seconds=min(stay, wait + 600) if unattended else stay
                    )
                    dirty = rng.lognormvariate(5.2, 0.6)
                    cleaned = vacated + timedelta(seconds=dirty)
                    sid = f"demo_{n:06d}"
                    db.add(
                        TableSession(
                            id=sid,
                            table_id=t.id,
                            business_date=business_date(seated, tz),
                            seated_at=seated.astimezone(UTC),
                            first_attention_at=first.astimezone(UTC) if first else None,
                            vacated_at=vacated.astimezone(UTC),
                            cleaned_at=cleaned.astimezone(UTC),
                            closed_at=cleaned.astimezone(UTC),
                            close_reason=SessionCloseReason.CLEANED,
                            max_people=rng.randint(1, 5),
                        )
                    )
                    if first:
                        v = first
                        k = 0
                        while v < vacated:
                            visits.append(
                                StaffVisit(
                                    id=f"{sid}_{k}",
                                    session_id=sid,
                                    table_id=t.id,
                                    started_at=v.astimezone(UTC),
                                    ended_at=(v + timedelta(seconds=30)).astimezone(UTC),
                                )
                            )
                            k += 1
                            v += timedelta(seconds=rng.uniform(5, 25) * 60)
                    n += 1
                    cursor = cleaned + timedelta(minutes=rng.randint(2, 30))
        await db.flush()  # sesiones antes que visitas (FK)
        db.add_all(visits)
        await db.commit()
    await engine.dispose()
    print(f"Listo: {n} sesiones de demostración en {days} días ({len(tables)} mesas).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--clear", action="store_true", help="borrar los datos de demostración y salir")
    args = ap.parse_args()
    if args.clear:
        settings = get_settings()
        engine = make_engine(settings.database_url)
        n = asyncio.run(clear(make_sessionmaker(engine)))
        print(f"Borradas {n} sesiones de demostración.")
        return
    asyncio.run(generate(args.days, args.seed))


if __name__ == "__main__":
    main()
