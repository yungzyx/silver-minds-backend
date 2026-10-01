"""Rutas de audio y consulta de trabajos."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from pydantic import BaseModel

from app.api.deps import CurrentProfile, DbSession
from app.core.config import get_settings
from app.core.errors import PayloadTooLargeError
from app.modules.voice import service

router = APIRouter(tags=["voz"])


class AudioAccepted(BaseModel):
    audio_id: uuid.UUID
    job_id: uuid.UUID
    status: Literal["uploaded"]
    duration_seconds: float


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    result: dict | None
    created_at: datetime
    finished_at: datetime | None


@router.post(
    "/conversations/{conversation_id}/audio",
    response_model=AudioAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enviar un audio",
    description=(
        "Máximo 10 MB y dos minutos, verificados sobre el contenido real del archivo. "
        "La transcripción pasa por el mismo flujo de seguridad y conversación que un "
        "mensaje escrito. Consulta el resultado en `GET /jobs/{id}`."
    ),
)
def upload_audio(
    conversation_id: uuid.UUID,
    db: DbSession,
    profile: CurrentProfile,
    file: Annotated[UploadFile, File(description="Audio de la persona")],
    speak_reply: Annotated[bool, Form()] = False,
) -> AudioAccepted:
    limit = get_settings().audio_max_bytes
    data = file.file.read(limit + 1)  # nunca se lee más que el límite
    if len(data) > limit:
        raise PayloadTooLargeError("El audio supera los 10 MB.")
    audio, job_id = service.accept_upload(
        db, profile, conversation_id, data, speak_reply=speak_reply
    )
    return AudioAccepted(
        audio_id=audio.id,
        job_id=job_id,
        status="uploaded",
        duration_seconds=audio.duration_seconds,
    )


@router.get("/jobs/{job_id}", response_model=JobOut, summary="Estado de un trabajo")
def get_job(job_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> JobOut:
    job = service.get_job(db, profile.id, job_id)
    return JobOut(
        id=job.id,
        kind=job.kind,
        status=job.status,
        result=job.result,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


@router.get(
    "/audio/{audio_id}/reply",
    summary="Respuesta hablada",
    response_class=Response,
    responses={200: {"content": {"audio/mpeg": {}, "audio/wav": {}}}},
)
def reply_audio(audio_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> Response:
    data, content_type = service.get_reply_audio(db, profile.id, audio_id)
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "no-store"})
