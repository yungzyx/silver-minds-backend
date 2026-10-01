"""Registro de manejadores. La lógica vive en los servicios de cada módulo."""

from collections.abc import Callable

from app.modules.actions import service as actions
from app.modules.contacts import service as contacts
from app.modules.devices import service as devices
from app.modules.followup import maintenance
from app.modules.memory import service as memory
from app.modules.safety import support_requests
from app.modules.voice import service as voice

Handler = Callable[[dict], dict | None]

HANDLERS: dict[str, Handler] = {
    "embed_memory": memory.index_memory,
    "send_contact_consent": contacts.deliver_consent,
    "send_invitation": actions.deliver_invitation,
    "send_reminder": actions.deliver_reminder,
    "send_family_access": devices.deliver_access,
    "send_support_request": support_requests.deliver_request,
    "process_audio": voice.process_audio,
    "maintenance": maintenance.run,
}
