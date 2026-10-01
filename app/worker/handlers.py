"""Registro de manejadores. La lógica vive en los servicios de cada módulo."""

from collections.abc import Callable

from app.modules.contacts import service as contacts
from app.modules.followup import maintenance
from app.modules.memory import service as memory

Handler = Callable[[dict], dict | None]

HANDLERS: dict[str, Handler] = {
    "embed_memory": memory.index_memory,
    "send_contact_consent": contacts.deliver_consent,
    "maintenance": maintenance.run,
}
