"""Dispositivo en casa, acceso de la familia y señales de actividad."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

OWNER_FK = "profiles.id"


class Device(Base):
    """Pantalla en casa de la persona mayor. Se autentica con un token propio."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(40))  # nombre con el que se le llama
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    # La cámara se comparte solo si la persona la enciende desde el dispositivo.
    camera_sharing: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class FamilyAccess(Base):
    """Permiso que la persona mayor da a un contacto para ver el panel familiar."""

    __tablename__ = "family_access"
    __table_args__ = (
        Index(
            "uq_family_access_active_contact",
            "contact_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(OWNER_FK, ondelete="CASCADE"), index=True
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contacts.id", ondelete="CASCADE"))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    delivery: Mapped[str] = mapped_column(String(16), default="queued")
    can_view_camera: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | revoked
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class DeviceEvent(Base):
    """Señal de actividad del dispositivo. Números y tipos: sin imagen, audio ni texto."""

    __tablename__ = "device_events"
    __table_args__ = (Index("ix_device_events_owner_created", "owner_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(OWNER_FK, ondelete="CASCADE"))
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    value: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = created_at()
