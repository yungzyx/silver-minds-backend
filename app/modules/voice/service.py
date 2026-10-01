"""Voz por turnos: audio → transcripción → el mismo flujo de seguridad y conversación.

El audio original se elimina al terminar de procesarlo y la respuesta hablada, antes de
24 horas. No se interpreta tono ni prosodia.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import InvalidInputError, NotFoundError, PayloadTooLargeError
from app.integrations import registry
from app.integrations.ai.base import AIError
from app.integrations.storage.base import StorageError
from app.models import AudioUpload, Job, Profile
from app.modules.conversations import service as conversations
from app.modules.voice.probe import probe_audio
from app.worker import queue
from app.worker.queue import RetryLaterError

logger = logging.getLogger(__name__)


def accept_upload(
    db: Session,
    profile: Profile,
    conversation_id: uuid.UUID,
    data: bytes,
    *,
    speak_reply: bool,
) -> tuple[AudioUpload, uuid.UUID]:
    settings = get_settings()
    conversations.get_conversation(db, profile.id, conversation_id)
    if len(data) > settings.audio_max_bytes:
        raise PayloadTooLargeError("El audio supera los 10 MB.")
    if not data:
        raise InvalidInputError("El audio está vacío.")
    info = probe_audio(data)
    if info.duration_seconds > settings.audio_max_seconds:
        raise InvalidInputError("El audio supera los dos minutos.")

    audio_id = uuid.uuid4()
    path = f"{profile.id}/{audio_id}.{info.extension}"
    registry.get_storage().put(path, data, info.content_type)
    audio = AudioUpload(
        id=audio_id,
        owner_id=profile.id,
        conversation_id=conversation_id,
        storage_path=path,
        content_type=info.content_type,
        duration_seconds=info.duration_seconds,
        size_bytes=len(data),
        status="uploaded",
        speak_reply=speak_reply or profile.voice_replies,
        expires_at=utcnow() + timedelta(hours=settings.audio_retention_hours),
    )
    db.add(audio)
    db.flush()
    job_id = queue.enqueue(
        db,
        "process_audio",
        {"audio_id": str(audio.id)},
        owner_id=profile.id,
        idempotency_key=f"process_audio:{audio.id}",
        max_attempts=3,
    )
    audio.job_id = job_id
    db.flush()
    return audio, job_id


def get_job(db: Session, owner_id: uuid.UUID, job_id: uuid.UUID) -> Job:
    job = db.execute(
        select(Job).where(Job.id == job_id, Job.owner_id == owner_id)
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("El trabajo no existe.")
    return job


def get_reply_audio(db: Session, owner_id: uuid.UUID, audio_id: uuid.UUID) -> tuple[bytes, str]:
    audio = db.execute(
        select(AudioUpload).where(AudioUpload.id == audio_id, AudioUpload.owner_id == owner_id)
    ).scalar_one_or_none()
    if audio is None or audio.reply_audio_path is None or audio.status == "deleted":
        raise NotFoundError("No hay respuesta hablada disponible.")
    try:
        return registry.get_storage().get(audio.reply_audio_path), audio.reply_content_type
    except StorageError as exc:
        raise NotFoundError("No hay respuesta hablada disponible.") from exc


def _fail(audio_id: uuid.UUID) -> None:
    with session_scope() as db:
        audio = db.get(AudioUpload, audio_id)
        if audio is not None:
            audio.status = "failed"


def process_audio(payload: dict) -> dict:
    """Trabajo del worker."""
    audio_id = uuid.UUID(payload["audio_id"])
    with session_scope() as db:
        audio = db.get(AudioUpload, audio_id)
        if audio is None or audio.status in ("done", "deleted"):
            return {"outcome": "skipped"}
        audio.status = "processing"
        owner_id, conversation_id = audio.owner_id, audio.conversation_id
        path, speak_reply = audio.storage_path, audio.speak_reply
        language = db.get(Profile, owner_id).language

    ai, storage = registry.get_ai(), registry.get_storage()
    try:
        data = storage.get(path)
    except StorageError:
        _fail(audio_id)
        return {"outcome": "failed", "error": "audio_not_found"}
    try:
        transcript = ai.transcribe(audio=data, filename=path.rsplit("/", 1)[-1], language=language)
    except AIError as exc:
        _fail(audio_id)
        raise RetryLaterError("transcription_unavailable") from exc
    if not transcript.strip():
        _fail(audio_id)
        storage.delete([path])
        return {"outcome": "failed", "error": "empty_transcript"}

    # La transcripción entra al mismo flujo que un mensaje escrito. El identificador
    # fijo hace que un reintento del trabajo no duplique el turno.
    with session_scope() as db:
        reply = conversations.handle_message(
            db,
            db.get(Profile, owner_id),
            conversation_id,
            transcript,
            source="voice",
            client_message_id=f"audio:{audio_id}",
        )

    reply_path = None
    if speak_reply:
        try:
            reply_path = f"{owner_id}/{audio_id}.reply"
            storage.put(reply_path, ai.synthesize(reply.reply), ai.speech_content_type)
        except (AIError, StorageError):
            # Si falla la síntesis se conserva el texto.
            logger.warning("La síntesis de voz falló; se entrega solo el texto")
            reply_path = None

    storage.delete([path])  # el audio original ya no hace falta
    with session_scope() as db:
        audio = db.get(AudioUpload, audio_id)
        if audio is not None:
            audio.status = "done"
            audio.reply_audio_path = reply_path
            audio.reply_content_type = ai.speech_content_type if reply_path else None
    return {
        "outcome": "done",
        "transcript": transcript,
        "reply_audio_available": reply_path is not None,
        "audio_id": str(audio_id),
        "reply": reply.model_dump(mode="json"),
    }


def cleanup_expired(db: Session, now: datetime) -> int:
    """Elimina audios vencidos. Corre antes de las 24 horas desde la carga."""
    expired = list(
        db.execute(
            select(AudioUpload).where(
                AudioUpload.expires_at <= now, AudioUpload.status != "deleted"
            )
        ).scalars()
    )
    paths = [p for audio in expired for p in (audio.storage_path, audio.reply_audio_path) if p]
    if paths:
        registry.get_storage().delete(paths)
    for audio in expired:
        audio.status = "deleted"
        audio.deleted_at = now
        audio.reply_audio_path = None
    return len(expired)
