"""Esquemas de propuestas, invitaciones, actividades y seguimiento."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalStatus = Literal["draft", "approved", "rejected", "cancelled", "completed"]
ProposalKind = Literal["activity", "invitation", "conversation_starter"]


class ProposalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: ProposalKind
    title: str
    body: str
    status: ProposalStatus
    version: int
    is_generic: bool = Field(
        description="`true`: idea general sin registro. `false`: actividad vigente del catálogo."
    )
    activity_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    scheduled_for: datetime | None
    created_at: datetime


class ProposalList(BaseModel):
    items: list[ProposalOut]
    total: int


class ProposalEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=1000)
    contact_id: uuid.UUID | None = None
    scheduled_for: datetime | None = None


class ProposalApprove(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(description="Versión que la persona revisó. Debe ser la vigente.")


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    happened: bool
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = Field(default=None, max_length=1000)


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    happened: bool
    rating: int | None
    comment: str | None
    created_at: datetime


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    contact_id: uuid.UUID
    status: Literal[
        "queued", "sending", "sent", "send_uncertain", "failed", "cancelled", "accepted", "declined"
    ]
    sent_at: datetime | None
    responded_at: datetime | None
    created_at: datetime


class InvitationList(BaseModel):
    items: list[InvitationOut]
    total: int


class InvitationView(BaseModel):
    """Lo que ve el contacto al abrir el enlace. Abrirlo no cambia nada."""

    inviter_name: str
    title: str
    body: str
    scheduled_for: datetime | None
    status: str
    expired: bool


class InvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: Literal["accept", "decline"]


class ActivityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    location: str | None
    schedule_text: str | None
    cost: str | None
    requirements: str | None
    accessibility: str | None
    territory: str
    source: str
    version: str
    valid_until: date | None


class ActivityList(BaseModel):
    items: list[ActivityOut]
    total: int


class ReminderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    kind: Literal["reminder", "followup"]
    due_at: datetime
    status: str


class ReminderList(BaseModel):
    items: list[ReminderOut]
    total: int
