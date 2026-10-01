"""Esquemas de contactos y aceptación."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    relationship: str | None = Field(default=None, max_length=40)
    support_opt_in: bool = Field(
        default=False,
        description="La persona marca si quiere poder pedirle apoyo a este contacto.",
    )


class ContactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    relationship: str | None = Field(default=None, max_length=40)
    support_opt_in: bool | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    relationship: str | None
    status: Literal["pending", "accepted", "declined", "revoked"]
    support_opt_in: bool
    consent_delivery: str
    created_at: datetime


class ContactList(BaseModel):
    items: list[ContactOut]
    total: int


class ConsentView(BaseModel):
    """Lo que ve quien abre el enlace. Abrirlo no cambia nada."""

    inviter_name: str
    contact_name: str
    status: Literal["pending", "accepted", "declined", "revoked"]
    expired: bool


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accept", "decline"]
