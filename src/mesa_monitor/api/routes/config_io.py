"""Respaldo de la configuración editable: export/import a YAML (SPEC §5.1).

Incluye mesas, vistas por cámara, zonas de staff, máscaras, umbrales, horarios, turnos,
parámetros y perfiles de staff. **No** incluye usuarios, API keys, tokens ni secretos.
Las mesas se identifican por su etiqueta: al importar, una mesa existente con la misma
etiqueta conserva su id (y por lo tanto su historial).
"""

from datetime import UTC, datetime, time
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.api.deps import AdminUser, DbSession
from mesa_monitor.api.routes.tables import actor_of
from mesa_monitor.api.routes.zones import replace_camera_zones
from mesa_monitor.api.schemas import (
    CameraZonesIn,
    IgnoreMaskIn,
    Label,
    PolygonIn,
    StaffZoneIn,
    TableBase,
    TableViewIn,
)
from mesa_monitor.core.enums import CleanMode, StaffZoneKind, ThresholdScope
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_CONFIG, notify, record_audit
from mesa_monitor.db.models import (
    AppSetting,
    Camera,
    IgnoreMask,
    OperatingHours,
    Shift,
    StaffProfile,
    StaffZone,
    Table,
    TableView,
    Threshold,
)

router = APIRouter(prefix="/config", tags=["config"])
FORMAT_VERSION = 1


class ExportView(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    table: Label  # etiqueta de la mesa
    table_polygon: PolygonIn
    seats_polygon: PolygonIn
    priority: int = 0
    clean_mode: CleanMode = CleanMode.OBJECTS_AND_STAFF


class ExportStaffZone(BaseModel):
    kind: StaffZoneKind
    name: str
    polygon: PolygonIn


class ExportMask(BaseModel):
    name: str
    polygon: PolygonIn


class ExportCamera(BaseModel):
    table_views: list[ExportView] = Field(default_factory=list)
    staff_zones: list[ExportStaffZone] = Field(default_factory=list)
    ignore_masks: list[ExportMask] = Field(default_factory=list)


class ExportThreshold(BaseModel):
    scope: ThresholdScope
    scope_ref: str | None = None  # sector, o etiqueta de mesa
    key: str
    value: Any
    valid_from: time | None = None
    valid_to: time | None = None


class ExportHours(BaseModel):
    weekday: int = Field(ge=0, le=6)
    opens_at: time
    closes_at: time


class ExportShift(BaseModel):
    name: str
    starts_at: time
    ends_at: time


class ExportProfile(BaseModel):
    name: str
    active: bool
    color_profile: dict[str, Any]
    weights: dict[str, Any]
    threshold: float


class ConfigDocument(BaseModel):
    version: int = FORMAT_VERSION
    exported_at: datetime | None = None
    tables: list[TableBase] = Field(default_factory=list)
    cameras: dict[str, ExportCamera] = Field(default_factory=dict)
    thresholds: list[ExportThreshold] = Field(default_factory=list)
    operating_hours: list[ExportHours] = Field(default_factory=list)
    shifts: list[ExportShift] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    staff_profiles: list[ExportProfile] = Field(default_factory=list)


class ImportResult(BaseModel):
    dry_run: bool
    tables_created: int
    tables_updated: int
    tables_disabled: int
    cameras: int
    table_views: int
    staff_zones: int
    ignore_masks: int
    thresholds: int
    warnings: list[str]


async def build_document(db: AsyncSession) -> ConfigDocument:
    tables = (await db.execute(select(Table).order_by(Table.label))).scalars().all()
    label_of = {t.id: t.label for t in tables}
    doc = ConfigDocument(
        exported_at=datetime.now(UTC),
        tables=[TableBase.model_validate(t, from_attributes=True) for t in tables],
    )
    cams: dict[str, ExportCamera] = {}
    for v in (await db.execute(select(TableView).order_by(TableView.camera_id))).scalars():
        cams.setdefault(v.camera_id, ExportCamera()).table_views.append(
            ExportView(
                table=label_of[v.table_id],
                table_polygon=v.table_polygon,
                seats_polygon=v.seats_polygon,
                priority=v.priority,
                clean_mode=v.clean_mode,
            )
        )
    for z in (await db.execute(select(StaffZone).order_by(StaffZone.name))).scalars():
        cams.setdefault(z.camera_id, ExportCamera()).staff_zones.append(
            ExportStaffZone(kind=z.kind, name=z.name, polygon=z.polygon)
        )
    for m in (await db.execute(select(IgnoreMask).order_by(IgnoreMask.name))).scalars():
        cams.setdefault(m.camera_id, ExportCamera()).ignore_masks.append(
            ExportMask(name=m.name, polygon=m.polygon)
        )
    doc.cameras = dict(sorted(cams.items()))
    for th in (await db.execute(select(Threshold).order_by(Threshold.id))).scalars():
        ref = (
            label_of.get(th.scope_ref or "", th.scope_ref)
            if th.scope is ThresholdScope.TABLE
            else th.scope_ref
        )
        doc.thresholds.append(
            ExportThreshold(
                scope=th.scope,
                scope_ref=ref,
                key=th.key,
                value=th.value,
                valid_from=th.valid_from,
                valid_to=th.valid_to,
            )
        )
    doc.operating_hours = [
        ExportHours(weekday=h.weekday, opens_at=h.opens_at, closes_at=h.closes_at)
        for h in (
            await db.execute(select(OperatingHours).order_by(OperatingHours.weekday))
        ).scalars()
    ]
    doc.shifts = [
        ExportShift(name=s.name, starts_at=s.starts_at, ends_at=s.ends_at)
        for s in (await db.execute(select(Shift).order_by(Shift.starts_at))).scalars()
    ]
    doc.settings = {s.key: s.value for s in (await db.execute(select(AppSetting))).scalars()}
    doc.staff_profiles = [
        ExportProfile(
            name=p.name,
            active=p.active,
            color_profile=p.color_profile,
            weights=p.weights,
            threshold=p.threshold,
        )
        for p in (
            await db.execute(select(StaffProfile).order_by(StaffProfile.created_at))
        ).scalars()
    ]
    return doc


async def apply_document(db: AsyncSession, doc: ConfigDocument) -> ImportResult:
    """Aplica el documento en la sesión actual (sin commit)."""
    warnings: list[str] = []
    labels = [t.label for t in doc.tables]
    if len(set(labels)) != len(labels):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Etiquetas de mesa repetidas"
        )
    known_cams = set((await db.execute(select(Camera.id))).scalars())
    if unknown := set(doc.cameras) - known_cams:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cámaras que no están en cameras.yaml: {sorted(unknown)}",
        )

    existing = {t.label: t for t in (await db.execute(select(Table))).scalars()}
    created = updated = 0
    id_of: dict[str, str] = {}
    for t in doc.tables:
        row = existing.pop(t.label, None)
        if row is None:
            row = Table(id=new_id("tbl"))
            db.add(row)
            created += 1
        else:
            updated += 1
        for field, value in t.model_dump().items():
            setattr(row, field, value)
        id_of[t.label] = row.id
    for leftover in existing.values():
        if leftover.enabled:
            leftover.enabled = False
            warnings.append(f"La mesa {leftover.label!r} no está en el archivo: se deshabilitó")
    await db.flush()

    n_views = n_staff = n_masks = 0
    for camera_id in known_cams:
        cam = doc.cameras.get(camera_id, ExportCamera())
        missing = {v.table for v in cam.table_views} - set(id_of)
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"La cámara {camera_id} referencia mesas que no están en el archivo: "
                    f"{sorted(missing)}"
                ),
            )
        await replace_camera_zones(
            db,
            camera_id,
            CameraZonesIn(
                table_views=[
                    TableViewIn(
                        table_id=id_of[v.table],
                        table_polygon=v.table_polygon,
                        seats_polygon=v.seats_polygon,
                        priority=v.priority,
                        clean_mode=v.clean_mode,
                    )
                    for v in cam.table_views
                ],
                staff_zones=[StaffZoneIn(**z.model_dump()) for z in cam.staff_zones],
                ignore_masks=[IgnoreMaskIn(**m.model_dump()) for m in cam.ignore_masks],
            ),
        )
        n_views += len(cam.table_views)
        n_staff += len(cam.staff_zones)
        n_masks += len(cam.ignore_masks)

    await db.execute(delete(Threshold))
    for th in doc.thresholds:
        ref = th.scope_ref
        if th.scope is ThresholdScope.TABLE:
            if ref not in id_of:
                warnings.append(f"Umbral {th.key} para mesa desconocida {ref!r}: se ignoró")
                continue
            ref = id_of[ref]
        db.add(
            Threshold(
                scope=th.scope,
                scope_ref=ref,
                key=th.key,
                value=th.value,
                valid_from=th.valid_from,
                valid_to=th.valid_to,
            )
        )
    await db.execute(delete(OperatingHours))
    db.add_all(OperatingHours(**h.model_dump()) for h in doc.operating_hours)
    await db.execute(delete(Shift))
    db.add_all(Shift(id=new_id("shf"), **s.model_dump()) for s in doc.shifts)
    for key, value in doc.settings.items():
        setting = await db.get(AppSetting, key)
        if setting is None:
            db.add(AppSetting(key=key, value=value))
        else:
            setting.value = value
    if doc.staff_profiles:
        await db.execute(delete(StaffProfile))
        await db.flush()
        db.add_all(StaffProfile(id=new_id("spf"), **p.model_dump()) for p in doc.staff_profiles)
    await db.flush()

    return ImportResult(
        dry_run=False,
        tables_created=created,
        tables_updated=updated,
        tables_disabled=sum("deshabilitó" in w for w in warnings),
        cameras=len(doc.cameras),
        table_views=n_views,
        staff_zones=n_staff,
        ignore_masks=n_masks,
        thresholds=len(doc.thresholds),
        warnings=warnings,
    )


@router.get(
    "/export", response_class=Response, responses={200: {"content": {"application/yaml": {}}}}
)
async def export_config(db: DbSession, _: AdminUser) -> Response:
    doc = await build_document(db)
    body = "# Respaldo de configuración de Mesa Monitor\n" + yaml.safe_dump(
        doc.model_dump(mode="json"), sort_keys=False, allow_unicode=True
    )
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    return Response(
        body,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="mesa-monitor-config-{stamp}.yaml"'},
    )


@router.post("/import", response_model=ImportResult)
async def import_config(
    request: Request,
    db: DbSession,
    user: AdminUser,
    dry_run: bool = Query(default=True, description="Validar y mostrar el resumen sin guardar"),
) -> ImportResult:
    raw = (await request.body()).decode("utf-8", errors="replace")
    try:
        data = yaml.safe_load(raw)
        doc = ConfigDocument.model_validate(data or {})
    except yaml.YAMLError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"YAML inválido: {exc}"
        ) from exc
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Archivo inválido en {where}: {first['msg']}",
        ) from exc
    if doc.version != FORMAT_VERSION:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Versión de archivo no soportada: {doc.version}",
        )
    result = await apply_document(db, doc)
    result.dry_run = dry_run
    if dry_run:
        await db.rollback()
        return result
    await record_audit(db, actor_of(user), "import", "config", None, after=result.model_dump())
    await notify(db, CHANNEL_CONFIG, {"kind": "all"})
    await db.commit()
    return result
