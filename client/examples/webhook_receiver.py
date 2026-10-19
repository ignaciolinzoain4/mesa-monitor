"""Ejemplo 6 — Recibir webhooks (alternativa a escuchar en vivo): Mesa Monitor le avisa a tu
sistema con un POST firmado. Configurarlo en el dashboard: Configuración → Webhooks.

pip install flask
"""

import os

from flask import Flask, request

from mesa_monitor_client import FirmaInvalida, parse_webhook

SECRETO = os.environ[
    "MESA_MONITOR_WEBHOOK_SECRET"
]  # el que muestra el dashboard al crear el webhook
app = Flask(__name__)


@app.post("/mesa-monitor/webhook")
def webhook():  # type: ignore[no-untyped-def]
    try:
        event = parse_webhook(request.headers, request.get_data(), SECRETO)
    except FirmaInvalida:
        return "firma inválida", 401
    if event.type == "alert.opened":
        print("Alerta:", event.data.where, event.data.alert_type.value)
    return "", 204


if __name__ == "__main__":
    app.run(port=5002)
