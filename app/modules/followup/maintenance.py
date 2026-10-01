"""Mantenimiento periódico: retención de datos."""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import session_scope
from app.models import Conversation, Job, SafetyEvent
from app.modules.voice import service as voice

FINISHED_JOB_STATUSES = ("succeeded", "failed", "cancelled")


def purge_expired(db: Session, now: datetime) -> dict:
    """Borra conversaciones, eventos de seguridad y audios que superaron su retención."""
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
    # El resultado de un trabajo de audio contiene la transcripción y la respuesta: es un
    # canal de entrega, no un registro. Se borra junto con los audios temporales.
    jobs = db.execute(
        delete(Job).where(
            Job.status.in_(FINISHED_JOB_STATUSES),
            Job.finished_at < now - timedelta(hours=settings.audio_retention_hours),
        )
    ).rowcount
    return {
        "jobs_deleted": jobs,
        "conversations_deleted": conversations,
        "safety_events_deleted": events,
        "audios_deleted": voice.cleanup_expired(db, now),
    }


def run(_payload: dict) -> dict:
    """Trabajo del worker. Cada tarea de limpieza se añade aquí."""
    with session_scope() as db:
        return purge_expired(db, utcnow())
