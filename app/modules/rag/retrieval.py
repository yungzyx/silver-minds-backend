"""Recuperación híbrida con autorización previa.

Cada colección se busca por texto y por vector; los rankings se combinan con
Reciprocal Rank Fusion. Los filtros de propietario, aprobación, vigencia y territorio
están en el ``WHERE`` de cada búsqueda: lo no autorizado nunca llega a ordenarse.
"""

import logging
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.text import estimate_tokens, words
from app.integrations import registry
from app.integrations.ai.base import AIError, ContextItem
from app.models import (
    Activity,
    ConfirmedMemory,
    IndexState,
    KnowledgeChunk,
    KnowledgeDocument,
    MemoryEmbedding,
    RetrievalRun,
)

logger = logging.getLogger(__name__)

RRF_K = 60
CANDIDATES_PER_SEARCH = 10
MAX_COSINE_DISTANCE = 0.8  # descarta vecinos sin relación con la consulta
TEXT_SEARCH_CONFIG = "es_unaccent"
_QUERY_STOPWORDS = frozenset(
    {
        "a", "al", "algo", "como", "con", "cual", "de", "del", "donde", "el", "en", "es",
        "esta", "hay", "la", "las", "le", "lo", "los", "me", "mi", "para", "por", "que",
        "se", "si", "su", "un", "una", "y", "yo", "puedo", "quiero", "hacer", "tengo",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Retrieved:
    item: ContextItem
    score: float
    tokens: int


def _text_query(query: str) -> ColumnElement | None:
    """tsquery con OR entre términos. Los términos son solo letras y dígitos."""
    terms = [word for word in words(query) if word not in _QUERY_STOPWORDS and len(word) > 2]
    if not terms:
        return None
    return func.to_tsquery(TEXT_SEARCH_CONFIG, " | ".join(dict.fromkeys(terms)))


def _knowledge_filter(territory: str, today: date) -> ColumnElement[bool]:
    return and_(
        KnowledgeDocument.approval_status == "approved",
        KnowledgeDocument.valid_from <= today,
        or_(KnowledgeDocument.valid_until.is_(None), KnowledgeDocument.valid_until >= today),
        KnowledgeDocument.territory == territory,
    )


def _activity_filter(territory: str, today: date) -> ColumnElement[bool]:
    return and_(
        Activity.approval_status == "approved",
        Activity.valid_from <= today,
        or_(Activity.valid_until.is_(None), Activity.valid_until >= today),
        Activity.territory == territory,
    )


def _ranked_ids(db: Session, statement: Select) -> list[uuid.UUID]:
    return list(db.execute(statement.limit(CANDIDATES_PER_SEARCH)).scalars())


def _fuse(scores: dict, kind: str, ranking: list[uuid.UUID]) -> None:
    for rank, item_id in enumerate(ranking, start=1):
        scores[(kind, item_id)] += 1.0 / (RRF_K + rank)


def _search(
    db: Session,
    *,
    owner_id: uuid.UUID,
    query: str,
    embedding: list[float] | None,
    territory: str,
    today: date,
) -> dict[tuple[str, uuid.UUID], float]:
    scores: dict[tuple[str, uuid.UUID], float] = defaultdict(float)
    tsquery = _text_query(query)
    knowledge = _knowledge_filter(territory, today)
    activities = _activity_filter(territory, today)
    owned = ConfirmedMemory.owner_id == owner_id

    if tsquery is not None:
        _fuse(
            scores,
            "knowledge",
            _ranked_ids(
                db,
                select(KnowledgeChunk.id)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(knowledge, KnowledgeChunk.tsv.op("@@")(tsquery))
                .order_by(func.ts_rank_cd(KnowledgeChunk.tsv, tsquery).desc()),
            ),
        )
        _fuse(
            scores,
            "activity",
            _ranked_ids(
                db,
                select(Activity.id)
                .where(activities, Activity.tsv.op("@@")(tsquery))
                .order_by(func.ts_rank_cd(Activity.tsv, tsquery).desc()),
            ),
        )
        _fuse(
            scores,
            "memory",
            _ranked_ids(
                db,
                select(ConfirmedMemory.id)
                .where(owned, ConfirmedMemory.tsv.op("@@")(tsquery))
                .order_by(func.ts_rank_cd(ConfirmedMemory.tsv, tsquery).desc()),
            ),
        )

    if embedding is not None:
        chunk_distance = KnowledgeChunk.embedding.cosine_distance(embedding)
        _fuse(
            scores,
            "knowledge",
            _ranked_ids(
                db,
                select(KnowledgeChunk.id)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(knowledge, chunk_distance < MAX_COSINE_DISTANCE)
                .order_by(chunk_distance),
            ),
        )
        activity_distance = Activity.embedding.cosine_distance(embedding)
        _fuse(
            scores,
            "activity",
            _ranked_ids(
                db,
                select(Activity.id)
                .where(activities, activity_distance < MAX_COSINE_DISTANCE)
                .order_by(activity_distance),
            ),
        )
        memory_distance = MemoryEmbedding.embedding.cosine_distance(embedding)
        _fuse(
            scores,
            "memory",
            _ranked_ids(
                db,
                select(ConfirmedMemory.id)
                .join(MemoryEmbedding, MemoryEmbedding.memory_id == ConfirmedMemory.id)
                .where(
                    owned,
                    MemoryEmbedding.owner_id == owner_id,
                    MemoryEmbedding.memory_version == ConfirmedMemory.version,
                    memory_distance < MAX_COSINE_DISTANCE,
                )
                .order_by(memory_distance),
            ),
        )
    return scores


def activity_text(activity: Activity) -> str:
    """Detalles de la actividad tomados del registro vigente, no de texto generado."""
    fields = [
        ("Descripción", activity.description),
        ("Cuándo", activity.schedule_text),
        ("Dónde", activity.location),
        ("Costo", activity.cost),
        ("Requisitos", activity.requirements),
        ("Accesibilidad", activity.accessibility),
    ]
    return "\n".join(f"{label}: {value}" for label, value in fields if value)


def _load(
    db: Session, owner_id: uuid.UUID, kind: str, item_id: uuid.UUID, territory: str, today: date
) -> ContextItem | None:
    """Carga el elemento repitiendo el filtro de autorización."""
    if kind == "memory":
        memory = db.execute(
            select(ConfirmedMemory).where(
                ConfirmedMemory.id == item_id, ConfirmedMemory.owner_id == owner_id
            )
        ).scalar_one_or_none()
        if memory is None:
            return None
        return ContextItem(
            "memory", str(memory.id), memory.category, memory.content, str(memory.version)
        )
    if kind == "activity":
        activity = db.execute(
            select(Activity).where(Activity.id == item_id, _activity_filter(territory, today))
        ).scalar_one_or_none()
        if activity is None:
            return None
        return ContextItem(
            "activity", str(activity.id), activity.title, activity_text(activity), activity.version
        )
    row = db.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.id == item_id, _knowledge_filter(territory, today))
    ).one_or_none()
    if row is None:
        return None
    chunk, document = row
    return ContextItem("knowledge", str(chunk.id), document.title, chunk.content, document.version)


def retrieve(
    db: Session,
    *,
    owner_id: uuid.UUID,
    query: str,
    territory: str,
    conversation_id: uuid.UUID | None = None,
    today: date | None = None,
) -> list[Retrieved]:
    """Hasta ``retrieval_max_items`` elementos y ``retrieval_max_tokens`` tokens en total."""
    settings = get_settings()
    today = today or utcnow().date()
    try:
        [embedding] = registry.get_ai().embed([query])
    except AIError:
        logger.warning("No se pudo calcular el embedding de la consulta; se usa solo texto")
        embedding = None

    scores = _search(
        db, owner_id=owner_id, query=query, embedding=embedding, territory=territory, today=today
    )
    selected: list[Retrieved] = []
    total_tokens = 0
    for (kind, item_id), score in sorted(scores.items(), key=lambda entry: -entry[1]):
        if len(selected) >= settings.retrieval_max_items:
            break
        item = _load(db, owner_id, kind, item_id, territory, today)
        if item is None:
            continue
        tokens = estimate_tokens(f"{item.title}\n{item.text}")
        if total_tokens + tokens > settings.retrieval_max_tokens:
            continue
        selected.append(Retrieved(item=item, score=score, tokens=tokens))
        total_tokens += tokens

    index_version = db.execute(select(IndexState.version)).scalar_one_or_none() or 0
    db.add(
        RetrievalRun(
            owner_id=owner_id,
            conversation_id=conversation_id,
            index_version=index_version,
            items=[
                {"type": r.item.type, "id": r.item.id, "version": r.item.version} for r in selected
            ],
            token_total=total_tokens,
        )
    )
    db.flush()
    return selected
