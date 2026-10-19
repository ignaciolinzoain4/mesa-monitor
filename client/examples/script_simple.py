"""Ejemplo 1 — script simple: estado de las mesas, alertas activas, métricas de hoy y alertas en vivo.

Requisitos:  pip install "git+https://github.com/ignaciolinzoain4/mesa-monitor.git#subdirectory=client"
Variables de entorno (archivo .env o sistema): MESA_MONITOR_URL y MESA_MONITOR_API_KEY.
"""

import os
import time
from datetime import datetime, timedelta

from mesa_monitor_client import MesaMonitorClient, MesaMonitorError

URL = os.environ.get("MESA_MONITOR_URL", "http://localhost:8000")
API_KEY = os.environ["MESA_MONITOR_API_KEY"]  # nunca escribirla en el código

client = MesaMonitorClient(base_url=URL, api_key=API_KEY)

try:
    estado = client.get_state()
except MesaMonitorError as e:  # mensajes en español, listos para mostrar
    print("No se pudo leer el estado:", e)
    raise SystemExit(1) from e

print("== Mesas")
for mesa in estado.tables:
    minutos = (mesa.elapsed_seconds or 0) / 60
    print(
        f"  Mesa {mesa.table.label:>4}  {mesa.state.value:<20}  {minutos:5.1f} min  {mesa.people} personas"
    )

print("== Alertas activas")
for a in client.get_active_alerts():
    print(f"  {a.where}: {a.alert_type.value} ({a.severity.value})")

print("== Hoy")
hoy = client.get_metrics(from_=datetime.now().astimezone() - timedelta(hours=12))
if hoy.groups:
    g = hoy.groups[0]
    espera = (g.t_esp.avg or 0) / 60
    print(f"  {g.n_sessions} sesiones · espera promedio {espera:.1f} min · a tiempo {g.sla_pct} %")


@client.on("alert.opened")
def nueva_alerta(event):  # type: ignore[no-untyped-def]
    a = event.data
    print(
        f"¡ALERTA! {a.where}: {a.alert_type.value} hace {int((a.elapsed_seconds or 0) // 60)} min"
    )


@client.on("alert.resolved")
def alerta_resuelta(event):  # type: ignore[no-untyped-def]
    print(f"Resuelta: {event.data.where}")


print("== Escuchando alertas en vivo (Ctrl+C para salir)")
client.start_in_background()
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    client.close()
