"""Esquemas de memoria."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.profiles.schemas import Category


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: Category
    content: str
    status: str
    created_at: datetime


class CandidateList(BaseModel):
    items: list[CandidateOut]
    total: int


class CandidateEdit(BaseModel):
    """Edición opcional antes de confirmar: la persona decide qué se recuerda."""

    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    content: str | None = Field(default=None, min_length=1, max_length=300)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: Category
    content: str
    version: int
    created_at: datetime
    updated_at: datetime


class MemoryList(BaseModel):
    items: list[MemoryOut]
    total: int


class MemoryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Category | None = None
    content: str | None = Field(default=None, min_length=1, max_length=300)
