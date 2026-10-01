"""Contactos, conversaciones y memoria personal."""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import EMBEDDING_DIMENSIONS
from app.models.base import Base, created_at, updated_at, uuid_pk

OWNER_FK = "profiles.id"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        # Un mismo correo no puede estar dos veces activo para la misma persona.
        Index(
            "uq_contacts_owner_email_active",
            "owner_id",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("status IN ('pending', 'accepted')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    email: Mapped[str] = mapped_column(String(320))
    relationship: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    support_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    consent_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    consent_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_delivery: Mapped[str] = mapped_column(String(16), default="queued")
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "client_message_id", name="uq_messages_client_id"),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    mode: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(8), default="text")  # text | voice
    client_message_id: Mapped[str | None] = mapped_column(String(64))
    reply_to_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at()


class MemoryCandidate(Base):
    """Memoria propuesta por el modelo. No es memoria hasta que la persona la confirma."""

    __tablename__ = "memory_candidates"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | confirmed
    created_at: Mapped[datetime] = created_at()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConfirmedMemory(Base):
    __tablename__ = "confirmed_memories"
    __table_args__ = (Index("ix_confirmed_memories_tsv", "tsv", postgresql_using="gin"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('es_unaccent', content)", persisted=True)
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MemoryEmbedding(Base):
    """Embedding de una memoria. Se elimina en cascada junto con ella."""

    __tablename__ = "memory_embeddings"
    __table_args__ = (
        Index(
            "ix_memory_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("confirmed_memories.id", ondelete="CASCADE"), primary_key=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    memory_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()
