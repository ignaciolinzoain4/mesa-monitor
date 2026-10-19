"""Cámaras: listado con estado y snapshot (proxy a go2rtc). Solo administradores.

Las cámaras se definen en `config/local/cameras.yaml`; la base guarda un espejo. Por eso no hay
alta/baja de cámaras desde la API: se edita el YAML y se reinicia `go2rtc-config` + `go2rtc`.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from mesa_monitor.api.camera_monitor import CameraMonitor
from mesa_monitor.api.deps import AdminUser, AuthUser, DbSession
from mesa_monitor.core.enums import CameraStatus
from mesa_monitor.db.models import Camera
from mesa_monitor.vision.go2rtc import Go2rtcClient, SnapshotError, StreamKind

router = APIRouter(prefix="/cameras", tags=["cameras"])


class CameraOut(BaseModel):
    id: str
    name: str
    enabled: bool
    channel: int | None
    fps_detection: float
    expected_resolution: tuple[int, int] | None
    status: CameraStatus
    status_detail: str | None
    resolution: tuple[int, int] | None
    last_ok_at: datetime | None
    last_checked_at: datetime | None


def _monitor(request: Request) -> CameraMonitor:
    monitor: CameraMonitor = request.app.state.camera_monitor
    return monitor


def _to_out(cam: Camera, monitor: CameraMonitor) -> CameraOut:
    h = monitor.health(cam.id)
    expected = (
        (cam.expected_width, cam.expected_height)
        if cam.expected_width and cam.expected_height
        else None
    )
    return CameraOut(
        id=cam.id,
        name=cam.name,
        enabled=cam.enabled,
        channel=cam.channel,
        fps_detection=cam.fps_detection,
        expected_resolution=expected,
        status=h.status if h else CameraStatus.OFFLINE,
        status_detail=(h.detail if h else "Cámara deshabilitada"),
        resolution=h.resolution if h else None,
        last_ok_at=h.last_ok_at if h else None,
        last_checked_at=h.last_checked_at if h else None,
    )


@router.get("", response_model=list[CameraOut])
async def list_cameras(request: Request, db: DbSession, _: AdminUser) -> list[CameraOut]:
    cams = (await db.execute(select(Camera).order_by(Camera.id))).scalars().all()
    monitor = _monitor(request)
    return [_to_out(c, monitor) for c in cams]


@router.get(
    "/{camera_id}/snapshot",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}}},
)
async def get_snapshot(
    camera_id: str,
    request: Request,
    db: DbSession,
    _: AdminUser,
    stream: Annotated[StreamKind, Query(description="main = alta resolución, sub = detección")] = (
        StreamKind.MAIN
    ),
) -> Response:
    cam = await db.get(Camera, camera_id)
    if cam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cámara inexistente")
    if not cam.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="La cámara está deshabilitada")
    client: Go2rtcClient = request.app.state.go2rtc
    try:
        frame = await client.frame(camera_id, stream)
    except SnapshotError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    headers = {
        "Cache-Control": "no-store",
        # Id para calibrar el perfil de personal sobre esta misma imagen.
        "X-Snapshot-Id": request.app.state.snapshots.put(frame.jpeg),
        "Access-Control-Expose-Headers": "X-Snapshot-Id, X-Frame-Size",
    }
    if frame.width and frame.height:
        headers["X-Frame-Size"] = f"{frame.width}x{frame.height}"
    return Response(frame.jpeg, media_type="image/jpeg", headers=headers)


async def _proxy_mjpeg(url: str, not_ok_detail: str) -> Response:
    client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
    try:
        upstream = await client.send(client.build_request("GET", url), stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail="El servicio de video no responde"
        ) from exc
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=not_ok_detail)

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    return StreamingResponse(body(), media_type=upstream.headers.get("content-type"))


MJPEG: dict[int | str, dict[str, Any]] = {200: {"content": {"multipart/x-mixed-replace": {}}}}


@router.get("/{camera_id}/debug.mjpeg", responses=MJPEG)
async def debug_stream(camera_id: str, request: Request, db: DbSession, _: AdminUser) -> Response:
    """Overlay de debug del worker (cajas, tracks, personal/cliente, zonas). Apagado por defecto:
    se activa en Configuración → Visión. Solo para calibrar; no se graba."""
    if await db.get(Camera, camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cámara inexistente")
    url = f"{request.app.state.settings.worker_debug_url}/debug/{camera_id}.mjpeg"
    return await _proxy_mjpeg(url, "El overlay de debug está desactivado (Configuración → Visión)")


@router.get("/{camera_id}/live.mjpeg", responses=MJPEG)
async def live_stream(camera_id: str, request: Request, db: DbSession, _: AuthUser) -> Response:
    """Video en vivo (substream) desde go2rtc, con la autenticación de la API: go2rtc no tiene
    login y solo escucha en la misma PC. No se graba."""
    cam = await db.get(Camera, camera_id)
    if cam is None or not cam.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Cámara inexistente o deshabilitada")
    url = f"{request.app.state.settings.go2rtc_url}/api/stream.mjpeg?src={camera_id}"
    return await _proxy_mjpeg(url, "go2rtc no pudo abrir la cámara")
