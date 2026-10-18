# Atajos de desarrollo (macOS/Linux). En Windows, usar los comandos `uv ...` de CLAUDE.md.
.PHONY: sync lint format typecheck test licenses check dashboard-check api dashboard-dev up down

# Python 3.12 ignora archivos .pth con la marca "hidden" de macOS (pasa en carpetas como
# ~/Documents). Sin esto, `import mesa_monitor` falla fuera de pytest/alembic.
sync:
	uv sync --all-extras
	-@[ "$$(uname)" = "Darwin" ] && chflags nohidden .venv/lib/python*/site-packages/*.pth

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

up:
	docker compose up -d --build

down:
	docker compose down
