"""Modelos Pydantic compartidos por varias rutas."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from mesa_monitor.core.enums import CleanMode, StaffZoneKind
from mesa_monitor.core.geometry import validate_polygon

PolygonIn = Annotated[list[list[float]], AfterValidator(validate_polygon)]
Sector = Annotated[
    str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=64)
]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class TableBase(BaseModel):
    # `label: 12` en un YAML editado a mano llega como número: se acepta como texto.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    label: Label
    sector: Sector
    capacity: int | None = Field(default=None, ge=1, le=50)
    enabled: bool = True
    plan_x: float = 0
    plan_y: float = 0
    plan_w: float = Field(default=4, gt=0)
    plan_h: float = Field(default=4, gt=0)
    plan_rotation: float = 0


class TableIn(TableBase):
    pass


class TablePatch(BaseModel):
    label: Label | None = None
    sector: Sector | None = None
    capacity: int | None = Field(default=None, ge=1, le=50)
    enabled: bool | None = None
    plan_x: float | None = None
    plan_y: float | None = None
    plan_w: float | None = Field(default=None, gt=0)
    plan_h: float | None = Field(default=None, gt=0)
    plan_rotation: float | None = None


class TableOut(TableBase):
    model_config = ConfigDict(from_attributes=True, coerce_numbers_to_str=True)

    id: str
    group_id: str | None = None
    camera_ids: list[str] = Field(default_factory=list)


class TableLayoutItem(BaseModel):
    id: str
    plan_x: float
    plan_y: float
    plan_w: float = Field(gt=0)
    plan_h: float = Field(gt=0)
    plan_rotation: float = 0


class TableViewIn(BaseModel):
    id: str | None = None
    table_id: str
    table_polygon: PolygonIn
    seats_polygon: PolygonIn
    priority: int = Field(default=0, ge=0, le=100)
    clean_mode: CleanMode = CleanMode.OBJECTS_AND_STAFF


class TableViewOut(TableViewIn):
    id: str


class StaffZoneIn(BaseModel):
    id: str | None = None
    kind: StaffZoneKind
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    polygon: PolygonIn


class StaffZoneOut(StaffZoneIn):
    id: str


class IgnoreMaskIn(BaseModel):
    id: str | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    polygon: PolygonIn


class IgnoreMaskOut(IgnoreMaskIn):
    id: str


class CameraZonesIn(BaseModel):
    table_views: list[TableViewIn] = Field(default_factory=list)
    staff_zones: list[StaffZoneIn] = Field(default_factory=list)
    ignore_masks: list[IgnoreMaskIn] = Field(default_factory=list)


class CameraZonesOut(BaseModel):
    camera_id: str
    table_views: list[TableViewOut]
    staff_zones: list[StaffZoneOut]
    ignore_masks: list[IgnoreMaskOut]
