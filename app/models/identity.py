"""Perfil, preferencias y auditoría."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class Profile(Base):
    """Persona usuaria. ``id`` coincide con el ``sub`` del JWT de Supabase."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    preferred_name: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(8), default="es", server_default="es")
    timezone: Mapped[str] = mapped_column(
        String(64), default="America/Santiago", server_default="America/Santiago"
    )
    country: Mapped[str] = mapped_column(String(2), default="CL", server_default="CL")
    voice_replies: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Preference(Base):
    """Preferencia declarada por la persona. Se consulta de forma exacta."""

    __tablename__ = "preferences"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AuditEvent(Base):
    """Rastro de acciones. Nunca guarda contenido de mensajes ni de memorias."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"))
    actor: Mapped[str] = mapped_column(String(16))  # user | agent | worker | contact
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at()
