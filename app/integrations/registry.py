"""Selección de adaptadores según la configuración.

La API y el worker obtienen aquí sus integraciones. Las pruebas pueden sustituirlas
con ``override``.
"""

from functools import lru_cache

from app.core.config import get_settings
from app.integrations.ai.base import AIProvider
from app.integrations.ai.fake import FakeAIProvider
from app.integrations.email.base import EmailSender
from app.integrations.email.fake import FakeEmailSender
from app.integrations.email.resend import ResendEmailSender
from app.integrations.speech.elevenlabs import ElevenLabsSpeech
from app.integrations.storage.base import Storage
from app.integrations.storage.local import LocalStorage
from app.integrations.storage.supabase import SupabaseStorage

_overrides: dict[str, object] = {}


def override(**adapters: object) -> None:
    _overrides.update(adapters)


def clear_overrides() -> None:
    _overrides.clear()


@lru_cache
def _default_ai() -> AIProvider:
    settings = get_settings()
    if settings.ai_provider == "openai":
        from app.integrations.ai.openai_provider import OpenAIProvider

        return OpenAIProvider(settings)
    return FakeAIProvider()


@lru_cache
def _default_email() -> EmailSender:
    settings = get_settings()
    if settings.email_provider == "resend":
        return ResendEmailSender(settings.resend_api_key.get_secret_value(), settings.email_from)
    return FakeEmailSender(settings.local_outbox_dir)


@lru_cache
def _default_storage() -> Storage:
    settings = get_settings()
    if settings.storage_provider == "supabase":
        return SupabaseStorage(
            settings.supabase_url,
            settings.supabase_service_role_key.get_secret_value(),
            settings.supabase_storage_bucket,
        )
    return LocalStorage(settings.local_storage_dir)


@lru_cache
def _elevenlabs() -> ElevenLabsSpeech:
    settings = get_settings()
    return ElevenLabsSpeech(
        settings.elevenlabs_api_key.get_secret_value(),
        voice_id=settings.elevenlabs_voice_id or None,
        tts_model=settings.elevenlabs_tts_model,
        stt_model=settings.elevenlabs_stt_model,
    )


def get_speech() -> AIProvider | ElevenLabsSpeech:
    """Quien transcribe y habla. Por defecto, el mismo proveedor de IA."""
    if "speech" in _overrides:
        return _overrides["speech"]
    if get_settings().voice_provider == "elevenlabs":
        return _elevenlabs()
    return get_ai()


def get_ai() -> AIProvider:
    return _overrides.get("ai") or _default_ai()


def get_email() -> EmailSender:
    return _overrides.get("email") or _default_email()


def get_storage() -> Storage:
    return _overrides.get("storage") or _default_storage()
