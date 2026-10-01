"""Interfaz de envío de correo.

Los tres errores separan lo que el worker puede hacer después:

- ``EmailRejectedError``: el proveedor rechazó el envío. No se reintenta.
- ``EmailTransientError``: el correo **no** salió (p. ej. conexión rechazada). Reintento seguro.
- ``EmailAmbiguousError``: no se sabe si salió. No se reenvía automáticamente.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    idempotency_key: str


class EmailRejectedError(Exception):
    pass


class EmailTransientError(Exception):
    pass


class EmailAmbiguousError(Exception):
    pass


class EmailSender(Protocol):
    name: str

    def send(self, message: EmailMessage) -> str:
        """Envía y devuelve el identificador del proveedor."""
        ...
