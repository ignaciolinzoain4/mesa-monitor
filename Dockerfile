# syntax=docker/dockerfile:1.7
# Imagen única con targets: `api` (API + dashboard) y `worker` (visión).

# ---------------------------------------------------------------- dashboard (build estático)
FROM node:24.22.0-bookworm-slim AS dashboard
WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json dashboard/.npmrc ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build

# ---------------------------------------------------------------- base Python
FROM python:3.12.15-slim-bookworm AS python-base
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN useradd --create-home --uid 1000 mesa

# ---------------------------------------------------------------- api
FROM python-base AS api
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY migrations/ migrations/
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
COPY --from=dashboard /app/dashboard/dist /app/dashboard/dist
COPY docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh
# Destino de la config generada de go2rtc (volumen compartido con el contenedor go2rtc).
RUN mkdir -p /go2rtc && chown mesa:mesa /go2rtc
USER mesa
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').status != 200)"
ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]

# ---------------------------------------------------------------- worker
# TORCH_VARIANT: "cpu" o "cuda" (extras de pyproject.toml).
FROM python-base AS worker
ARG TORCH_VARIANT=cpu
ENV RF_HOME=/models
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra vision --extra ${TORCH_VARIANT}
COPY src/ src/
COPY tools/ tools/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra vision --extra ${TORCH_VARIANT}
RUN mkdir -p /models /app/config/local && chown -R mesa:mesa /models /app/config
USER mesa
CMD ["python", "-m", "mesa_monitor.vision"]
