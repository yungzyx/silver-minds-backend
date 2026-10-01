"""WebSockets de la transmisión en vivo.

El token no viaja en la URL: el cliente lo envía en el primer mensaje.

Dispositivo → servidor: ``{"type": "camera", "enabled": bool}`` y cuadros JPEG binarios.
Servidor → dispositivo: ``{"type": "viewers", "names": [...]}``.
Servidor → familia: ``{"type": "status", ...}`` y los cuadros, solo mientras la persona
mayor mantiene la cámara encendida.
"""

import asyncio
import contextlib
import json
import time
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.clock import utcnow
from app.core.db import session_scope
from app.models import Contact, Device, DeviceEvent
from app.modules.devices import service
from app.modules.devices.hub import Viewer, hub
from app.modules.followup import audit

router = APIRouter()

AUTH_TIMEOUT_SECONDS = 5.0
MAX_FRAME_BYTES = 300_000
MIN_FRAME_INTERVAL = 0.1  # como máximo 10 cuadros por segundo
REVALIDATE_SECONDS = 15.0
MIN_ENABLE_INTERVAL = 1.0  # encender la cámara, como máximo una vez por segundo
MAX_COMMAND_CHARS = 200
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
JPEG_MAGIC = b"\xff\xd8\xff"


async def _read_token(socket: WebSocket) -> str | None:
    try:
        raw = await asyncio.wait_for(socket.receive_text(), AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw)
    except (TimeoutError, ValueError, WebSocketDisconnect, KeyError, RuntimeError):
        return None
    if not isinstance(message, dict) or message.get("type") != "auth":
        return None
    token = message.get("token")
    return token if isinstance(token, str) and token else None


def _authenticate_device(token: str) -> tuple[uuid.UUID, uuid.UUID, bool] | None:
    with session_scope() as db:
        device = service.device_by_token(db, token)
        if device is None:
            return None
        service.touch(db, device)
        return device.id, device.owner_id, device.camera_sharing


def _set_camera(device_id: uuid.UUID, enabled: bool) -> bool:
    with session_scope() as db:
        return service.set_camera_sharing(db, device_id, enabled)


def _touch_device(device_id: uuid.UUID) -> bool:
    """Marca el latido y confirma que el dispositivo sigue autorizado."""
    with session_scope() as db:
        device = db.get(Device, device_id)
        if device is None or device.status != "active":
            return False
        service.touch(db, device)
        return True


@router.websocket("/device/stream")
async def device_stream(socket: WebSocket) -> None:
    await socket.accept()
    token = await _read_token(socket)
    identity = await run_in_threadpool(_authenticate_device, token) if token else None
    if identity is None:
        await socket.close(code=CLOSE_UNAUTHORIZED)
        return
    device_id, owner_id, sharing = identity
    await socket.send_json({"type": "ready", "camera_sharing": sharing})
    await hub.attach_device(owner_id, socket, sharing)
    last_frame = last_check = last_enable = 0.0
    try:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                break
            now = time.monotonic()
            if now - last_check > REVALIDATE_SECONDS:
                last_check = now
                if not await run_in_threadpool(_touch_device, device_id):
                    await socket.close(code=CLOSE_FORBIDDEN)
                    break
            if message.get("text") is not None:
                enabled = _camera_command(message["text"])
                if enabled is None:
                    continue
                # Apagar la cámara nunca se descarta. Encenderla se limita a una vez por
                # segundo; si se descarta, el dispositivo recibe el estado vigente.
                if enabled and now - last_enable < MIN_ENABLE_INTERVAL:
                    await socket.send_json({"type": "camera", "enabled": False})
                    continue
                if enabled:
                    last_enable = now
                await _apply_camera(socket, device_id, owner_id, enabled)
            elif message.get("bytes") is not None:
                frame = message["bytes"]
                acceptable = (
                    len(frame) <= MAX_FRAME_BYTES
                    and frame.startswith(JPEG_MAGIC)
                    and now - last_frame >= MIN_FRAME_INTERVAL
                )
                if acceptable:
                    last_frame = now
                    await hub.publish(owner_id, frame)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.detach_device(owner_id, socket)


def _camera_command(raw: str) -> bool | None:
    """Devuelve el estado pedido, o ``None`` si el mensaje no es una orden de cámara."""
    if len(raw) > MAX_COMMAND_CHARS:
        return None
    try:
        command = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(command, dict) or command.get("type") != "camera":
        return None
    return command.get("enabled") is True


async def _apply_camera(
    socket: WebSocket, device_id: uuid.UUID, owner_id: uuid.UUID, enabled: bool
) -> None:
    enabled = await run_in_threadpool(_set_camera, device_id, enabled)
    await hub.set_sharing(owner_id, enabled)
    await socket.send_json({"type": "camera", "enabled": enabled})


def _authenticate_viewer(token: str) -> tuple[uuid.UUID, uuid.UUID, str, bool, bool] | None:
    with session_scope() as db:
        access = service.access_by_token(db, token)
        if access is None:
            return None
        access.last_used_at = utcnow()
        contact = db.get(Contact, access.contact_id)
        active = [d for d in service.list_devices(db, access.owner_id) if d.status == "active"]
        device = active[0] if active else None
        sharing = bool(device and device.camera_sharing)
        if access.can_view_camera:
            audit.record(
                db,
                owner_id=access.owner_id,
                actor="contact",
                action="camera.view_started",
                entity_type="family_access",
                entity_id=access.id,
            )
            if device is not None:
                db.add(
                    DeviceEvent(
                        owner_id=access.owner_id,
                        device_id=device.id,
                        kind="view_start",
                        created_at=utcnow(),
                    )
                )
        return access.id, access.owner_id, contact.name, access.can_view_camera, sharing


def _viewer_still_allowed(access_id: uuid.UUID) -> bool:
    with session_scope() as db:
        return service.is_access_valid(db, access_id, camera=True)


@router.websocket("/family/stream")
async def family_stream(socket: WebSocket) -> None:
    await socket.accept()
    token = await _read_token(socket)
    identity = await run_in_threadpool(_authenticate_viewer, token) if token else None
    if identity is None:
        await socket.close(code=CLOSE_UNAUTHORIZED)
        return
    access_id, owner_id, name, can_view_camera, _ = identity
    if not can_view_camera:
        await socket.send_json({"type": "denied", "reason": "camera_not_allowed"})
        await socket.close(code=CLOSE_FORBIDDEN)
        return
    await hub.add_viewer(owner_id, Viewer(access_id=access_id, name=name, socket=socket))
    try:
        while True:
            # El permiso se vuelve a comprobar al entrar y luego a intervalos fijos: los
            # mensajes que envíe la familia no adelantan ni multiplican esa consulta.
            if not await run_in_threadpool(_viewer_still_allowed, access_id):
                await socket.close(code=CLOSE_FORBIDDEN)
                break
            deadline = time.monotonic() + REVALIDATE_SECONDS
            while (remaining := deadline - time.monotonic()) > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(socket.receive_text(), remaining)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await hub.remove_viewer(owner_id, access_id)
