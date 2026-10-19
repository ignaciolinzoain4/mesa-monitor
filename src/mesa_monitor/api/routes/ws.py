"""WebSocket `/api/v1/ws` (SPEC §12.2): primero un `snapshot`, después eventos incrementales y
`heartbeat` cada 15 s. Credencial en `?token=` (JWT, API key o token de dispositivo)."""

import asyncio
import contextlib
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status

from mesa_monitor.api.deps import resolve_token
from mesa_monitor.api.state_view import snapshot
from mesa_monitor.core.events import SnapshotEvent
from mesa_monitor.core.ids import new_id

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def ws(websocket: WebSocket, token: str = "") -> None:
    app = websocket.app
    async with app.state.sessionmaker() as db:
        try:
            if not token:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED)
            await resolve_token(db, app.state.settings, token)
        except HTTPException:
            await websocket.close(code=4401, reason="No autorizado")
            return
    await websocket.accept()
    hub = app.state.events_hub
    queue = hub.subscribe()
    try:
        async with app.state.sessionmaker() as db:
            snap = await snapshot(db, app.state.camera_monitor)
        first = SnapshotEvent(id=new_id("evt"), ts=datetime.now(UTC), data=snap)
        await websocket.send_text(first.model_dump_json())

        async def drain_incoming() -> None:
            while True:
                await websocket.receive_text()  # pings del cliente; se ignoran

        reader = asyncio.create_task(drain_incoming())
        try:
            while True:
                getter = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {getter, reader}, timeout=30, return_when=asyncio.FIRST_COMPLETED
                )
                if reader in done:
                    getter.cancel()
                    break
                if getter not in done:
                    getter.cancel()
                    if not hub.is_subscribed(queue):
                        await websocket.close(code=4408, reason="Cliente demasiado lento")
                        break
                    continue
                await websocket.send_json(getter.result())
        finally:
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await reader
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
