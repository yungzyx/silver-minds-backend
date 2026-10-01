"""Mantenimiento periódico: retención de datos."""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Conversation, SafetyEvent


def purge_expired(db: Session, now: datetime) -> dict:
    """Borra conversaciones y eventos de seguridad más antiguos que su retención."""
    settings = get_settings()
    conversations = db.execute(
        delete(Conversation).where(
            Conversation.updated_at < now - timedelta(days=settings.conversation_retention_days)
        )
    ).rowcount
    events = db.execute(
        delete(SafetyEvent).where(
            SafetyEvent.created_at < now - timedelta(days=settings.safety_event_retention_days)
        )
    ).rowcount
    return {"conversations_deleted": conversations, "safety_events_deleted": events}


def run(_payload: dict) -> dict:
    """Trabajo del worker. Cada tarea de limpieza se añade aquí."""
    with session_scope() as db:
        return purge_expired(db, utcnow())
