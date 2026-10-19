"""Mesas: listado (cualquier usuario), alta/edición/baja y ubicación en el plano (admin)."""

from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.api.deps import AdminUser, AuthUser, CurrentUser, DbSession
from mesa_monitor.api.schemas import TableIn, TableLayoutItem, TableOut, TablePatch
from mesa_monitor.core.enums import ActorType
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_CONFIG, Actor, notify, record_audit
from mesa_monitor.db.models import Table, TableSession, TableView

router = APIRouter(prefix="/tables", tags=["tables"])

_TABLE_FIELDS = (
    "label",
    "sector",
    "capacity",
    "enabled",
    "plan_x",
    "plan_y",
    "plan_w",
    "plan_h",
    "plan_rotation",
)


def actor_of(user: CurrentUser) -> Actor:
    return Actor(ActorType.USER, user.username)


def snapshot(table: Table) -> dict[str, Any]:
    return {f: getattr(table, f) for f in _TABLE_FIELDS}


async def _camera_ids(db: AsyncSession) -> dict[str, list[str]]:
    rows = (await db.execute(select(TableView.table_id, TableView.camera_id))).all()
    out: dict[str, list[str]] = {}
    for table_id, camera_id in rows:
        out.setdefault(table_id, []).append(camera_id)
    return out


def _out(table: Table, cams: dict[str, list[str]]) -> TableOut:
    out = TableOut.model_validate(table)
    out.camera_ids = sorted(cams.get(table.id, []))
    return out


async def _get(db: AsyncSession, table_id: str) -> Table:
    table = await db.get(Table, table_id)
    if table is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Mesa inexistente")
    return table


async def _flush(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Ya existe una mesa con esa etiqueta"
        ) from exc


@router.get("", response_model=list[TableOut])
async def list_tables(db: DbSession, _: AuthUser) -> list[TableOut]:
    tables = (await db.execute(select(Table).order_by(Table.sector, Table.label))).scalars().all()
    cams = await _camera_ids(db)
    return [_out(t, cams) for t in tables]


@router.get("/{table_id}", response_model=TableOut)
async def get_table(table_id: str, db: DbSession, _: AuthUser) -> TableOut:
    return _out(await _get(db, table_id), await _camera_ids(db))


@router.post("", response_model=TableOut, status_code=status.HTTP_201_CREATED)
async def create_table(body: TableIn, db: DbSession, user: AdminUser) -> TableOut:
    table = Table(id=new_id("tbl"), **body.model_dump())
    db.add(table)
    await _flush(db)
    await record_audit(db, actor_of(user), "create", "table", table.id, after=body.model_dump())
    await notify(db, CHANNEL_CONFIG, {"kind": "tables"})
    await db.commit()
    return _out(table, {})


@router.patch("/{table_id}", response_model=TableOut)
async def update_table(table_id: str, body: TablePatch, db: DbSession, user: AdminUser) -> TableOut:
    table = await _get(db, table_id)
    before = snapshot(table)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(table, field, value)
    await _flush(db)
    await record_audit(
        db, actor_of(user), "update", "table", table.id, before=before, after=snapshot(table)
    )
    await notify(db, CHANNEL_CONFIG, {"kind": "tables"})
    await db.commit()
    return _out(table, await _camera_ids(db))


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_table(table_id: str, db: DbSession, user: AdminUser) -> Response:
    table = await _get(db, table_id)
    n_sessions = (
        await db.execute(
            select(func.count()).select_from(TableSession).where(TableSession.table_id == table_id)
        )
    ).scalar_one()
    if n_sessions:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="La mesa tiene historial: deshabilitala en lugar de borrarla",
        )
    await record_audit(db, actor_of(user), "delete", "table", table.id, before=snapshot(table))
    await db.delete(table)
    await notify(db, CHANNEL_CONFIG, {"kind": "tables"})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/layout/positions", response_model=list[TableOut])
async def update_layout(
    body: list[TableLayoutItem], db: DbSession, user: AdminUser
) -> list[TableOut]:
    """Guarda posición, tamaño y rotación de varias mesas a la vez (editor del plano)."""
    tables = {
        t.id: t
        for t in (
            await db.execute(select(Table).where(Table.id.in_([i.id for i in body])))
        ).scalars()
    }
    missing = {i.id for i in body} - set(tables)
    if missing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Mesas inexistentes: {missing}")
    for item in body:
        t = tables[item.id]
        t.plan_x, t.plan_y = item.plan_x, item.plan_y
        t.plan_w, t.plan_h, t.plan_rotation = item.plan_w, item.plan_h, item.plan_rotation
    await record_audit(db, actor_of(user), "update_layout", "table", None, after={"n": len(body)})
    await db.commit()
    cams = await _camera_ids(db)
    return [_out(t, cams) for t in tables.values()]
