"""Entrega de correo con estado explícito.

Toda entidad que se envía por correo recorre ``queued → sending → sent | failed |
send_uncertain``. ``sending`` se confirma en la base **antes** de llamar al proveedor:
si el proceso muere en medio, el siguiente intento encuentra ``sending`` y lo marca
``send_uncertain`` en lugar de reenviar.
"""

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import session_scope
from app.integrations import registry
from app.integrations.email.base import (
    EmailAmbiguousError,
    EmailMessage,
    EmailRejectedError,
    EmailTransientError,
)
from app.worker.queue import RetryLaterError

Prepare = Callable[[Session, Any], EmailMessage | None]


def run_delivery(
    model: type,
    entity_id: uuid.UUID,
    prepare: Prepare,
    *,
    status_attr: str = "status",
) -> dict:
    """Ejecuta una entrega.

    ``prepare`` vuelve a comprobar permisos dentro de la transacción y devuelve el
    mensaje. Si devuelve ``None`` no se envía nada (y puede haber dejado la entidad
    cancelada). Puede lanzar ``DeferError`` para posponer.
    """
    with session_scope() as db:
        entity = db.get(model, entity_id, with_for_update=True)
        if entity is None:
            return {"outcome": "skipped", "reason": "missing"}
        current = getattr(entity, status_attr)
        if current == "sending":
            setattr(entity, status_attr, "send_uncertain")
            return {"outcome": "send_uncertain"}
        if current != "queued":
            return {"outcome": "skipped", "reason": current}
        message = prepare(db, entity)
        if message is None:
            return {"outcome": "skipped", "reason": getattr(entity, status_attr)}
        setattr(entity, status_attr, "sending")

    try:
        status, provider_id = _send(message)
    except EmailTransientError as exc:
        _finish(model, entity_id, status_attr, "queued", None)
        raise RetryLaterError("email_transient") from exc

    _finish(model, entity_id, status_attr, status, provider_id)
    return {"outcome": status}


def _send(message: EmailMessage) -> tuple[str, str | None]:
    try:
        return "sent", registry.get_email().send(message)
    except EmailRejectedError:
        return "failed", None
    except EmailAmbiguousError:
        return "send_uncertain", None


def _finish(
    model: type, entity_id: uuid.UUID, status_attr: str, status: str, provider_id: str | None
) -> None:
    with session_scope() as db:
        entity = db.get(model, entity_id, with_for_update=True)
        if entity is None:
            return
        setattr(entity, status_attr, status)
        if status == "sent":
            if hasattr(entity, "provider_message_id"):
                entity.provider_message_id = provider_id
            if hasattr(entity, "sent_at"):
                entity.sent_at = utcnow()
