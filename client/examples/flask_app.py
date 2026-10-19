"""Ejemplo 2 — Flask: una página /mesas con el estado y las alertas, actualizada en vivo.

pip install flask
"""

import os

from flask import Flask, jsonify, render_template_string

from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(os.environ["MESA_MONITOR_URL"], os.environ["MESA_MONITOR_API_KEY"])
client.start_in_background()  # mantiene client.tables y client.alerts al día con los eventos

app = Flask(__name__)

PAGINA = """
<!doctype html><meta charset="utf-8"><title>Mesas</title>
<meta http-equiv="refresh" content="5">
<h1>Alertas</h1>
<ul>{% for a in alertas %}<li><b>{{ a.where }}</b>: {{ a.alert_type.value }}</li>{% else %}<li>Sin alertas</li>{% endfor %}</ul>
<h1>Mesas</h1>
<table border=1 cellpadding=4>
<tr><th>Mesa</th><th>Estado</th><th>Minutos</th></tr>
{% for m in mesas %}<tr><td>{{ m.table.label }}</td><td>{{ m.state.value }}</td>
<td>{{ ((m.elapsed_seconds or 0) / 60) | round(1) }}</td></tr>{% endfor %}
</table>
"""


@app.route("/mesas")
def mesas():  # type: ignore[no-untyped-def]
    datos = list(client.tables.values()) or client.get_state().tables
    return render_template_string(PAGINA, mesas=datos, alertas=client.active_alerts_now())


@app.route("/api/alertas")
def alertas_json():  # type: ignore[no-untyped-def]
    return jsonify([a.model_dump(mode="json") for a in client.active_alerts_now()])


if __name__ == "__main__":
    app.run(port=5000)
