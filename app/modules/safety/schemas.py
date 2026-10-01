"""Esquemas de apoyo. Ninguno expone puntuaciones ni categorías internas."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mode = Literal["normal", "clarify", "support", "urgent", "unavailable"]


class ResourceOut(BaseModel):
    id: str
    kind: Literal["emergency", "crisis_line"]
    name: str
    phone: str
    purpose: str
    availability: str
    cost: str
    source_url: str
    verified_at: date
    needs_review: bool = Field(description="La verificación superó el intervalo de revisión.")


class ResourceList(BaseModel):
    country: str
    version: str
    professional_review: Literal["pending", "approved"]
    items: list[ResourceOut]


class SupportContact(BaseModel):
    id: uuid.UUID
    name: str


class SupportOptions(BaseModel):
    resources: list[ResourceOut]
    contacts: list[SupportContact] = Field(
        description="Contactos aceptados que la persona marcó para pedir apoyo. "
        "El agente no recomienda a ninguno."
    )
    contact_note: str
    can_resume: bool


class SupportState(BaseModel):
    mode: Mode
    can_resume: bool


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: Literal[True] = Field(description="Decisión explícita de la persona.")


class SupportRequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: uuid.UUID
    message: str = Field(min_length=1, max_length=1000)


class SupportRequestEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1000)


class SupportRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    message: str
    status: Literal["draft", "queued", "sending", "sent", "send_uncertain", "failed", "cancelled"]
    approved_at: datetime | None
    sent_at: datetime | None
    created_at: datetime


class SupportRequestList(BaseModel):
    items: list[SupportRequestOut]
    total: int
