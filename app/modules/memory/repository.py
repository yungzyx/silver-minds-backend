"""Acceso a datos de memoria. Toda consulta filtra por propietario."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConfirmedMemory, MemoryCandidate


def list_candidates(db: Session, owner_id: uuid.UUID) -> list[MemoryCandidate]:
    statement = (
        select(MemoryCandidate)
        .where(MemoryCandidate.owner_id == owner_id, MemoryCandidate.status == "pending")
        .order_by(MemoryCandidate.created_at)
    )
    return list(db.execute(statement).scalars())


def get_candidate(
    db: Session, owner_id: uuid.UUID, candidate_id: uuid.UUID, *, lock: bool = False
) -> MemoryCandidate | None:
    statement = select(MemoryCandidate).where(
        MemoryCandidate.id == candidate_id, MemoryCandidate.owner_id == owner_id
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def list_memories(db: Session, owner_id: uuid.UUID) -> list[ConfirmedMemory]:
    statement = (
        select(ConfirmedMemory)
        .where(ConfirmedMemory.owner_id == owner_id)
        .order_by(ConfirmedMemory.created_at)
    )
    return list(db.execute(statement).scalars())


def get_memory(
    db: Session, owner_id: uuid.UUID, memory_id: uuid.UUID, *, lock: bool = False
) -> ConfirmedMemory | None:
    statement = select(ConfirmedMemory).where(
        ConfirmedMemory.id == memory_id, ConfirmedMemory.owner_id == owner_id
    )
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()
