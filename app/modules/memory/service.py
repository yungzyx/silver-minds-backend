"""Memoria: el modelo propone, la persona confirma.

Solo lo confirmado se persiste como memoria. Rechazar elimina la candidata y borrar
una memoria elimina también su embedding.
"""

import uuid

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import session_scope
from app.core.errors import ConflictError, NotFoundError
from app.core.text import normalize
from app.integrations import registry
from app.integrations.ai.base import AIError, MemoryDraft
from app.models import ConfirmedMemory, MemoryCandidate, MemoryEmbedding
from app.modules.followup import audit
from app.modules.memory import repository
from app.modules.memory.schemas import CandidateEdit, MemoryEdit
from app.worker import queue
from app.worker.queue import RetryLaterError

MAX_PENDING_CANDIDATES = 20


def propose_candidates(
    db: Session, owner_id: uuid.UUID, conversation_id: uuid.UUID | None, drafts: list[MemoryDraft]
) -> list[MemoryCandidate]:
    """Guarda candidatas nuevas. Omite las que repiten algo ya confirmado o ya propuesto."""
    known = {normalize(m.content) for m in repository.list_memories(db, owner_id)}
    pending = repository.list_candidates(db, owner_id)
    known |= {normalize(c.content) for c in pending}
    room = MAX_PENDING_CANDIDATES - len(pending)
    created = []
    for draft in drafts:
        key = normalize(draft.content)
        if not key or key in known or room <= 0:
            continue
        candidate = MemoryCandidate(
            owner_id=owner_id,
            conversation_id=conversation_id,
            category=draft.category,
            content=draft.content.strip(),
        )
        db.add(candidate)
        created.append(candidate)
        known.add(key)
        room -= 1
    db.flush()
    return created


def _pending_candidate(
    db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID
) -> MemoryCandidate:
    candidate = repository.get_candidate(db, owner_id, candidate_id, lock=True)
    if candidate is None:
        raise NotFoundError("La memoria candidata no existe.")
    if candidate.status != "pending":
        raise ConflictError("La memoria candidata ya fue confirmada.")
    return candidate


def edit_candidate(
    db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID, changes: CandidateEdit
) -> MemoryCandidate:
    candidate = _pending_candidate(db, owner_id, candidate_id)
    for field, value in changes.model_dump(exclude_unset=True, mode="json").items():
        setattr(candidate, field, value)
    db.flush()
    return candidate


def confirm_candidate(
    db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID, changes: CandidateEdit | None = None
) -> ConfirmedMemory:
    candidate = _pending_candidate(db, owner_id, candidate_id)
    if changes is not None:
        for field, value in changes.model_dump(exclude_unset=True, mode="json").items():
            setattr(candidate, field, value)
    memory = ConfirmedMemory(
        owner_id=owner_id, category=candidate.category, content=candidate.content, version=1
    )
    db.add(memory)
    candidate.status = "confirmed"
    candidate.resolved_at = utcnow()
    db.flush()
    _enqueue_indexing(db, memory)
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="memory.confirmed",
        entity_type="memory",
        entity_id=memory.id,
    )
    return memory


def reject_candidate(db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID) -> None:
    candidate = _pending_candidate(db, owner_id, candidate_id)
    db.delete(candidate)
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="memory_candidate.rejected",
        entity_type="memory_candidate",
        entity_id=candidate_id,
    )


def _owned_memory(db: Session, owner_id: uuid.UUID, memory_id: uuid.UUID) -> ConfirmedMemory:
    memory = repository.get_memory(db, owner_id, memory_id, lock=True)
    if memory is None:
        raise NotFoundError("La memoria no existe.")
    return memory


def update_memory(
    db: Session, owner_id: uuid.UUID, memory_id: uuid.UUID, changes: MemoryEdit
) -> ConfirmedMemory:
    memory = _owned_memory(db, owner_id, memory_id)
    for field, value in changes.model_dump(exclude_unset=True, mode="json").items():
        setattr(memory, field, value)
    memory.version += 1
    # El embedding anterior describe un texto que ya no existe.
    db.execute(delete(MemoryEmbedding).where(MemoryEmbedding.memory_id == memory.id))
    db.flush()
    _enqueue_indexing(db, memory)
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="memory.updated",
        entity_type="memory",
        entity_id=memory.id,
    )
    return memory


def delete_memory(db: Session, owner_id: uuid.UUID, memory_id: uuid.UUID) -> None:
    memory = _owned_memory(db, owner_id, memory_id)
    db.delete(memory)  # memory_embeddings se elimina por ON DELETE CASCADE
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="memory.deleted",
        entity_type="memory",
        entity_id=memory_id,
    )


def _enqueue_indexing(db: Session, memory: ConfirmedMemory) -> None:
    queue.enqueue(
        db,
        "embed_memory",
        {"memory_id": str(memory.id), "version": memory.version},
        owner_id=memory.owner_id,
        idempotency_key=f"embed_memory:{memory.id}:{memory.version}",
    )


def index_memory(payload: dict) -> dict:
    """Trabajo del worker: genera el embedding de una memoria.

    La memoria pudo borrarse o editarse mientras el trabajo esperaba o mientras se
    calculaba el embedding. Por eso se comprueba la versión antes de calcular y, de
    nuevo, con la fila bloqueada, antes de escribir.
    """
    memory_id, version = uuid.UUID(payload["memory_id"]), int(payload["version"])

    with session_scope() as db:
        current = db.execute(
            select(ConfirmedMemory.content, ConfirmedMemory.version).where(
                ConfirmedMemory.id == memory_id
            )
        ).one_or_none()
    if current is None or current.version != version:
        return {"outcome": "discarded", "reason": "memory_changed_or_deleted"}

    try:
        [embedding] = registry.get_ai().embed([current.content])
    except AIError as exc:
        raise RetryLaterError("embedding_unavailable") from exc

    with session_scope() as db:
        memory = db.execute(
            select(ConfirmedMemory).where(ConfirmedMemory.id == memory_id).with_for_update()
        ).scalar_one_or_none()
        if memory is None or memory.version != version:
            return {"outcome": "discarded", "reason": "memory_changed_or_deleted"}
        db.execute(
            insert(MemoryEmbedding)
            .values(
                memory_id=memory.id,
                owner_id=memory.owner_id,
                embedding=embedding,
                memory_version=version,
            )
            .on_conflict_do_update(
                index_elements=["memory_id"],
                set_={"embedding": embedding, "memory_version": version},
            )
        )
    return {"outcome": "indexed"}
