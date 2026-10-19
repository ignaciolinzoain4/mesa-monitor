"""Ejemplo 5 — Streamlit: tablero que se actualiza solo cada 5 segundos.

pip install streamlit
streamlit run streamlit_app.py
"""

import os
import time

import streamlit as st

from mesa_monitor_client import MesaMonitorClient, MesaMonitorError


@st.cache_resource  # un solo cliente para toda la app (no uno por recarga)
def get_client() -> MesaMonitorClient:
    c = MesaMonitorClient(os.environ["MESA_MONITOR_URL"], os.environ["MESA_MONITOR_API_KEY"])
    c.start_in_background()
    return c


client = get_client()
st.title("Mesas del local")

try:
    estado = client.get_state() if not client.tables else None
except MesaMonitorError as e:
    st.error(str(e))
    st.stop()

alertas = client.active_alerts_now() if client.tables else (estado.alerts if estado else [])
mesas = list(client.tables.values()) if client.tables else (estado.tables if estado else [])

st.subheader(f"Alertas ({len(alertas)})")
for a in alertas:
    (st.error if a.severity.value == "critical" else st.warning)(f"{a.where}: {a.alert_type.value}")

st.subheader("Mesas")
st.dataframe(
    [
        {
            "Mesa": m.table.label,
            "Estado": m.state.value,
            "Minutos": round((m.elapsed_seconds or 0) / 60, 1),
            "Personas": m.people,
        }
        for m in mesas
    ],
    hide_index=True,
)
st.caption("Conectado" if client.connected else "Reconectando…")
time.sleep(5)
st.rerun()
