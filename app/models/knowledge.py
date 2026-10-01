"""Conocimiento revisado, actividades y trazabilidad de la recuperación."""

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import EMBEDDING_DIMENSIONS
from app.models.base import Base, created_at, updated_at, uuid_pk

_ACTIVITY_TSV = (
    "to_tsvector('es_unaccent', title || ' ' || description || ' ' || "
    "coalesce(schedule_text, '') || ' ' || coalesce(location, ''))"
)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_knowledge_documents_version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(80))
    collection: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str | None] = mapped_column(String(500))
    responsible: Mapped[str] = mapped_column(String(120))
    reviewed_at: Mapped[date] = mapped_column(Date)
    version: Mapped[str] = mapped_column(String(32))
    territory: Mapped[str] = mapped_column(String(8))
    approval_status: Mapped[str] = mapped_column(String(16))  # draft | approved | rejected
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at()


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_knowledge_chunks_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('es_unaccent', content)", persisted=True)
    )
    created_at: Mapped[datetime] = created_at()


class Activity(Base):
    """Actividad concreta. Sus detalles salen de este registro, no del texto generado."""

    __tablename__ = "activities"
    __table_args__ = (
        Index("ix_activities_tsv", "tsv", postgresql_using="gin"),
        Index(
            "ix_activities_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(String(200))
    schedule_text: Mapped[str | None] = mapped_column(String(200))
    cost: Mapped[str | None] = mapped_column(String(80))
    requirements: Mapped[str | None] = mapped_column(String(300))
    accessibility: Mapped[str | None] = mapped_column(String(300))
    territory: Mapped[str] = mapped_column(String(8))
    source: Mapped[str] = mapped_column(String(200))
    responsible: Mapped[str] = mapped_column(String(120))
    reviewed_at: Mapped[date] = mapped_column(Date)
    version: Mapped[str] = mapped_column(String(32))
    approval_status: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    tsv: Mapped[str] = mapped_column(TSVECTOR, Computed(_ACTIVITY_TSV, persisted=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class IndexState(Base):
    """Versión publicada del índice. Una sola fila."""

    __tablename__ = "index_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RetrievalRun(Base):
    """Qué se recuperó para un mensaje: identificadores y versiones, sin texto."""

    __tablename__ = "retrieval_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL")
    )
    index_version: Mapped[int] = mapped_column(Integer)
    items: Mapped[list] = mapped_column(JSONB)
    token_total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()
