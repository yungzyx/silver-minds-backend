"""Esquemas de conversaciones y de la respuesta conversacional."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.actions.schemas import ProposalOut
from app.modules.memory.schemas import CandidateOut
from app.modules.safety.schemas import Mode, SupportOptions


class ConversationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationList(BaseModel):
    items: list[ConversationOut]
    total: int


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2000)
    client_message_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Identificador del cliente. Repetirlo devuelve la misma respuesta.",
    )


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    mode: Mode | None
    source: Literal["text", "voice"]
    created_at: datetime


class MessageList(BaseModel):
    items: list[MessageOut]
    total: int


class SourceRef(BaseModel):
    type: Literal["activity", "knowledge", "memory"]
    id: uuid.UUID
    title: str
    version: str


class ConversationReply(BaseModel):
    """Respuesta del agente. No incluye puntuaciones ni categorías internas de seguridad."""

    message_id: uuid.UUID
    reply: str
    mode: Mode
    proposals: list[ProposalOut] = Field(description="Borradores. Vacío si `mode` no es `normal`.")
    memory_candidates: list[CandidateOut] = Field(
        description="Pendientes de confirmación. Vacío si `mode` no es `normal`."
    )
    source_refs: list[SourceRef]
    support_options: SupportOptions | None
