"""Proveedor OpenAI: Responses API, embeddings, moderación, transcripción y voz.

Formas de llamada verificadas contra la documentación oficial y el SDK instalado el
1 de octubre de 2026. **No se ejecutó contra la API real**: faltan credenciales.
"""

import openai
from openai import OpenAI
from pydantic import BaseModel

from app.core.config import Settings
from app.integrations.ai.base import (
    AgentDraft,
    AgentRequest,
    AIError,
    MemoryDraft,
    ModerationResult,
    ProposalDraft,
    SafetyClassification,
    Turn,
)
from app.integrations.ai.prompts import CLASSIFIER_PROMPT_VERSION, load_prompt

TTS_MAX_CHARS = 4096
MAX_PROPOSALS = 3
MAX_CANDIDATES = 3


class _WireProposal(BaseModel):
    """Esquema sin restricciones ni valores por defecto, apto para salida estricta."""

    kind: str
    title: str
    body: str
    activity_id: str | None
    contact_id: str | None


class _WireMemory(BaseModel):
    category: str
    content: str


class _WireDraft(BaseModel):
    reply: str
    proposals: list[_WireProposal]
    memory_candidates: list[_WireMemory]
    used_source_ids: list[str]


def _valid(model: type[BaseModel], items: list[BaseModel], limit: int) -> list:
    """Convierte al esquema interno y descarta lo que no lo cumple."""
    accepted = []
    for item in items[:limit]:
        try:
            accepted.append(model.model_validate(item.model_dump()))
        except ValueError:
            continue
    return accepted


class OpenAIProvider:
    name = "openai"
    speech_content_type = "audio/mpeg"

    def __init__(self, settings: Settings, client: OpenAI | None = None) -> None:
        self._settings = settings
        self._client = client or OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.openai_timeout_seconds,
            max_retries=2,
        )
        self.classifier_version = f"openai:{settings.openai_text_model}:{CLASSIFIER_PROMPT_VERSION}"
        self.embedding_model = f"openai:{settings.openai_embedding_model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            response = self._client.embeddings.create(
                model=self._settings.openai_embedding_model, input=texts
            )
        except openai.OpenAIError as exc:
            raise AIError(type(exc).__name__) from exc
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def moderate(self, text: str) -> ModerationResult:
        try:
            response = self._client.moderations.create(
                model=self._settings.openai_moderation_model, input=text
            )
            result = response.results[0]
        except (openai.OpenAIError, IndexError) as exc:
            raise AIError(type(exc).__name__) from exc
        categories = result.categories
        return ModerationResult(
            flagged=bool(result.flagged),
            self_harm=bool(categories.self_harm),
            self_harm_intent=bool(categories.self_harm_intent),
            self_harm_instructions=bool(categories.self_harm_instructions),
        )

    def classify_safety(self, *, message: str, recent_turns: list[Turn]) -> SafetyClassification:
        context = "\n".join(f"{turn.role}: {turn.content}" for turn in recent_turns)
        try:
            response = self._client.responses.parse(
                model=self._settings.openai_text_model,
                instructions=load_prompt(CLASSIFIER_PROMPT_VERSION),
                input=(
                    f"<turnos_anteriores>\n{context}\n</turnos_anteriores>\n"
                    f"<mensaje>\n{message}\n</mensaje>"
                ),
                text_format=SafetyClassification,
                temperature=0,
                store=False,
            )
        except openai.OpenAIError as exc:
            raise AIError(type(exc).__name__) from exc
        if response.output_parsed is None:
            raise AIError("Clasificación sin salida estructurada")
        return response.output_parsed

    def generate(self, request: AgentRequest) -> AgentDraft:
        history = [{"role": turn.role, "content": turn.content} for turn in request.turns]
        current = (
            f"{request.context_text}\n\n<mensaje_actual>\n{request.message}\n</mensaje_actual>"
        )
        try:
            response = self._client.responses.parse(
                model=self._settings.openai_text_model,
                instructions=request.instructions,
                input=[*history, {"role": "user", "content": current}],
                text_format=_WireDraft,
                store=False,
            )
        except openai.OpenAIError as exc:
            raise AIError(type(exc).__name__) from exc
        wire = response.output_parsed
        if wire is None or not wire.reply.strip():
            raise AIError("Respuesta sin salida estructurada")
        return AgentDraft(
            reply=wire.reply.strip(),
            proposals=_valid(ProposalDraft, wire.proposals, MAX_PROPOSALS),
            memory_candidates=_valid(MemoryDraft, wire.memory_candidates, MAX_CANDIDATES),
            used_source_ids=wire.used_source_ids,
        )

    def transcribe(self, *, audio: bytes, filename: str, language: str) -> str:
        try:
            response = self._client.audio.transcriptions.create(
                model=self._settings.openai_transcription_model,
                file=(filename, audio),
                language=language[:2],
            )
        except openai.OpenAIError as exc:
            raise AIError(type(exc).__name__) from exc
        return response.text.strip()

    def synthesize(self, text: str) -> bytes:
        try:
            response = self._client.audio.speech.create(
                model=self._settings.openai_tts_model,
                voice=self._settings.openai_tts_voice,
                input=text[:TTS_MAX_CHARS],
                instructions=self._settings.openai_tts_instructions,
                response_format="mp3",
            )
            return response.read()
        except openai.OpenAIError as exc:
            raise AIError(type(exc).__name__) from exc
