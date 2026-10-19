# Atajos de desarrollo (macOS/Linux). En Windows, usar los comandos `uv ...` de CLAUDE.md.
.PHONY: sync go2rtc-config benchmark lint format typecheck test licenses check dashboard-check api dashboard-dev up down

# En macOS, Python 3.12 ignora los .pth del venv marcados "hidden" (uv los vuelve a marcar en
# carpetas como ~/Documents) y `import mesa_monitor` falla. PYTHONPATH=src lo evita siempre.
export PYTHONPATH := src

sync:
	uv sync --extra vision --extra cpu

lint:
	uv run --no-sync ruff check .
	uv run --no-sync ruff format --check .

format:
	uv run --no-sync ruff check --fix .
	uv run --no-sync ruff format .

typecheck:
	uv run --no-sync mypy

test:
	uv run --no-sync pytest -q

licenses:
	uv run --no-sync python tools/check_licenses.py

dashboard-check:
	cd dashboard && npm run lint && npm run build

check: lint typecheck test licenses dashboard-check

api:
	uv run --no-sync uvicorn --factory mesa_monitor.api.app:create_app --reload --port 8000

dashboard-dev:
	cd dashboard && npm run dev

go2rtc-config:
	uv run --no-sync python -m mesa_monitor.vision.go2rtc

benchmark:
	uv run --no-sync python tools/benchmark.py --go2rtc-url http://localhost:1984

up:
	docker compose up -d --build

down:
	docker compose down
