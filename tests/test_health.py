import pytest

from mesa_monitor.api.app import create_app
from tests.conftest import app_client, make_settings


@pytest.mark.db
async def test_health_ok(migrated_db: str) -> None:
    app = create_app(make_settings(database_url=migrated_db), start_background_tasks=False)
    async for client in app_client(app):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_health_degraded_when_database_down() -> None:
    url = "postgresql+asyncpg://nadie@127.0.0.1:1/inexistente"
    app = create_app(make_settings(database_url=url), start_background_tasks=False)
    async for client in app_client(app):
        resp = await client.get("/api/v1/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "error"


async def test_openapi_is_served_under_api_prefix() -> None:
    url = "postgresql+asyncpg://nadie@127.0.0.1:1/inexistente"
    app = create_app(make_settings(database_url=url), start_background_tasks=False)
    async for client in app_client(app):
        resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "/api/v1/health" in resp.json()["paths"]


@pytest.mark.parametrize("secret", ["", "reemplazar-por-un-valor-largo-y-aleatorio", "corto"])
def test_app_refuses_to_start_with_weak_jwt_secret(secret: str) -> None:
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        create_app(make_settings(jwt_secret=secret))
