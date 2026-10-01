"""Interfaz del proveedor de IA y tipos intercambiados con él."""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class AIError(Exception):
    """El proveedor falló o devolvió algo inutilizable."""


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class ModerationResult:
    """Marcas por categoría. No se conservan puntuaciones: no son probabilidades clínicas."""

    flagged: bool
    self_harm: bool
    self_harm_intent: bool
    self_harm_instructions: bool


class SafetyClassification(BaseModel):
    """Lectura contextual de un mensaje. Describe el texto, no diagnostica a la persona."""

    model_config = ConfigDict(extra="forbid")

    subject: Literal["self", "third_party", "quote_or_fiction"]
    timeframe: Literal["present", "past", "hypothetical"]
    negated: bool
    intent: Literal["none", "unclear", "ideation", "imminent"]
    bypass_attempt: bool


class ProposalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["activity", "invitation", "conversation_starter"]
    title: str = Field(max_length=120)
    body: str = Field(max_length=1000)
    activity_id: str | None = None
    contact_id: str | None = None


class MemoryDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["interest", "activity", "schedule", "limit", "support_style", "communication"]
    content: str = Field(max_length=300)


class AgentDraft(BaseModel):
    """Salida estructurada del agente. Son borradores: nada aquí ejecuta una acción."""

    model_config = ConfigDict(extra="forbid")

    reply: str
    proposals: list[ProposalDraft] = Field(default_factory=list)
    memory_candidates: list[MemoryDraft] = Field(default_factory=list)
    used_source_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ContextItem:
    """Dato autorizado que acompaña a un mensaje: memoria, actividad o fragmento de guía."""

    type: Literal["memory", "activity", "knowledge"]
    id: str
    title: str
    text: str
    version: str = ""


@dataclass(frozen=True)
class ContactRef:
    id: str
    name: str
    relationship: str = ""


@dataclass(frozen=True)
class AgentRequest:
    """Todo lo que el agente recibe para un turno.

    ``instructions`` es la única parte con rango de instrucción. ``context_text`` es la
    representación delimitada de los datos; el contenido recuperado nunca se mezcla con
    las instrucciones.
    """

    instructions: str
    context_text: str
    message: str
    preferred_name: str | None = None
    turns: tuple[Turn, ...] = ()
    items: tuple[ContextItem, ...] = ()
    contacts: tuple[ContactRef, ...] = field(default=())


class AIProvider(Protocol):
    name: str
    speech_content_type: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def moderate(self, text: str) -> ModerationResult: ...

    def classify_safety(
        self, *, message: str, recent_turns: list[Turn]
    ) -> SafetyClassification: ...

    def generate(self, request: AgentRequest) -> AgentDraft: ...

    def transcribe(self, *, audio: bytes, filename: str, language: str) -> str: ...

    def synthesize(self, text: str) -> bytes: ...
