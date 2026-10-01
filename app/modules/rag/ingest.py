"""Ingesta administrativa de conocimiento revisado. No hay endpoint público de carga.

Cada documento declara fuente, responsable, fecha de revisión, versión, territorio,
aprobación y vigencia. Solo se indexan documentos aprobados; la recuperación vuelve a
filtrar aprobación y vigencia en cada consulta.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.text import estimate_tokens, normalize
from app.integrations import registry
from app.models import Activity, IndexState, KnowledgeChunk, KnowledgeDocument
from app.modules.rag.chunking import chunk_text

Approval = Literal["draft", "approved", "rejected"]


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    title: str
    source: str
    responsible: str
    reviewed_at: date
    version: str
    territory: str
    approval_status: Approval
    valid_from: date
    valid_until: date | None = None


class DocumentEntry(_Entry):
    collection: Literal["guide", "activity_catalog"]
    path: str
    source_url: str | None = None


class ActivityEntry(_Entry):
    description: str
    location: str | None = None
    schedule_text: str | None = None
    cost: str | None = None
    requirements: str | None = None
    accessibility: str | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentEntry] = []
    activities: list[ActivityEntry] = []


@dataclass
class IngestReport:
    documents_indexed: int = 0
    documents_unchanged: int = 0
    documents_not_approved: int = 0
    chunks_created: int = 0
    chunks_deduplicated: int = 0
    activities_upserted: int = 0
    activities_unchanged: int = 0
    index_version: int = 0
    errors: list[str] = field(default_factory=list)


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def activity_search_text(entry: ActivityEntry | Activity) -> str:
    parts = [entry.title, entry.description, entry.schedule_text, entry.location]
    return ". ".join(part for part in parts if part)


def ingest_manifest(db: Session, manifest_path: Path) -> IngestReport:
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = Manifest.model_validate(yaml.safe_load(handle))
    report = IngestReport()
    changed = False
    for entry in manifest.documents:
        text = (manifest_path.parent / entry.path).read_text(encoding="utf-8")
        changed |= _ingest_document(db, entry, text, report)
    for entry in manifest.activities:
        changed |= _upsert_activity(db, entry, report)
    report.index_version = _publish(db, bump=changed)
    return report


def _ingest_document(db: Session, entry: DocumentEntry, text: str, report: IngestReport) -> bool:
    digest = content_hash(text)
    existing = db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.slug == entry.slug, KnowledgeDocument.version == entry.version
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_hash != digest:
            report.errors.append(
                f"{entry.slug}: el contenido cambió sin cambiar la versión {entry.version}"
            )
        else:
            report.documents_unchanged += 1
        return False

    document = KnowledgeDocument(
        **entry.model_dump(exclude={"path"}),
        content_hash=digest,
    )
    db.add(document)
    db.flush()
    if entry.approval_status != "approved":
        report.documents_not_approved += 1
        return True

    # Una versión aprobada nueva reemplaza a las anteriores del mismo documento. Sus
    # fragmentos se eliminan para que la deduplicación no descarte contenido vigente.
    previous = select(KnowledgeDocument.id).where(
        KnowledgeDocument.slug == entry.slug, KnowledgeDocument.id != document.id
    )
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(previous)))
    db.execute(
        update(KnowledgeDocument)
        .where(KnowledgeDocument.id.in_(previous), KnowledgeDocument.approval_status == "approved")
        .values(approval_status="superseded")
    )
    known = set(db.execute(select(KnowledgeChunk.content_hash)).scalars())
    fresh = []
    for chunk in chunk_text(text):
        chunk_digest = content_hash(chunk)
        if chunk_digest in known:
            report.chunks_deduplicated += 1
            continue
        known.add(chunk_digest)
        fresh.append((chunk, chunk_digest))
    if fresh:
        embeddings = registry.get_ai().embed([chunk for chunk, _ in fresh])
        for index, ((chunk, chunk_digest), embedding) in enumerate(
            zip(fresh, embeddings, strict=True)
        ):
            db.add(
                KnowledgeChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=chunk,
                    token_count=estimate_tokens(chunk),
                    content_hash=chunk_digest,
                    embedding=embedding,
                )
            )
    report.documents_indexed += 1
    report.chunks_created += len(fresh)
    return True


def _upsert_activity(db: Session, entry: ActivityEntry, report: IngestReport) -> bool:
    values = entry.model_dump()
    existing = db.execute(select(Activity).where(Activity.slug == entry.slug)).scalar_one_or_none()
    if existing is not None and all(
        getattr(existing, name) == value for name, value in values.items()
    ):
        report.activities_unchanged += 1
        return False
    [embedding] = registry.get_ai().embed([activity_search_text(entry)])
    if existing is None:
        db.add(Activity(**values, embedding=embedding))
    else:
        for name, value in values.items():
            setattr(existing, name, value)
        existing.embedding = embedding
    report.activities_upserted += 1
    return True


def _publish(db: Session, *, bump: bool) -> int:
    state = db.get(IndexState, 1)
    if state is None:
        state = IndexState(id=1, version=0)
        db.add(state)
    if bump:
        state.version += 1
    if state.embedding_model is None:
        state.embedding_model = registry.get_ai().embedding_model
    db.flush()
    return state.version
