# CLAUDE.md — Mesa Monitor

La especificación completa está en [SPEC.md](SPEC.md): leela antes de cambiar comportamiento.
Las decisiones marcadas **[DECIDIDO]** no se discuten sin un impedimento técnico real.

## Convenciones
- UI, docs y mensajes al usuario: **español rioplatense**. Código, tablas, variables y commits: **inglés**.
- Licencias: solo Apache-2.0 / MIT / BSD (y permisivas equivalentes). **Prohibido**: `ultralytics`,
  cualquier GPL/AGPL/LGPL, `rfdetr[plus]` y RF-DETR XL/2XL (PML). `tools/check_licenses.py` lo valida en CI.
  Por eso el driver de Postgres es **asyncpg** (psycopg es LGPL).
- Versiones exactas: `uv add --bounds exact <pkg>`; en el dashboard `.npmrc` tiene `save-exact=true`.
- Nunca al repo: credenciales, URLs de cámaras con usuario/clave, IPs reales, capturas o videos.
  Van en `.env` y `config/local/` (ignorados). El repo es **público**.
- Verificar la API actual de `rfdetr`, `supervision`, `trackers` y `go2rtc` en el código instalado antes de usarlas.
- Commits chicos; al cerrar cada fase: tests en verde, README actualizado, resumen de hecho/pendiente.

## Cumplimiento (SPEC Anexo A — tiene prioridad sobre el resto de la spec)
- El sistema **no identifica empleados**. Los `track_id` viven solo en memoria; nunca se persisten
  ni se asocian a visitas, sesiones o métricas. `staff_visits` = solo tiempos.
- Métricas solo por mesa / sector / franja / día / turno (`core.metrics.MetricsGroupBy`, lista cerrada).
  Prohibido: métricas o rankings por empleado, asignación mozo ↔ mesa, "desempeño del mozo".
- UI: hablar de "atención de mesas" y "servicio", nunca de "desempeño" o "productividad del personal".
- No cruzar datos del POS (mozo asignado) con métricas de cámara sin revisión legal.
- `tests/test_compliance.py` recorre el esquema (tablas y vistas) y falla con columnas de identidad.
  Detalle para el dueño: [docs/cumplimiento.md](docs/cumplimiento.md).

## Estructura
- `src/mesa_monitor/core/` — dominio puro (enums, ids, state engine, métricas). Sin IO ni visión.
- `src/mesa_monitor/db/` — modelos SQLAlchemy 2 (async). Polígonos en coordenadas normalizadas 0–1.
- `src/mesa_monitor/api/` — FastAPI. Todo bajo `/api/v1`. Sirve el dashboard compilado desde `dashboard/dist`.
- `src/mesa_monitor/vision/` — worker (`python -m mesa_monitor.vision`). Dependencias pesadas en el extra `vision`.
- `migrations/` — Alembic (async). `tools/` — scripts (licencias, benchmark, replay, evaluate).
- `dashboard/` — React + Vite + TS + Tailwind (Recharts solo en Análisis, carga diferida).
- `client/` — paquete liviano `mesa_monitor_client` (httpx, websockets, pydantic; rangos de versión
  compatibles porque se instala en otro proyecto). Sus modelos deben coincidir con
  `core/events.py`: `tests/test_schemas.py` lo verifica. Si cambia un evento: actualizar ambos y
  correr `uv run python tools/export_schemas.py` (regenera `schemas/`).
- `INTEGRATION.md` — guía para el asistente de IA del dueño (el sistema de gestión).

## Comandos
```sh
make sync                 # uv sync --extra vision --extra cpu  (cpu y cuda son excluyentes)
make check                # lint + mypy + tests + licencias + dashboard
make api                  # API en :8000 con reload (necesita DATABASE_URL)
make dashboard-dev        # Vite en :5173, proxy de /api a :8000
docker compose up -d      # stack completo (perfil según COMPOSE_PROFILES en .env)
```
En macOS uv marca los `.pth` del venv como ocultos y Python 3.12 los ignora: los comandos sueltos
necesitan `PYTHONPATH=src` (el Makefile ya lo exporta; pytest y alembic lo configuran solos).
Sin make (Windows): `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`,
`uv run pytest`, `uv run python tools/check_licenses.py`.

### Tests con base de datos
Los tests marcados `db` usan `TEST_DATABASE_URL` (una base vacía; se recrea el esquema). Sin la
variable se saltean. Ejemplo: `TEST_DATABASE_URL=postgresql+asyncpg://mesa@localhost:5432/mesa_test make test`.

### Migraciones
```sh
uv run alembic revision --autogenerate -m "describe change"   # revisar el archivo generado
uv run alembic upgrade head
```
Los enums se guardan como texto + CHECK (`db.base.str_enum`). Alembic autogenera esos CHECK dos
veces; el hook `tools/fix_enum_checks.py` (configurado en alembic.ini) deja solo los `ck_...`. `test_models_match_migrations` detecta divergencias entre modelos y migraciones.

## Decisiones tomadas (además de SPEC)
- IDs públicos: prefijo + ULID (`core.ids.new_id("alr")`). Tablas de alto volumen: bigint.
- Una sola sesión abierta por mesa y una alerta activa por (mesa|cámara, tipo): índices únicos parciales.
- `SIN_VISION` guarda el estado previo y se retoma; las alertas descuentan el tiempo sin visión
  (`table_sessions.vision_lost_seconds`); las métricas usan tiempos de reloj.
- Rotación `R = N_ses / (N_mesas · H_op)`. `G_max` se calcula sobre `[t_a, visitas…, t_v]`.
- Día de negocio con hora de corte (`app_settings`) porque el local cierra después de medianoche; turnos en `shifts`.
- La escalada warning→critical ignora "Visto" y "Posponer". El SLA usa el `U_1` efectivo de cada mesa.
- Cámaras fuera del NVR: `cameras.stream_url_template` propio por cámara.
- Video en vivo: MSE/MJPEG por HTTP por defecto; WebRTC opcional (en Docker Desktop/Windows necesita UDP + candidates).
- La UI de go2rtc no tiene login: se publica solo en `127.0.0.1`.
