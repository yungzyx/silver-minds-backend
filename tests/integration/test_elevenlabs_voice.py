import io
import json
import wave

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.integrations import registry
from app.integrations.ai.base import AIError
from app.integrations.speech.elevenlabs import ElevenLabsSpeech
from app.models import DeviceEvent
from app.worker.main import run_pending
from tests.helpers import API, new_user

MP3 = b"ID3\x03audio-de-prueba"


class StubSpeech:
    """Proveedor de voz «real» para probar el endpoint sin llamar a nadie."""

    name = "elevenlabs"
    speech_content_type = "audio/mpeg"

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.fail = False

    def synthesize(self, text: str) -> bytes:
        if self.fail:
            raise AIError("ElevenLabs respondió 401")
        self.spoken.append(text)
        return MP3

    def transcribe(self, *, audio: bytes, filename: str, language: str) -> str:
        return "Me gustaría una actividad con plantas y huerto"


def device_headers(client: TestClient, headers: dict) -> dict[str, str]:
    token = client.post(f"{API}/devices", headers=headers, json={"name": "Silvia"}).json()["token"]
    return {"Authorization": f"Device {token}"}


def adapter(handler, **kwargs) -> ElevenLabsSpeech:
    options = {"voice_id": "voz-1", "tts_model": "eleven_flash_v2_5", "stt_model": "scribe_v2"}
    return ElevenLabsSpeech(
        "clave-de-prueba", transport=httpx.MockTransport(handler), **{**options, **kwargs}
    )


def test_synthesis_request_matches_the_documented_contract() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            method=request.method,
            url=str(request.url),
            key=request.headers["xi-api-key"],
            body=json.loads(request.content),
        )
        return httpx.Response(200, content=MP3)

    audio = adapter(handler).synthesize("Hola, Rosa")

    assert audio == MP3
    assert seen["method"] == "POST"
    assert seen["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voz-1?output_format=mp3_44100_128"
    )
    assert seen["key"] == "clave-de-prueba"
    assert seen["body"] == {"text": "Hola, Rosa", "model_id": "eleven_flash_v2_5"}


def test_transcription_request_is_multipart_with_the_model_and_language() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            url=str(request.url), body=request.content, kind=request.headers["content-type"]
        )
        return httpx.Response(200, json={"text": " Hola ", "language_code": "spa"})

    text = adapter(handler).transcribe(audio=b"bytes", filename="voz.webm", language="es-CL")

    assert text == "Hola"
    assert seen["url"] == "https://api.elevenlabs.io/v1/speech-to-text"
    assert seen["kind"].startswith("multipart/form-data")
    assert b"scribe_v2" in seen["body"] and b'name="language_code"\r\n\r\nes' in seen["body"]
    assert b'filename="voz.webm"' in seen["body"]


def test_first_account_voice_is_used_when_none_is_configured() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/v1/voices":
            return httpx.Response(
                200,
                json={
                    "voices": [
                        {"voice_id": "abc", "name": "Uno"},
                        {"voice_id": "def", "name": "Dos"},
                    ]
                },
            )
        return httpx.Response(200, content=MP3)

    speech = adapter(handler, voice_id=None)
    speech.synthesize("Hola")
    speech.synthesize("Otra vez")

    assert speech.list_voices() == [("abc", "Uno"), ("def", "Dos")]
    assert paths[:3] == ["/v1/voices", "/v1/text-to-speech/abc", "/v1/text-to-speech/abc"]


@pytest.mark.parametrize("status", [401, 422, 429, 500])
def test_provider_errors_do_not_leak_the_response_body(status: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"detail": {"message": "texto privado del usuario"}})

    with pytest.raises(AIError) as error:
        adapter(handler).synthesize("texto privado del usuario")

    assert str(status) in str(error.value) and "privado" not in str(error.value)


def test_network_failure_and_empty_audio_are_errors() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(AIError):
        adapter(refuse).synthesize("Hola")
    with pytest.raises(AIError):
        adapter(lambda _: httpx.Response(200, content=b"")).synthesize("Hola")
    with pytest.raises(AIError):
        adapter(lambda _: httpx.Response(200, json={})).transcribe(
            audio=b"x", filename="a.wav", language="es"
        )


def test_elevenlabs_requires_its_key() -> None:
    with pytest.raises(ValidationError, match="ELEVENLABS_API_KEY"):
        Settings(voice_provider="elevenlabs", elevenlabs_api_key=None)


def test_without_a_voice_provider_the_device_uses_the_browser_voice(client: TestClient) -> None:
    _, headers = new_user(client)
    device = device_headers(client, headers)

    session = client.get(f"{API}/device/session", headers=device).json()
    response = client.post(f"{API}/device/speech", headers=device, json={"text": "Hola"})

    assert session["server_voice"] is False
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "voice_unavailable"


def test_device_speaks_with_the_configured_voice(client: TestClient, db: Session) -> None:
    speech = StubSpeech()
    registry.override(speech=speech)
    _, headers = new_user(client)
    device = device_headers(client, headers)

    session = client.get(f"{API}/device/session", headers=device).json()
    response = client.post(f"{API}/device/speech", headers=device, json={"text": "Hola, Rosa"})

    event = db.execute(select(DeviceEvent).where(DeviceEvent.kind == "speech")).scalar_one()
    assert session["server_voice"] is True
    assert response.status_code == 200 and response.content == MP3
    assert response.headers["content-type"] == "audio/mpeg"
    assert speech.spoken == ["Hola, Rosa"]
    assert event.value == len("Hola, Rosa")  # se guarda cuántos caracteres, no el texto


def test_speech_endpoint_is_only_for_the_device(client: TestClient) -> None:
    registry.override(speech=StubSpeech())
    _, headers = new_user(client)

    assert client.post(f"{API}/device/speech", json={"text": "Hola"}).status_code == 401
    assert (
        client.post(f"{API}/device/speech", headers=headers, json={"text": "Hola"}).status_code
        == 401
    )


def test_long_text_is_truncated_and_daily_limit_protects_credits(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    speech = StubSpeech()
    registry.override(speech=speech)
    monkeypatch.setattr(get_settings(), "voice_max_chars", 10)
    monkeypatch.setattr(get_settings(), "voice_daily_chars", 15)
    _, headers = new_user(client)
    device = device_headers(client, headers)

    first = client.post(f"{API}/device/speech", headers=device, json={"text": "a" * 50})
    second = client.post(f"{API}/device/speech", headers=device, json={"text": "b" * 50})

    assert first.status_code == 200 and speech.spoken == ["a" * 10]
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "voice_quota_exceeded"


def test_provider_failure_falls_back_instead_of_breaking(client: TestClient) -> None:
    speech = StubSpeech()
    speech.fail = True
    registry.override(speech=speech)
    _, headers = new_user(client)

    response = client.post(
        f"{API}/device/speech", headers=device_headers(client, headers), json={"text": "Hola"}
    )

    assert response.status_code == 503  # el dispositivo sigue con la voz del navegador


def test_backend_audio_messages_use_the_voice_provider(
    knowledge: None, client: TestClient, providers
) -> None:
    registry.override(speech=StubSpeech())
    _, headers = new_user(client)
    conversation_id = client.post(f"{API}/conversations", headers=headers).json()["id"]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(1)
        output.setframerate(8000)
        output.writeframes(b"\x80" * 8000)

    accepted = client.post(
        f"{API}/conversations/{conversation_id}/audio",
        headers=headers,
        files={"file": ("voz.wav", buffer.getvalue(), "audio/wav")},
        data={"speak_reply": "true"},
    ).json()
    run_pending()
    job = client.get(f"{API}/jobs/{accepted['job_id']}", headers=headers).json()
    reply = client.get(f"{API}/audio/{accepted['audio_id']}/reply", headers=headers)

    assert job["result"]["transcript"] == "Me gustaría una actividad con plantas y huerto"
    assert "transcribe" not in providers.ai.calls and "synthesize" not in providers.ai.calls
    assert reply.content == MP3 and reply.headers["content-type"] == "audio/mpeg"


def test_device_page_prefers_server_voice_and_falls_back(client: TestClient) -> None:
    voice = client.get("/device/voice.js").text
    device = client.get("/device/device.js").text

    assert "speakWithServer(text)) || (await speakWithBrowser(text))" in voice
    assert "session.server_voice" in device and "/device/speech" in device
