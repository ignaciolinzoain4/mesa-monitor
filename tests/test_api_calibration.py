"""F2: mesas, zonas por cámara, plano y export/import YAML."""

from pathlib import Path

import pytest
import yaml
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from mesa_monitor.db.models import AuditLog
from tests.helpers import api

pytestmark = pytest.mark.db

SQ = [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.1, 0.3]]
SEATS = [[0.05, 0.05], [0.35, 0.05], [0.35, 0.35], [0.05, 0.35]]


async def test_table_crud_and_unique_label(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        r = await c.post("/tables", json={"label": "12", "sector": "Salon", "capacity": 4})
        assert r.status_code == 201
        t = r.json()
        assert t["sector"] == "salon"  # se normaliza a minúsculas
        assert t["id"].startswith("tbl_")
        dup = await c.post("/tables", json={"label": "12", "sector": "galeria"})
        assert dup.status_code == 409
        r = await c.patch(f"/tables/{t['id']}", json={"capacity": 6, "plan_x": 10})
        assert r.json()["capacity"] == 6
        assert r.json()["plan_x"] == 10
        listed = (await c.get("/tables")).json()
        assert [x["label"] for x in listed] == ["12"]
        assert (await c.delete(f"/tables/{t['id']}")).status_code == 204
        assert (await c.get(f"/tables/{t['id']}")).status_code == 404


async def test_table_with_history_cannot_be_deleted(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        t = (await c.post("/tables", json={"label": "1", "sector": "salon"})).json()
        engine = create_async_engine(clean_db)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO table_sessions (id, table_id, business_date, seated_at)"
                    " VALUES ('ses_x', :t, '2026-10-18', now())"
                ),
                {"t": t["id"]},
            )
        await engine.dispose()
        r = await c.delete(f"/tables/{t['id']}")
        assert r.status_code == 409
        assert "deshabilitala" in r.json()["detail"]


async def test_zones_replace_validate_and_coverage(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        t1 = (await c.post("/tables", json={"label": "1", "sector": "salon"})).json()
        t2 = (await c.post("/tables", json={"label": "2", "sector": "salon"})).json()
        body = {
            "table_views": [
                {"table_id": t1["id"], "table_polygon": SQ, "seats_polygon": SEATS, "priority": 2}
            ],
            "staff_zones": [{"kind": "bar", "name": "Barra", "polygon": SQ}],
            "ignore_masks": [{"name": "Fecha OSD", "polygon": SQ}],
        }
        r = await c.put("/zones/cam1", json=body)
        assert r.status_code == 200, r.text
        zones = r.json()
        assert zones["table_views"][0]["priority"] == 2
        assert zones["staff_zones"][0]["kind"] == "bar"

        tables = {t["label"]: t for t in (await c.get("/tables")).json()}
        assert tables["1"]["camera_ids"] == ["cam1"]
        assert tables["2"]["camera_ids"] == []  # mesa sin vista: el editor la marca

        bad = dict(
            body,
            table_views=[{**body["table_views"][0], "table_polygon": [[0, 0], [2, 2], [0, 1]]}],
        )
        assert (await c.put("/zones/cam1", json=bad)).status_code == 422
        dup = dict(body, table_views=body["table_views"] * 2)
        assert (await c.put("/zones/cam1", json=dup)).status_code == 422
        ghost = dict(body, table_views=[{**body["table_views"][0], "table_id": "tbl_nope"}])
        assert (await c.put("/zones/cam1", json=ghost)).status_code == 422
        assert (await c.put("/zones/nope", json=body)).status_code == 404

        # reemplazo completo
        r = await c.put(
            "/zones/cam1",
            json={
                "table_views": [{"table_id": t2["id"], "table_polygon": SQ, "seats_polygon": SEATS}]
            },
        )
        assert [v["table_id"] for v in r.json()["table_views"]] == [t2["id"]]
        assert r.json()["staff_zones"] == []

    engine = create_async_engine(clean_db)
    async with engine.connect() as conn:
        actions = (await conn.execute(select(AuditLog.action))).scalars().all()
    await engine.dispose()
    assert actions.count("replace_zones") == 2


async def test_layout_bulk_update(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        t = (await c.post("/tables", json={"label": "1", "sector": "salon"})).json()
        r = await c.put(
            "/tables/layout/positions",
            json=[
                {
                    "id": t["id"],
                    "plan_x": 5,
                    "plan_y": 7,
                    "plan_w": 3,
                    "plan_h": 2,
                    "plan_rotation": 45,
                }
            ],
        )
        assert r.status_code == 200
        assert (await c.get(f"/tables/{t['id']}")).json()["plan_rotation"] == 45
        missing = await c.put(
            "/tables/layout/positions",
            json=[{"id": "tbl_x", "plan_x": 1, "plan_y": 1, "plan_w": 1, "plan_h": 1}],
        )
        assert missing.status_code == 404


async def test_export_import_roundtrip(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        t1 = (await c.post("/tables", json={"label": "1", "sector": "salon", "plan_x": 3})).json()
        await c.post("/tables", json={"label": "2", "sector": "galeria"})
        await c.put(
            "/zones/cam1",
            json={
                "table_views": [
                    {"table_id": t1["id"], "table_polygon": SQ, "seats_polygon": SEATS}
                ],
                "ignore_masks": [{"name": "OSD", "polygon": SQ}],
            },
        )
        exported = await c.get("/config/export")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        doc = yaml.safe_load(exported.text)
        assert doc["cameras"]["cam1"]["table_views"][0]["table"] == "1"

        # editar el archivo: mesa 2 desaparece, aparece mesa 3 con vista en cam2
        doc["tables"] = [t for t in doc["tables"] if t["label"] != "2"]
        doc["tables"].append({"label": "3", "sector": "galeria"})
        doc["cameras"]["cam2"] = {
            "table_views": [{"table": "3", "table_polygon": SQ, "seats_polygon": SEATS}]
        }
        text_doc = yaml.safe_dump(doc)

        dry = (await c.post("/config/import", content=text_doc)).json()
        assert dry["dry_run"] is True
        assert (dry["tables_created"], dry["tables_updated"], dry["tables_disabled"]) == (1, 1, 1)
        assert len((await c.get("/tables")).json()) == 2  # nada cambió

        real = (await c.post("/config/import?dry_run=false", content=text_doc)).json()
        assert real["table_views"] == 2
        tables = {t["label"]: t for t in (await c.get("/tables")).json()}
        assert tables["1"]["id"] == t1["id"]  # conserva id → conserva historial
        assert tables["2"]["enabled"] is False
        assert tables["3"]["camera_ids"] == ["cam2"]


async def test_import_rejects_unknown_camera_and_bad_yaml(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        r = await c.post("/config/import", content="version: 1\ncameras: {camX: {}}\n")
        assert r.status_code == 422
        assert "camX" in r.json()["detail"]
        r = await c.post("/config/import", content="tables: [{label: 1}]")
        assert r.status_code == 422
        assert "sector" in r.json()["detail"]
        r = await c.post("/config/import", content="{{{")
        assert r.status_code == 422


async def test_viewer_cannot_edit(clean_db: str, tmp_path: Path) -> None:
    async with api(clean_db, tmp_path) as c:
        c.headers.pop("Authorization")
        assert (await c.post("/tables", json={"label": "1", "sector": "s"})).status_code == 401
