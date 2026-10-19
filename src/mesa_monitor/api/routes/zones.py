"""Zonas por cámara: vistas de mesa (superficie + asientos), zonas de staff y máscaras.

El editor guarda todas las zonas de una cámara juntas (`PUT /zones/{camera_id}`), en una
transacción, y el worker las recarga en caliente (`NOTIFY config_changed`).
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.api.deps import AdminUser, DbSession
from mesa_monitor.api.routes.tables import actor_of
from mesa_monitor.api.schemas import (
    CameraZonesIn,
    CameraZonesOut,
    IgnoreMaskOut,
    StaffZoneOut,
    TableViewOut,
)
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_CONFIG, notify, record_audit
from mesa_monitor.db.models import Camera, IgnoreMask, StaffZone, Table, TableView

router = APIRouter(prefix="/zones", tags=["zones"])


async def load_camera_zones(db: AsyncSession, camera_id: str) -> CameraZonesOut:
    views = (
        await db.execute(
            select(TableView).where(TableView.camera_id == camera_id).order_by(TableView.id)
        )
    ).scalars()
    staff = (
        await db.execute(
            select(StaffZone).where(StaffZone.camera_id == camera_id).order_by(StaffZone.id)
        )
    ).scalars()
    masks = (
        await db.execute(
            select(IgnoreMask).where(IgnoreMask.camera_id == camera_id).order_by(IgnoreMask.id)
        )
    ).scalars()
    return CameraZonesOut(
        camera_id=camera_id,
        table_views=[
            TableViewOut(
                id=v.id,
                table_id=v.table_id,
                table_polygon=v.table_polygon,
                seats_polygon=v.seats_polygon,
                priority=v.priority,
                clean_mode=v.clean_mode,
            )
            for v in views
        ],
        staff_zones=[
            StaffZoneOut(id=z.id, kind=z.kind, name=z.name, polygon=z.polygon) for z in staff
        ],
        ignore_masks=[IgnoreMaskOut(id=m.id, name=m.name, polygon=m.polygon) for m in masks],
    )


async def replace_camera_zones(db: AsyncSession, camera_id: str, body: CameraZonesIn) -> None:
    """Reemplaza las zonas de la cámara (sin commit)."""
    table_ids = [v.table_id for v in body.table_views]
    if len(set(table_ids)) != len(table_ids):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Una mesa no puede tener dos vistas en la misma cámara",
        )
    existing = set((await db.execute(select(Table.id).where(Table.id.in_(table_ids)))).scalars())
    if missing := set(table_ids) - existing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Mesas inexistentes: {missing}"
        )

    for model in (TableView, StaffZone, IgnoreMask):
        await db.execute(delete(model).where(model.camera_id == camera_id))
    await db.flush()
    for v in body.table_views:
        db.add(
            TableView(
                id=v.id or new_id("tvw"),
                camera_id=camera_id,
                table_id=v.table_id,
                table_polygon=v.table_polygon,
                seats_polygon=v.seats_polygon,
                priority=v.priority,
                clean_mode=v.clean_mode,
            )
        )
    for z in body.staff_zones:
        db.add(
            StaffZone(
                id=z.id or new_id("stz"),
                camera_id=camera_id,
                kind=z.kind,
                name=z.name,
                polygon=z.polygon,
            )
        )
    for m in body.ignore_masks:
        db.add(
            IgnoreMask(
                id=m.id or new_id("msk"), camera_id=camera_id, name=m.name, polygon=m.polygon
            )
        )


async def _camera(db: AsyncSession, camera_id: str) -> Camera:
    cam = await db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cámara inexistente")
    return cam


@router.get("/{camera_id}", response_model=CameraZonesOut)
async def get_zones(camera_id: str, db: DbSession, _: AdminUser) -> CameraZonesOut:
    await _camera(db, camera_id)
    return await load_camera_zones(db, camera_id)


@router.put("/{camera_id}", response_model=CameraZonesOut)
async def put_zones(
    camera_id: str, body: CameraZonesIn, db: DbSession, user: AdminUser
) -> CameraZonesOut:
    await _camera(db, camera_id)
    before = await load_camera_zones(db, camera_id)
    await replace_camera_zones(db, camera_id, body)
    await record_audit(
        db,
        actor_of(user),
        "replace_zones",
        "camera",
        camera_id,
        before=before.model_dump(mode="json"),
        after=body.model_dump(mode="json"),
    )
    await notify(db, CHANNEL_CONFIG, {"kind": "zones", "camera_id": camera_id})
    await db.commit()
    return await load_camera_zones(db, camera_id)
