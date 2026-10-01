"""Esquemas del dispositivo y del panel familiar."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.safety.schemas import Mode


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=2,
        max_length=40,
        pattern=r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ ]+$",
        description="Nombre con el que la persona llama al dispositivo.",
    )


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    status: Literal["active", "revoked"]
    camera_sharing: bool
    last_seen_at: datetime | None
    created_at: datetime


class DeviceCreated(DeviceOut):
    token: str = Field(description="Se muestra una sola vez. La base guarda solo su hash.")


class DeviceList(BaseModel):
    items: list[DeviceOut]
    total: int


class DeviceSession(BaseModel):
    """Lo que el dispositivo necesita para mostrarse. También sirve de latido."""

    device_id: uuid.UUID
    device_name: str
    preferred_name: str | None
    camera_sharing: bool
    viewers: list[str] = Field(description="Quién está mirando la cámara en este momento.")
    mode: Mode
    can_resume: bool
    pending_proposals: int
    pending_memory_candidates: int


class DeviceEventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["wake_button", "wake_name", "presence"]
    value: float | None = Field(default=None, ge=0, le=1)


class FamilyAccessIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: uuid.UUID
    can_view_camera: bool = False


class FamilyAccessEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_view_camera: bool


class FamilyAccessOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    can_view_camera: bool
    status: Literal["active", "revoked"]
    delivery: str
    expires_at: datetime
    last_used_at: datetime | None
    created_at: datetime


class FamilyAccessList(BaseModel):
    items: list[FamilyAccessOut]
    total: int


# --- Panel familiar -----------------------------------------------------------------


class DeviceStatus(BaseModel):
    name: str | None
    online: bool
    last_seen_at: datetime | None


class CameraStatus(BaseModel):
    allowed: bool = Field(description="La persona mayor dio permiso de cámara a este contacto.")
    sharing: bool = Field(description="La persona mayor tiene la cámara encendida ahora.")


class TodayMetrics(BaseModel):
    interactions: int = Field(description="Mensajes de la persona al asistente hoy.")
    calls_by_button: int
    calls_by_name: int
    presence_minutes: int = Field(
        description="Minutos de hoy con movimiento frente al dispositivo. "
        "Solo se mide con la cámara encendida."
    )
    last_interaction_at: datetime | None
    last_presence_at: datetime | None


class WeekMetrics(BaseModel):
    activities_approved: int
    activities_done: int
    invitations_sent: int
    invitations_accepted: int


class DayPoint(BaseModel):
    day: date
    interactions: int
    calls: int


class TimelineEntry(BaseModel):
    at: datetime
    kind: Literal[
        "wake_button", "wake_name", "camera_on", "camera_off", "activity_approved", "invitation"
    ]
    label: str


class FamilyOverview(BaseModel):
    """Señales de actividad. Nunca incluye conversaciones ni clasificación de seguridad."""

    person_name: str
    viewer_name: str
    generated_at: datetime
    device: DeviceStatus
    camera: CameraStatus
    today: TodayMetrics
    week: WeekMetrics
    series: list[DayPoint]
    timeline: list[TimelineEntry]
