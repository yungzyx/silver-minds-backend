"""Seguridad, propuestas, invitaciones, seguimiento y operación."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

OWNER_FK = "profiles.id"


def _owner() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True)


class SafetyState(Base):
    """Estado de seguridad por persona. Es una ruta del software, no un diagnóstico."""

    __tablename__ = "safety_states"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="normal")
    last_route: Mapped[str] = mapped_column(String(16), default="normal")
    policy_version: Mapped[str] = mapped_column(String(32))
    since: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class SafetyEvent(Base):
    """Evento mínimo: ruta, capas y versiones. Sin texto del mensaje ni puntuaciones."""

    __tablename__ = "safety_events"
    __table_args__ = (Index("ix_safety_events_created", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    route: Mapped[str] = mapped_column(String(16))
    layers: Mapped[list] = mapped_column(JSONB)
    classifier_version: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created_at()


class SupportRequest(Base):
    """Mensaje a un contacto elegido por la persona. Solo se comparte el texto aprobado."""

    __tablename__ = "support_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(120))
    body: Mapped[str] = mapped_column(Text)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("activities.id", ondelete="SET NULL")
    )
    is_generic: Mapped[bool] = mapped_column(Boolean, default=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_version: Mapped[int | None] = mapped_column(Integer)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )
    proposal_version: Mapped[int] = mapped_column(Integer)
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contacts.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="queued")
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )
    proposal_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # reminder | followup
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at()


class ActivityFeedback(Base):
    __tablename__ = "activity_feedback"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"), index=True
    )
    happened: Mapped[bool] = mapped_column(Boolean)
    rating: Mapped[int | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Job(Base):
    """Cola persistente de trabajos diferidos."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    result: Mapped[dict | None] = mapped_column(JSONB)
    last_error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AudioUpload(Base):
    __tablename__ = "audio_uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = _owner()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    storage_path: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(64))
    duration_seconds: Mapped[float] = mapped_column(Float)
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    speak_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    reply_audio_path: Mapped[str | None] = mapped_column(String(300))
    reply_content_type: Mapped[str | None] = mapped_column(String(64))
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
