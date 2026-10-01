"""Rutas de memorias candidatas y confirmadas."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentProfile, DbSession
from app.modules.memory import repository, service
from app.modules.memory.schemas import (
    CandidateEdit,
    CandidateList,
    CandidateOut,
    MemoryEdit,
    MemoryList,
    MemoryOut,
)

router = APIRouter(tags=["memoria"])


@router.get("/memory-candidates", response_model=CandidateList, summary="Candidatas pendientes")
def list_candidates(db: DbSession, profile: CurrentProfile) -> CandidateList:
    items = [CandidateOut.model_validate(c) for c in repository.list_candidates(db, profile.id)]
    return CandidateList(items=items, total=len(items))


@router.patch(
    "/memory-candidates/{candidate_id}",
    response_model=CandidateOut,
    summary="Editar una candidata antes de confirmar",
)
def edit_candidate(
    candidate_id: uuid.UUID, changes: CandidateEdit, db: DbSession, profile: CurrentProfile
) -> CandidateOut:
    candidate = service.edit_candidate(db, profile.id, candidate_id, changes)
    return CandidateOut.model_validate(candidate)


@router.post(
    "/memory-candidates/{candidate_id}/confirm",
    response_model=MemoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Confirmar: crea la memoria",
    description="Solo lo confirmado por la persona se guarda como memoria.",
)
def confirm_candidate(
    candidate_id: uuid.UUID,
    db: DbSession,
    profile: CurrentProfile,
    changes: CandidateEdit | None = None,
) -> MemoryOut:
    memory = service.confirm_candidate(db, profile.id, candidate_id, changes)
    return MemoryOut.model_validate(memory)


@router.post(
    "/memory-candidates/{candidate_id}/reject",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rechazar: elimina la candidata",
)
def reject_candidate(candidate_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> Response:
    service.reject_candidate(db, profile.id, candidate_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memories", response_model=MemoryList, summary="Memorias confirmadas")
def list_memories(db: DbSession, profile: CurrentProfile) -> MemoryList:
    items = [MemoryOut.model_validate(m) for m in repository.list_memories(db, profile.id)]
    return MemoryList(items=items, total=len(items))


@router.patch("/memories/{memory_id}", response_model=MemoryOut, summary="Editar una memoria")
def edit_memory(
    memory_id: uuid.UUID, changes: MemoryEdit, db: DbSession, profile: CurrentProfile
) -> MemoryOut:
    return MemoryOut.model_validate(service.update_memory(db, profile.id, memory_id, changes))


@router.delete(
    "/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar memoria y embedding",
)
def delete_memory(memory_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> Response:
    service.delete_memory(db, profile.id, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
