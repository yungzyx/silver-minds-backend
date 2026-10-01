"""Esquemas de perfil y preferencias."""

import uuid
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(StrEnum):
    """Categorías compartidas por preferencias y memorias."""

    interest = "interest"
    activity = "activity"
    schedule = "schedule"
    limit = "limit"
    support_style = "support_style"
    communication = "communication"


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None
    preferred_name: str | None
    language: str
    timezone: str
    country: str
    voice_replies: bool


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_name: str | None = Field(default=None, min_length=1, max_length=80)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
    timezone: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    voice_replies: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Zona horaria desconocida") from exc
        return value


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category
    value: str = Field(min_length=1, max_length=500)


class PreferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    value: str | None = Field(default=None, min_length=1, max_length=500)


class PreferenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: Category
    value: str
    created_at: datetime


class PreferenceList(BaseModel):
    items: list[PreferenceOut]
    total: int
