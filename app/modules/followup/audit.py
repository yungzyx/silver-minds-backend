"""Auditoría: registra quién hizo qué sobre qué entidad, sin contenido."""

import uuid
from typing import Literal

from sqlalchemy.orm import Session

from app.models import AuditEvent

Actor = Literal["user", "agent", "worker", "contact"]


def record(
    db: Session,
    *,
    owner_id: uuid.UUID,
    actor: Actor,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
) -> None:
    db.add(
        AuditEvent(
            owner_id=owner_id,
            actor=actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )
