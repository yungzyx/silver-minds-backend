import io
import uuid
import wave
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.db import session_scope
from app.integrations.storage.base import StorageError
from app.integrations.storage.local import LocalStorage
from app.integrations.storage.supabase import SupabaseStorage
from app.models import AudioUpload, Message
from app.modules.followup import maintenance
from app.modules.voice import probe
from app.worker.main import run_pending
from tests.helpers import API, new_user


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(rate)
        output.writeframes(b"\x80" * int(seconds * rate))
    return buffer.getvalue()


def upload(client: TestClient, headers: dict, data: bytes, *, speak: bool = False, name="voz.wav"):
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    response = client.post(
        f"{API}/conversations/{conversation_id}/audio",
        headers=headers,
        files={"file": (name, data, "audio/wav")},
        data={"speak_reply": str(speak).lower()},
    )
    return conversation_id, response


def stored_files(providers) -> list[Path]:
    root = Path(providers.storage._root)
    return [path for path in root.rglob("*") if path.is_file()]


def test_audio_upload_requires_authentication(client: TestClient) -> None:
    response = client.post(
        f"{API}/conversations/{uuid.uuid4()}/audio", files={"file": ("a.wav", wav_bytes(1))}
    )

    assert response.status_code == 401


def test_audio_goes_through_the_same_conversation_flow(
    knowledge: None, client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    providers.ai.next_transcript = "Me gustaría una actividad con plantas y huerto"

    conversation_id, accepted = upload(client, headers, wav_bytes(2))
    run_pending()
    job = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()

    assert accepted.status_code == 202 and accepted.json()["duration_seconds"] == pytest.approx(2)
    assert job["status"] == "succeeded"
    assert job["result"]["transcript"] == "Me gustaría una actividad con plantas y huerto"
    assert job["result"]["reply"]["proposals"][0]["title"] == "Taller de huerto comunitario"
    sources = set(db.execute(select(Message.source)).scalars())
    assert sources == {"voice"}


def test_voice_message_gets_the_same_safety_evaluation(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    providers.ai.next_transcript = "Ya no quiero seguir viviendo"

    _, accepted = upload(client, headers, wav_bytes(1))
    run_pending()
    result = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()["result"]

    assert result["reply"]["mode"] == "support"
    assert result["reply"]["proposals"] == []
    assert {r["phone"] for r in result["reply"]["support_options"]["resources"]} == {"131", "*4141"}


def test_file_over_ten_megabytes_is_rejected(client: TestClient) -> None:
    _, headers = new_user(client)

    _, response = upload(client, headers, b"\x00" * (10 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_audio_longer_than_two_minutes_is_rejected(client: TestClient, providers) -> None:
    _, headers = new_user(client)

    _, response = upload(client, headers, wav_bytes(121))

    assert response.status_code == 422 and "dos minutos" in response.json()["error"]["message"]
    assert stored_files(providers) == []


def test_format_is_checked_on_the_real_content(client: TestClient, providers) -> None:
    _, headers = new_user(client)

    _, fake_wav = upload(client, headers, b"esto es texto, no audio", name="voz.wav")
    _, script = upload(client, headers, b"#!/bin/sh\nrm -rf /\n", name="voz.mp3")
    _, renamed = upload(client, headers, wav_bytes(1), name="documento.pdf")

    assert fake_wav.status_code == 422 and script.status_code == 422
    assert renamed.status_code == 202  # un WAV real se acepta aunque el nombre diga otra cosa
    assert len(stored_files(providers)) == 1


def test_missing_validator_rejects_instead_of_accepting_blindly(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, headers = new_user(client)
    monkeypatch.setattr(probe.shutil, "which", lambda _: None)

    _, response = upload(client, headers, wav_bytes(1))

    assert response.status_code == 503


def test_spoken_reply_is_optional_and_served_to_its_owner(client: TestClient, providers) -> None:
    _, rosa = new_user(client)
    _, pedro = new_user(client)

    _, accepted = upload(client, rosa, wav_bytes(1), speak=True)
    run_pending()
    audio_id = accepted.json()["audio_id"]
    job = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=rosa).json()
    reply = client.get(f"{API}/audio/{audio_id}/reply", headers=rosa)

    assert job["result"]["reply_audio_available"] is True
    assert reply.status_code == 200 and reply.headers["content-type"] == "audio/wav"
    assert reply.content[:4] == b"RIFF"
    assert client.get(f"{API}/audio/{audio_id}/reply", headers=pedro).status_code == 404
    assert client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=pedro).status_code == 404


def test_without_speak_reply_no_audio_is_synthesized(client: TestClient, providers) -> None:
    _, headers = new_user(client)

    _, accepted = upload(client, headers, wav_bytes(1))
    run_pending()

    assert "synthesize" not in providers.ai.calls
    reply = client.get(f"{API}/audio/{accepted.json()['audio_id']}/reply", headers=headers)
    assert reply.status_code == 404


def test_text_is_kept_when_speech_synthesis_fails(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    providers.ai.failing.add("synthesize")

    _, accepted = upload(client, headers, wav_bytes(1), speak=True)
    run_pending()
    job = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()

    assert job["status"] == "succeeded"
    assert job["result"]["reply"]["reply"]
    assert job["result"]["reply_audio_available"] is False


def test_transcription_failure_is_retried_without_duplicating_the_turn(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    providers.ai.failing.add("transcribe")
    _, accepted = upload(client, headers, wav_bytes(1))

    run_pending()
    first = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()
    providers.ai.failing.clear()
    run_pending(now=utcnow() + timedelta(minutes=5))
    run_pending(now=utcnow() + timedelta(minutes=10))
    final = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()

    assert first["status"] == "queued"
    assert final["status"] == "succeeded"
    assert len(list(db.execute(select(Message)).scalars())) == 2  # un turno: pregunta y respuesta


def test_transcription_that_keeps_failing_ends_as_failed(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    providers.ai.failing.add("transcribe")
    _, accepted = upload(client, headers, wav_bytes(1))

    for minutes in (0, 5, 30, 120):
        run_pending(now=utcnow() + timedelta(minutes=minutes))
    job = client.get(f"{API}/jobs/{accepted.json()['job_id']}", headers=headers).json()

    assert job["status"] == "failed" and job["result"] is None


def test_original_audio_is_deleted_right_after_processing(client: TestClient, providers) -> None:
    _, headers = new_user(client)
    _, accepted = upload(client, headers, wav_bytes(1), speak=True)
    before = [path.suffix for path in stored_files(providers)]

    run_pending()

    after = [path.suffix for path in stored_files(providers)]
    assert before == [".wav"]
    assert after == [".reply"]  # solo queda la respuesta hablada, hasta su vencimiento


def test_temporary_audio_is_cleaned_before_24_hours(
    client: TestClient, providers, db: Session
) -> None:
    _, headers = new_user(client)
    _, accepted = upload(client, headers, wav_bytes(1), speak=True)
    run_pending()
    audio = db.execute(select(AudioUpload)).scalar_one()
    lifetime = audio.expires_at - audio.created_at

    with session_scope() as session:
        early = maintenance.purge_expired(session, utcnow() + timedelta(hours=11))
    with session_scope() as session:
        late = maintenance.purge_expired(session, utcnow() + timedelta(hours=13))

    assert lifetime < timedelta(hours=24)
    assert early["audios_deleted"] == 0 and late["audios_deleted"] == 1
    assert stored_files(providers) == []
    reply = client.get(f"{API}/audio/{accepted.json()['audio_id']}/reply", headers=headers)
    assert reply.status_code == 404


def test_audio_cannot_be_uploaded_to_another_users_conversation(client: TestClient) -> None:
    _, rosa = new_user(client)
    _, pedro = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=rosa).json()["id"]

    response = client.post(
        f"{API}/conversations/{conversation_id}/audio",
        headers=pedro,
        files={"file": ("voz.wav", wav_bytes(1), "audio/wav")},
    )

    assert response.status_code == 404


def test_local_storage_rejects_paths_outside_its_root(tmp_path: Path) -> None:
    storage = LocalStorage(str(tmp_path / "root"))

    with pytest.raises(StorageError):
        storage.put("../fuera.txt", b"x", "text/plain")
    with pytest.raises(StorageError):
        storage.get("../../etc/passwd")


def test_supabase_storage_uses_the_private_object_routes() -> None:
    seen: list[tuple[str, str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        return httpx.Response(200, content=b"audio")

    storage = SupabaseStorage(
        "https://ref.supabase.co",
        "service-key",
        "audio-temp",
        transport=httpx.MockTransport(handler),
    )
    storage.put("u/a.wav", b"data", "audio/wav")
    downloaded = storage.get("u/a.wav")
    storage.delete(["u/a.wav"])

    assert downloaded == b"audio"
    assert [(method, path) for method, path, _ in seen] == [
        ("POST", "/storage/v1/object/audio-temp/u/a.wav"),
        ("GET", "/storage/v1/object/authenticated/audio-temp/u/a.wav"),
        ("DELETE", "/storage/v1/object/audio-temp"),
    ]
    assert seen[0][2]["authorization"] == "Bearer service-key"


def test_supabase_storage_errors_are_reported() -> None:
    storage = SupabaseStorage(
        "https://ref.supabase.co",
        "service-key",
        "audio-temp",
        transport=httpx.MockTransport(lambda _: httpx.Response(403)),
    )

    with pytest.raises(StorageError):
        storage.get("u/a.wav")
