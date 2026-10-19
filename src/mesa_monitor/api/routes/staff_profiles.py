"""Perfil de apariencia del personal (SPEC §7.2): calibración sobre un snapshot.

Flujo del dashboard: pide un snapshot (`GET /cameras/{id}/snapshot`, que devuelve el header
`X-Snapshot-Id`), el admin marca 3–10 mozos y 3–10 clientes y envía las cajas con ese id. Así
se calcula sobre la MISMA imagen que vio (la gente se mueve entre una toma y otra).
"""

import io
from datetime import datetime
from typing import Annotated, Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, Response, status
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from mesa_monitor.api.deps import AdminUser, DbSession
from mesa_monitor.api.routes.tables import actor_of
from mesa_monitor.core.color import calibrate, hsv_histogram, torso_crop
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.audit import CHANNEL_CONFIG, notify, record_audit
from mesa_monitor.db.models import StaffProfile
from mesa_monitor.vision.staff import StaffWeights

router = APIRouter(prefix="/staff-profiles", tags=["staff"])

NormBox = Annotated[list[float], Field(min_length=4, max_length=4)]


class CalibrateIn(BaseModel):
    snapshot_id: str
    name: str = Field(default="Perfil", min_length=1, max_length=120)
    staff_boxes: list[NormBox] = Field(min_length=2, max_length=30)
    customer_boxes: list[NormBox] = Field(min_length=2, max_length=30)
    activate: bool = False


class ProfileOut(BaseModel):
    id: str
    name: str
    active: bool
    threshold: float
    weights: dict[str, Any]
    created_at: datetime
    created_by: str | None
    quality: str | None = None
    separation: float | None = None


class CalibrationOut(ProfileOut):
    staff_scores: list[float]
    customer_scores: list[float]
    advice: str


def _out(p: StaffProfile) -> ProfileOut:
    cal = p.color_profile.get("calibration") or {}
    return ProfileOut(
        id=p.id,
        name=p.name,
        active=p.active,
        threshold=p.threshold,
        weights=p.weights,
        created_at=p.created_at,
        created_by=p.created_by,
        quality=cal.get("quality"),
        separation=cal.get("separation"),
    )


def _hists(jpeg: bytes, boxes: list[list[float]]) -> list[np.ndarray]:
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    w, h = img.size
    hsv = np.asarray(img.convert("HSV"))
    out = []
    for x1, y1, x2, y2 in boxes:
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Caja inválida")
        out.append(hsv_histogram(torso_crop(hsv, [x1 * w, y1 * h, x2 * w, y2 * h])))
    return out


ADVICE = {
    "buena": "El color de la ropa distingue bien al personal. Se usará con peso alto.",
    "regular": (
        "El color ayuda, pero no alcanza solo: el sistema lo combina con postura y recorridos."
    ),
    "insuficiente": (
        "El color del personal se parece demasiado al de los clientes (ej. ropa oscura). "
        "Recomendación: un distintivo de color llamativo (delantal) y volver a calibrar."
    ),
}


@router.get("", response_model=list[ProfileOut])
async def list_profiles(db: DbSession, _: AdminUser) -> list[ProfileOut]:
    rows = (
        await db.execute(select(StaffProfile).order_by(StaffProfile.created_at.desc()))
    ).scalars()
    return [_out(p) for p in rows]


@router.post("/calibrate", response_model=CalibrationOut, status_code=status.HTTP_201_CREATED)
async def calibrate_profile(
    body: CalibrateIn, request: Request, db: DbSession, user: AdminUser
) -> CalibrationOut:
    jpeg = request.app.state.snapshots.get(body.snapshot_id)
    if jpeg is None:
        raise HTTPException(
            status.HTTP_410_GONE, detail="La imagen ya no está disponible: pedí un snapshot nuevo"
        )
    try:
        cal = calibrate(_hists(jpeg, body.staff_boxes), _hists(jpeg, body.customer_boxes))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    weights = StaffWeights()
    weights_json = {
        k: getattr(weights, k) for k in weights.__dataclass_fields__ if k != "threshold"
    }
    weights_json["color"] = cal.suggested_color_weight
    color = cal.profile.to_json() | {
        "calibration": {
            "quality": cal.quality,
            "separation": cal.separation,
            "n_staff": len(body.staff_boxes),
            "n_customer": len(body.customer_boxes),
        }
    }
    profile = StaffProfile(
        id=new_id("spf"),
        name=body.name,
        active=False,
        color_profile=color,
        weights=weights_json,
        threshold=weights.threshold,
        created_by=user.username,
    )
    db.add(profile)
    await db.flush()
    if body.activate:
        await _activate(db, profile.id)
    await record_audit(
        db, actor_of(user), "calibrate", "staff_profile", profile.id, after=color["calibration"]
    )
    await db.commit()
    await db.refresh(profile)
    base = _out(profile)
    return CalibrationOut(
        **base.model_dump(),
        staff_scores=cal.staff_scores,
        customer_scores=cal.customer_scores,
        advice=ADVICE[cal.quality],
    )


async def _activate(db: DbSession, profile_id: str) -> None:
    await db.execute(update(StaffProfile).where(StaffProfile.active).values(active=False))
    await db.flush()
    await db.execute(update(StaffProfile).where(StaffProfile.id == profile_id).values(active=True))
    await notify(db, CHANNEL_CONFIG, {"kind": "staff_profile"})


@router.post("/{profile_id}/activate", response_model=ProfileOut)
async def activate_profile(profile_id: str, db: DbSession, user: AdminUser) -> ProfileOut:
    p = await db.get(StaffProfile, profile_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Perfil inexistente")
    await _activate(db, profile_id)
    await record_audit(db, actor_of(user), "activate", "staff_profile", profile_id)
    await db.commit()
    await db.refresh(p)
    return _out(p)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str, db: DbSession, user: AdminUser) -> Response:
    p = await db.get(StaffProfile, profile_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Perfil inexistente")
    await db.delete(p)
    await record_audit(db, actor_of(user), "delete", "staff_profile", profile_id)
    await notify(db, CHANNEL_CONFIG, {"kind": "staff_profile"})
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
