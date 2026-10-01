"""Configuración de la aplicación, cargada desde variables de entorno."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_JWT_SECRET_CHARS = 32
# Valor de .env.example: se rechaza fuera de desarrollo, no es un secreto real.
EXAMPLE_JWT_SECRET = "cambia-este-secreto-local-de-al-menos-32-caracteres"  # noqa: S105
EMBEDDING_DIMENSIONS = 1536  # text-embedding-3-small; fija el tipo de las columnas vector


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = "postgresql+psycopg://localhost:5432/silver_minds"

    # Supabase Auth: JWKS (claves asimétricas) o secreto compartido HS256 heredado.
    supabase_url: str | None = None
    supabase_jwks_url: str | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: SecretStr | None = None
    supabase_storage_bucket: str = "audio-temp"

    ai_provider: Literal["fake", "openai"] = "fake"
    openai_api_key: SecretStr | None = None
    openai_text_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_moderation_model: str = "omni-moderation-latest"
    openai_transcription_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "alloy"
    openai_tts_instructions: str = (
        "Habla en español de Chile, con tono cálido, cercano y tranquilo. Ritmo pausado y "
        "claro, como una persona que conversa sin apuro."
    )
    openai_timeout_seconds: float = 30.0

    # Voz: "ai" usa el proveedor de IA (simulado u OpenAI); "elevenlabs" usa ElevenLabs.
    voice_provider: Literal["ai", "elevenlabs"] = "ai"
    elevenlabs_api_key: SecretStr | None = None
    elevenlabs_voice_id: str | None = None
    elevenlabs_tts_model: str = "eleven_flash_v2_5"
    elevenlabs_stt_model: str = "scribe_v2"
    voice_max_chars: int = 1200
    voice_daily_chars: int = 20000

    email_provider: Literal["fake", "resend"] = "fake"
    resend_api_key: SecretStr | None = None
    email_from: str = "Silver Minds <no-reply@example.com>"

    storage_provider: Literal["local", "supabase"] = "local"
    local_storage_dir: str = "var/storage"
    local_outbox_dir: str = "var/outbox"

    public_link_base_url: str = "http://localhost:8000/api/v1"
    public_app_base_url: str = "http://localhost:8000"
    default_country: str = "CL"

    daily_message_quota: int = 200
    recent_turns: int = 8
    retrieval_max_items: int = 5
    retrieval_max_tokens: int = 2500

    audio_max_bytes: int = 10 * 1024 * 1024
    audio_max_seconds: float = 120.0
    audio_retention_hours: int = 12
    conversation_retention_days: int = 30
    safety_event_retention_days: int = 30
    link_token_ttl_days: int = 7

    worker_poll_seconds: float = 2.0
    job_lock_timeout_seconds: int = 300

    @model_validator(mode="after")
    def _require_credentials_for_real_providers(self) -> Self:
        missing = []
        if self.ai_provider == "openai" and not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if self.voice_provider == "elevenlabs" and not self.elevenlabs_api_key:
            missing.append("ELEVENLABS_API_KEY")
        if self.email_provider == "resend" and not self.resend_api_key:
            missing.append("RESEND_API_KEY")
        if self.storage_provider == "supabase" and not (
            self.supabase_url and self.supabase_service_role_key
        ):
            missing.append("SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY")
        if not (self.supabase_jwks_url or self.supabase_jwt_secret):
            missing.append("SUPABASE_JWKS_URL o SUPABASE_JWT_SECRET")
        secret = self.supabase_jwt_secret.get_secret_value() if self.supabase_jwt_secret else ""
        weak = bool(secret) and (len(secret) < MIN_JWT_SECRET_CHARS or secret == EXAMPLE_JWT_SECRET)
        if weak and self.environment in ("staging", "production"):
            raise ValueError("SUPABASE_JWT_SECRET es el de ejemplo o tiene menos de 32 caracteres")
        if self.environment == "production":
            if not self.supabase_jwks_url:
                missing.append("SUPABASE_JWKS_URL (producción no acepta el secreto HS256)")
            if not self.supabase_url:
                missing.append("SUPABASE_URL (para verificar el emisor del token)")
        if self.audio_retention_hours >= 24:
            raise ValueError("AUDIO_RETENTION_HOURS debe ser menor que 24")
        if missing:
            raise ValueError(f"Faltan variables de entorno: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
