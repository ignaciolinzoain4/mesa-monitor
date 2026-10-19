"""Configuración editable en caliente: umbrales, horario/turnos y ajustes de visión.

Todo cambio queda en `audit_log` y se avisa con `NOTIFY config_changed`: el worker y el motor de
alertas lo aplican sin reiniciar.
"""

from datetime import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select

from mesa_monitor.api.deps import AdminUser, AuthUser, DbSession
from mesa_monitor.core.enums import ThresholdScope
from mesa_monitor.core.ids import new_id
from mesa_monitor.core.schedule import DEFAULT_CUTOFF
from mesa_monitor.core.thresholds import DEFINITIONS, validate_value
from mesa_monitor.db.audit import CHANNEL_CONFIG, notify, record_audit
from mesa_monitor.db.models import AppSetting, OperatingHours, Shift, Table, Threshold

router = APIRouter(prefix="/config", tags=["config"])


# --------------------------------------------------------------------------- umbrales


class ThresholdDef(BaseModel):
    key: str
    default: Any
    description: str
    unit: str


class ThresholdItem(BaseModel):
    scope: ThresholdScope = ThresholdScope.GLOBAL
    scope_ref: str | None = None
    key: str
    value: Any
    valid_from: time | None = None
    valid_to: time | None = None

    @model_validator(mode="after")
    def _check(self) -> "ThresholdItem":
        self.value = validate_value(self.key, self.value)
        if (self.scope is ThresholdScope.GLOBAL) != (self.scope_ref is None):
            raise ValueError("scope_ref es obligatorio para sector/mesa y vacío para global")
        if (self.valid_from is None) != (self.valid_to is None):
            raise ValueError("La franja horaria necesita inicio y fin")
        return self


class ThresholdsOut(BaseModel):
    definitions: list[ThresholdDef]
    items: list[ThresholdItem]


@router.get("/thresholds", response_model=ThresholdsOut)
async def get_thresholds(db: DbSession, _: AuthUser) -> ThresholdsOut:
    rows = (await db.execute(select(Threshold).order_by(Threshold.scope, Threshold.key))).scalars()
    return ThresholdsOut(
        definitions=[
            ThresholdDef(key=k, default=d, description=desc, unit=u)
            for k, (d, desc, u) in DEFINITIONS.items()
        ],
        items=[
            ThresholdItem(
                scope=r.scope,
                scope_ref=r.scope_ref,
                key=r.key,
                value=r.value,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
            )
            for r in rows
        ],
    )


@router.put("/thresholds", response_model=ThresholdsOut)
async def put_thresholds(
    body: list[ThresholdItem], request: Request, db: DbSession, who: AdminUser
) -> ThresholdsOut:
    """Reemplaza todos los overrides (los valores que no estén usan el default)."""
    table_ids = {i.scope_ref for i in body if i.scope is ThresholdScope.TABLE}
    if table_ids:
        known = set((await db.execute(select(Table.id).where(Table.id.in_(table_ids)))).scalars())
        if missing := table_ids - known:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Mesas inexistentes: {missing}"
            )
    keys = [(i.scope, i.scope_ref, i.key, i.valid_from, i.valid_to) for i in body]
    if len(keys) != len(set(keys)):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Hay umbrales repetidos")
    before = await get_thresholds(db, who)
    await db.execute(delete(Threshold))
    for i in body:
        db.add(
            Threshold(
                scope=i.scope,
                scope_ref=i.scope_ref,
                key=i.key,
                value=i.value,
                valid_from=i.valid_from,
                valid_to=i.valid_to,
                updated_by=who.username,
            )
        )
    await record_audit(
        db,
        who.actor,
        "replace",
        "thresholds",
        None,
        before={"items": [x.model_dump(mode="json") for x in before.items]},
        after={"items": [x.model_dump(mode="json") for x in body]},
    )
    await notify(db, CHANNEL_CONFIG, {"kind": "thresholds"})
    await db.commit()
    request.app.state.alert_engine.invalidate()
    return await get_thresholds(db, who)


# --------------------------------------------------------------------------- horario y turnos


class HoursItem(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0 = lunes")
    opens_at: time
    closes_at: time = Field(description="Si es anterior a opens_at, cierra al día siguiente")


class ShiftItem(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    starts_at: time
    ends_at: time


class Schedule(BaseModel):
    operating_hours: list[HoursItem] = Field(default_factory=list)
    shifts: list[ShiftItem] = Field(default_factory=list)
    business_day_cutoff: time = DEFAULT_CUTOFF

    @field_validator("shifts")
    @classmethod
    def _unique(cls, v: list[ShiftItem]) -> list[ShiftItem]:
        if len({s.name for s in v}) != len(v):
            raise ValueError("Nombres de turno repetidos")
        return v


async def _setting(db: DbSession, key: str) -> Any:
    row = await db.get(AppSetting, key)
    return row.value if row else None


async def _set(db: DbSession, key: str, value: Any, who: str) -> None:
    row = await db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=who))
    else:
        row.value, row.updated_by = value, who


@router.get("/schedule", response_model=Schedule)
async def get_schedule(db: DbSession, _: AuthUser) -> Schedule:
    hours = (await db.execute(select(OperatingHours).order_by(OperatingHours.weekday))).scalars()
    shifts = (await db.execute(select(Shift).order_by(Shift.starts_at))).scalars()
    cutoff = await _setting(db, "business_day_cutoff")
    return Schedule(
        operating_hours=[
            HoursItem(weekday=h.weekday, opens_at=h.opens_at, closes_at=h.closes_at) for h in hours
        ],
        shifts=[ShiftItem(name=s.name, starts_at=s.starts_at, ends_at=s.ends_at) for s in shifts],
        business_day_cutoff=time.fromisoformat(cutoff)
        if isinstance(cutoff, str)
        else DEFAULT_CUTOFF,
    )


@router.put("/schedule", response_model=Schedule)
async def put_schedule(body: Schedule, db: DbSession, who: AdminUser) -> Schedule:
    await db.execute(delete(OperatingHours))
    await db.execute(delete(Shift))
    db.add_all(OperatingHours(**h.model_dump()) for h in body.operating_hours)
    db.add_all(Shift(id=new_id("shf"), **s.model_dump()) for s in body.shifts)
    await _set(
        db,
        "business_day_cutoff",
        body.business_day_cutoff.isoformat(timespec="minutes"),
        who.username,
    )
    await record_audit(
        db, who.actor, "replace", "schedule", None, after=body.model_dump(mode="json")
    )
    await notify(db, CHANNEL_CONFIG, {"kind": "schedule"})
    await db.commit()
    return await get_schedule(db, who)


# --------------------------------------------------------------------------- visión


class TilingIn(BaseModel):
    enabled: bool = True
    tile_w: int = Field(default=560, ge=256, le=1920)
    tile_h: int = Field(default=600, ge=256, le=1920)
    overlap: int = Field(default=96, ge=0, le=400)
    batch_size: int = Field(default=4, ge=1, le=16)


class VisionSettings(BaseModel):
    model: str | None = Field(
        default=None, description="nano/small/medium/large; vacío = el del benchmark"
    )
    device: str | None = None
    person_confidence: float = Field(default=0.4, ge=0.05, le=0.95)
    tiling: TilingIn = Field(default_factory=TilingIn)
    standing_ratio: float = Field(default=2.6, ge=1.0, le=6.0)
    moving_ratio: float = Field(default=1.8, ge=1.0, le=6.0)
    seats_expand: float = Field(default=1.25, ge=1.0, le=2.0)
    debug_overlay: bool = False

    @field_validator("model")
    @classmethod
    def _model(cls, v: str | None) -> str | None:
        if v not in (None, "", "nano", "small", "medium", "large"):
            raise ValueError("Modelo no permitido (solo nano, small, medium, large)")
        return v or None


@router.get("/vision", response_model=VisionSettings)
async def get_vision(db: DbSession, _: AdminUser) -> VisionSettings:
    return VisionSettings.model_validate(await _setting(db, "vision") or {})


@router.put("/vision", response_model=VisionSettings)
async def put_vision(body: VisionSettings, db: DbSession, who: AdminUser) -> VisionSettings:
    await _set(db, "vision", body.model_dump(mode="json"), who.username)
    await record_audit(db, who.actor, "update", "vision", None, after=body.model_dump(mode="json"))
    await notify(db, CHANNEL_CONFIG, {"kind": "vision"})
    await db.commit()
    return body
