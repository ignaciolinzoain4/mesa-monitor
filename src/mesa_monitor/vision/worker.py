"""Vision-worker.

F0: solo espera a la base y queda en reposo. El pipeline (captura → RF-DETR → OC-SORT →
zonas → state engine) se implementa en F3/F4.
"""

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import text

from mesa_monitor.config import get_settings
from mesa_monitor.db.session import make_engine
from mesa_monitor.log import setup_logging

log = logging.getLogger("mesa_monitor.worker")


async def wait_for_database(database_url: str, max_delay_s: float = 30.0) -> None:
    engine = make_engine(database_url)
    delay = 1.0
    try:
        while True:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                return
            except Exception as exc:  # reintenta con backoff exponencial
                log.warning("Base no disponible (%s). Reintento en %.0f s", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay_s)
    finally:
        await engine.dispose()


async def run() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows sin Docker
            loop.add_signal_handler(sig, stop.set)

    await wait_for_database(settings.database_url)
    log.info("Worker conectado a la base. Pipeline de visión todavía no implementado (F3).")
    await stop.wait()
    log.info("Worker detenido.")


def main() -> None:
    setup_logging(get_settings().log_level)
    asyncio.run(run())
