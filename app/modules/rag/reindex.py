"""Reindexación: vuelve a generar todos los embeddings con el proveedor configurado.

Hace falta al cambiar de modelo de embeddings (por ejemplo, del simulador a OpenAI):
los vectores de dos modelos no son comparables entre sí.
"""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.integrations import registry
from app.models import (
    Activity,
    ConfirmedMemory,
    IndexState,
    KnowledgeChunk,
    MemoryEmbedding,
)
from app.modules.rag.ingest import activity_search_text

BATCH_SIZE = 64


@dataclass(frozen=True)
class ReindexReport:
    chunks: int
    activities: int
    memories: int
    embedding_model: str
    index_version: int


def _embed_in_batches(texts: list[str]) -> list[list[float]]:
    ai = registry.get_ai()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        vectors.extend(ai.embed(texts[start : start + BATCH_SIZE]))
    return vectors


def reindex_all(db: Session) -> ReindexReport:
    """Si el proveedor falla, la excepción revierte la transacción y el índice no cambia."""
    chunks = list(db.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.id)).scalars())
    for chunk, vector in zip(chunks, _embed_in_batches([c.content for c in chunks]), strict=True):
        chunk.embedding = vector

    activities = list(db.execute(select(Activity).order_by(Activity.id)).scalars())
    activity_texts = [activity_search_text(activity) for activity in activities]
    for activity, vector in zip(activities, _embed_in_batches(activity_texts), strict=True):
        activity.embedding = vector

    memories = list(db.execute(select(ConfirmedMemory).order_by(ConfirmedMemory.id)).scalars())
    memory_vectors = _embed_in_batches([memory.content for memory in memories])
    db.execute(delete(MemoryEmbedding))
    db.add_all(
        MemoryEmbedding(
            memory_id=memory.id,
            owner_id=memory.owner_id,
            embedding=vector,
            memory_version=memory.version,
        )
        for memory, vector in zip(memories, memory_vectors, strict=True)
    )

    state = db.get(IndexState, 1)
    if state is None:
        state = IndexState(id=1, version=0)
        db.add(state)
    state.version += 1
    state.embedding_model = registry.get_ai().embedding_model
    db.flush()
    return ReindexReport(
        chunks=len(chunks),
        activities=len(activities),
        memories=len(memories),
        embedding_model=state.embedding_model,
        index_version=state.version,
    )
