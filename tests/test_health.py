from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from mesa_monitor.api.app import create_app
from mesa_monitor.config import Settings


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.db
async def test_health_ok(migrated_db: str) -> None:
    app = create_app(Settings(database_url=migrated_db, _env_file=None))  # type: ignore[call-arg]
    async for client in _client(app):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_health_degraded_when_database_down() -> None:
    url = "postgresql+asyncpg://nadie@127.0.0.1:1/inexistente"
    app = create_app(Settings(database_url=url, _env_file=None))  # type: ignore[call-arg]
    async for client in _client(app):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "error"


async def test_openapi_is_served_under_api_prefix() -> None:
    app = create_app(Settings(_env_file=None))  # type: ignore[call-arg]
    async for client in _client(app):
        resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/health" in resp.json()["paths"]
