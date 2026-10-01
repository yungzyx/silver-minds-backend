"""Voz con ElevenLabs: síntesis (texto a voz) y transcripción (voz a texto).

Rutas y campos verificados en la documentación oficial el 1 de octubre de 2026:
https://elevenlabs.io/docs/api-reference/text-to-speech/convert
https://elevenlabs.io/docs/api-reference/speech-to-text/convert

Reemplaza a OpenAI solo en la voz. El motivo está en docs/architecture.md.
"""

import httpx

from app.integrations.ai.base import AIError

API_URL = "https://api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_128"
USER_AGENT = "silver-minds-backend/0.1"


class ElevenLabsSpeech:
    name = "elevenlabs"
    speech_content_type = "audio/mpeg"

    def __init__(
        self,
        api_key: str,
        *,
        voice_id: str | None,
        tts_model: str,
        stt_model: str,
        timeout_seconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._voice_id = voice_id
        self._tts_model = tts_model
        self._stt_model = stt_model
        self._client = httpx.Client(
            base_url=API_URL,
            timeout=timeout_seconds,
            transport=transport,
            headers={"xi-api-key": api_key, "User-Agent": USER_AGENT},
        )

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AIError(f"ElevenLabs no respondió ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            # Solo el código: el cuerpo del error puede repetir el texto enviado.
            raise AIError(f"ElevenLabs respondió {response.status_code}")
        return response

    def list_voices(self) -> list[tuple[str, str]]:
        """Voces disponibles en la cuenta: ``(voice_id, nombre)``."""
        try:
            voices = self._request("GET", "/v1/voices").json()["voices"]
            return [(voice["voice_id"], voice.get("name", "")) for voice in voices]
        except (ValueError, KeyError, TypeError) as exc:
            raise AIError("Respuesta inesperada al listar voces") from exc

    def _voice(self) -> str:
        """La voz configurada o, si no hay, la primera de la cuenta."""
        if self._voice_id is None:
            voices = self.list_voices()
            if not voices:
                raise AIError("La cuenta de ElevenLabs no tiene voces disponibles")
            self._voice_id = voices[0][0]
        return self._voice_id

    def synthesize(self, text: str) -> bytes:
        response = self._request(
            "POST",
            f"/v1/text-to-speech/{self._voice()}",
            params={"output_format": OUTPUT_FORMAT},
            json={"text": text, "model_id": self._tts_model},
        )
        if not response.content:
            raise AIError("ElevenLabs devolvió un audio vacío")
        return response.content

    def transcribe(self, *, audio: bytes, filename: str, language: str) -> str:
        response = self._request(
            "POST",
            "/v1/speech-to-text",
            data={"model_id": self._stt_model, "language_code": language[:2]},
            files={"file": (filename, audio)},
        )
        try:
            return str(response.json()["text"]).strip()
        except (ValueError, KeyError) as exc:
            raise AIError("Transcripción sin texto") from exc
