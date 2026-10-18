#!/bin/sh
# Aplica migraciones pendientes y levanta la API.
set -e
alembic upgrade head
exec uvicorn --factory mesa_monitor.api.app:create_app \
    --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'
