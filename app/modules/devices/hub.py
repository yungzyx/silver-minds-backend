"""Transmisión en vivo de la cámara: relevo en memoria, sin almacenamiento.

El dispositivo publica cuadros JPEG y el servidor los reenvía a quienes tienen permiso
mientras la persona mayor mantiene la cámara encendida. Ningún cuadro se guarda.

El estado vive en el proceso: con más de una instancia de la API haría falta un canal
compartido (por ejemplo Redis). Para el MVP se despliega una sola instancia.
"""

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket

logger = logging.getLogger(__name__)

SEND_TIMEOUT_SECONDS = 1.0
CLOSE_REVOKED = 4403
CLOSE_SLOW = 4408  # la conexión no recibe los cuadros a tiempo
CLOSE_REPLACED = 4000  # el mismo dispositivo se conectó desde otra ventana


@dataclass
class Viewer:
    access_id: uuid.UUID
    name: str
    socket: WebSocket


@dataclass
class Room:
    publisher: WebSocket | None = None
    sharing: bool = False
    viewers: dict[uuid.UUID, Viewer] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.publisher is None and not self.viewers


class StreamHub:
    def __init__(self) -> None:
        self._rooms: dict[uuid.UUID, Room] = {}

    def _room(self, owner_id: uuid.UUID) -> Room:
        return self._rooms.setdefault(owner_id, Room())

    def _cleanup(self, owner_id: uuid.UUID) -> None:
        room = self._rooms.get(owner_id)
        if room is not None and room.empty:
            del self._rooms[owner_id]

    def viewer_names(self, owner_id: uuid.UUID) -> list[str]:
        """Quién está mirando ahora. Solo cuenta mientras la cámara está compartida."""
        room = self._rooms.get(owner_id)
        if room is None or not room.sharing:
            return []
        return sorted(viewer.name for viewer in room.viewers.values())

    async def _send_json(self, socket: WebSocket, payload: dict) -> bool:
        try:
            await asyncio.wait_for(socket.send_json(payload), SEND_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - un socket caído no debe afectar a los demás
            return False
        return True

    async def _status_to_viewers(self, owner_id: uuid.UUID) -> None:
        room = self._rooms.get(owner_id)
        if room is None:
            return
        payload = {
            "type": "status",
            "sharing": room.sharing,
            "device_connected": room.publisher is not None,
        }
        for viewer in list(room.viewers.values()):
            await self._send_json(viewer.socket, payload)

    async def _viewers_to_device(self, owner_id: uuid.UUID) -> None:
        room = self._rooms.get(owner_id)
        if room is not None and room.publisher is not None:
            payload = {"type": "viewers", "names": self.viewer_names(owner_id)}
            await self._send_json(room.publisher, payload)

    async def attach_device(self, owner_id: uuid.UUID, socket: WebSocket, sharing: bool) -> None:
        room = self._room(owner_id)
        previous = room.publisher
        room.publisher, room.sharing = socket, sharing
        if previous is not None and previous is not socket:
            await self._close(previous, CLOSE_REPLACED)
        await self._status_to_viewers(owner_id)
        await self._viewers_to_device(owner_id)

    async def detach_device(self, owner_id: uuid.UUID, socket: WebSocket) -> None:
        room = self._rooms.get(owner_id)
        if room is None or room.publisher is not socket:
            return
        room.publisher = None
        await self._status_to_viewers(owner_id)
        self._cleanup(owner_id)

    async def set_sharing(self, owner_id: uuid.UUID, enabled: bool) -> None:
        self._room(owner_id).sharing = enabled
        await self._status_to_viewers(owner_id)
        await self._viewers_to_device(owner_id)

    async def add_viewer(self, owner_id: uuid.UUID, viewer: Viewer) -> None:
        room = self._room(owner_id)
        room.viewers[viewer.access_id] = viewer
        await self._status_to_viewers(owner_id)
        await self._viewers_to_device(owner_id)

    async def remove_viewer(self, owner_id: uuid.UUID, access_id: uuid.UUID) -> None:
        room = self._rooms.get(owner_id)
        if room is None or room.viewers.pop(access_id, None) is None:
            return
        await self._viewers_to_device(owner_id)
        self._cleanup(owner_id)

    async def publish(self, owner_id: uuid.UUID, frame: bytes) -> int:
        """Reenvía un cuadro. Devuelve a cuántas personas llegó."""
        room = self._rooms.get(owner_id)
        if room is None or not room.sharing:
            return 0
        viewers = list(room.viewers.values())
        # En paralelo: una conexión lenta no retrasa al dispositivo ni a los demás.
        results = await asyncio.gather(*(self._send_frame(v, frame) for v in viewers))
        for viewer, delivered in zip(viewers, results, strict=True):
            if not delivered:
                await self.remove_viewer(owner_id, viewer.access_id)
                await self._close(viewer.socket, CLOSE_SLOW)
        return sum(results)

    @staticmethod
    async def _send_frame(viewer: Viewer, frame: bytes) -> bool:
        try:
            await asyncio.wait_for(viewer.socket.send_bytes(frame), SEND_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - un socket caído no debe afectar a los demás
            return False
        return True

    async def kick_access(self, access_id: uuid.UUID) -> None:
        """Corta de inmediato la transmisión de un acceso revocado."""
        for owner_id, room in list(self._rooms.items()):
            viewer = room.viewers.get(access_id)
            if viewer is not None:
                await self.remove_viewer(owner_id, access_id)
                await self._close(viewer.socket, CLOSE_REVOKED)

    async def kick_device(self, owner_id: uuid.UUID) -> None:
        room = self._rooms.get(owner_id)
        if room is None or room.publisher is None:
            return
        socket, room.publisher, room.sharing = room.publisher, None, False
        await self._close(socket, CLOSE_REVOKED)
        await self._status_to_viewers(owner_id)

    @staticmethod
    async def _close(socket: WebSocket, code: int) -> None:
        with contextlib.suppress(Exception):  # ya estaba cerrado
            await socket.close(code=code)


hub = StreamHub()
