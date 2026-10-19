"""Ejemplo 3 — FastAPI (async): escucha eventos dentro del ciclo de la app.

pip install fastapi uvicorn
uvicorn fastapi_app:app --port 5001
"""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(os.environ["MESA_MONITOR_URL"], os.environ["MESA_MONITOR_API_KEY"])


@client.on("alert.opened")
async def guardar_alerta(event):  # type: ignore[no-untyped-def]
    # Acá iría, por ejemplo, guardar la alerta en la base del sistema de gestión.
    print("Alerta:", event.data.where, event.data.alert_type.value)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    tarea = asyncio.create_task(client.run_forever())
    yield
    client.stop()
    tarea.cancel()


app = FastAPI(lifespan=lifespan)


@app.get("/mesas")
async def mesas():  # type: ignore[no-untyped-def]
    estado = await client.aget_state()
    return [
        {
            "mesa": m.table.label,
            "estado": m.state.value,
            "minutos": round((m.elapsed_seconds or 0) / 60, 1),
        }
        for m in estado.tables
    ]


@app.get("/alertas")
async def alertas():  # type: ignore[no-untyped-def]
    return [a.model_dump(mode="json") for a in await client.aget_active_alerts()]
