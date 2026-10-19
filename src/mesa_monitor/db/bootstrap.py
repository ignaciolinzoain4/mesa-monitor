"""Tareas de arranque sobre la base: admin inicial y espejo de cámaras desde cameras.yaml."""

import logging

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from mesa_monitor.config.cameras import CamerasFile
from mesa_monitor.core.enums import UserRole
from mesa_monitor.core.ids import new_id
from mesa_monitor.db.models import Camera, User
from mesa_monitor.security import hash_password

log = logging.getLogger(__name__)
DEFAULT_ADMIN_PASSWORDS = {"", "cambiame"}


async def ensure_admin(session: AsyncSession, username: str, password: str) -> bool:
    """Crea el admin inicial si no hay ningún usuario. Devuelve True si lo creó."""
    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    if count:
        return False
    if not password:
        log.warning("No hay usuarios y ADMIN_PASSWORD está vacío: no se creó el admin inicial.")
        return False
    if password in DEFAULT_ADMIN_PASSWORDS:
        log.warning("ADMIN_PASSWORD tiene el valor de ejemplo. Cambialo en el .env cuanto antes.")
    session.add(
        User(
            id=new_id("usr"),
            username=username,
            password_hash=hash_password(password),
            role=UserRole.ADMIN,
        )
    )
    await session.commit()
    log.info("Admin inicial %r creado.", username)
    return True


async def sync_cameras(session: AsyncSession, cameras: CamerasFile) -> None:
    """Refleja cameras.yaml en la tabla `cameras` (fuente de verdad: el YAML).

    Las cámaras que ya no están en el YAML se deshabilitan, no se borran: sus zonas e historial
    siguen referenciándolas.
    """
    existing = {c.id: c for c in (await session.execute(select(Camera))).scalars()}
    for cam in cameras.cameras:
        row = existing.pop(cam.id, None) or Camera(id=cam.id)
        row.name = cam.name
        row.enabled = cam.enabled
        row.connection = None if cam.source else cam.connection
        row.channel = None if cam.source else cam.channel
        row.fps_detection = cam.fps_detection
        row.expected_width, row.expected_height = cam.expected_resolution or (None, None)
        session.add(row)
    if existing:
        await session.execute(
            update(Camera).where(Camera.id.in_(list(existing))).values(enabled=False)
        )
    await session.commit()
