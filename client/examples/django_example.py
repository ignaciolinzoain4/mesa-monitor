"""Ejemplo 4 — Django. Tres partes: el cliente compartido, arrancarlo y una vista.

1) En tu app (ej. `gestion/mesa_monitor.py`), un solo cliente para todo el proyecto:
"""

import os

from mesa_monitor_client import MesaMonitorClient

client = MesaMonitorClient(os.environ["MESA_MONITOR_URL"], os.environ["MESA_MONITOR_API_KEY"])

# 2) En `gestion/apps.py`, arrancar la escucha cuando Django inicia:
#
#     from django.apps import AppConfig
#
#     class GestionConfig(AppConfig):
#         name = "gestion"
#
#         def ready(self):
#             import os
#             if os.environ.get("RUN_MAIN") == "true" or not os.environ.get("DJANGO_DEV"):
#                 from gestion.mesa_monitor import client
#                 client.start_in_background()
#
#    (El `RUN_MAIN` evita arrancarlo dos veces con el autoreload de `runserver`.)
#
# 3) En `gestion/views.py`:
#
#     from django.http import JsonResponse
#     from django.shortcuts import render
#     from gestion.mesa_monitor import client
#
#     def mesas(request):
#         estado = client.get_state()
#         return render(request, "mesas.html", {"mesas": estado.tables, "alertas": estado.alerts})
#
#     def alertas_json(request):
#         return JsonResponse([a.model_dump(mode="json") for a in client.active_alerts_now()], safe=False)
