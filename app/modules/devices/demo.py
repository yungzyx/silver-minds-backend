"""Datos ficticios para la demostración. Nada aquí corresponde a personas reales."""

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.integrations import registry
from app.models import (
    ConfirmedMemory,
    Contact,
    Device,
    FamilyAccess,
    MemoryEmbedding,
    Preference,
)
from app.modules.devices import service
from app.modules.profiles import repository as profiles
from app.modules.rag.ingest import ingest_manifest

DEMO_OWNER_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://silver-minds.invalid/demo/rosa")
DEMO_MANIFEST = Path(__file__).resolve().parents[3] / "data" / "knowledge" / "manifest.yaml"
DEMO_MEMORY = "Le gusta la jardinería y cuidar sus plantas"
DEMO_PREFERENCE = "Prefiere las actividades por la mañana"


@dataclass(frozen=True)
class DemoLinks:
    device_url: str
    family_url: str
    owner_id: uuid.UUID


def setup_demo(db: Session, *, device_name: str = "Silvia") -> DemoLinks:
    """Crea (o renueva) una persona ficticia con su dispositivo y un acceso familiar.

    Salta los correos de aceptación: es un atajo exclusivo de la demostración.
    """
    settings = get_settings()
    if settings.environment == "production":
        raise RuntimeError("La demostración no se puede preparar en producción.")

    ingest_manifest(db, DEMO_MANIFEST)
    profile = profiles.get_or_create_profile(db, DEMO_OWNER_ID, "rosa.demo@example.com")
    profile.preferred_name = "Rosa"

    contact = db.execute(
        select(Contact).where(
            Contact.owner_id == profile.id, Contact.email == "camila.demo@example.com"
        )
    ).scalar_one_or_none()
    if contact is None:
        contact = Contact(
            owner_id=profile.id,
            name="Camila",
            email="camila.demo@example.com",
            relationship="hija",
            consent_delivery="skipped_demo",
        )
        db.add(contact)
    contact.status = "accepted"
    contact.accepted_at = contact.accepted_at or utcnow()
    contact.support_opt_in = True
    db.flush()

    if not db.execute(select(Preference.id).where(Preference.owner_id == profile.id)).first():
        db.add(Preference(owner_id=profile.id, category="schedule", value=DEMO_PREFERENCE))
    if not db.execute(
        select(ConfirmedMemory.id).where(ConfirmedMemory.owner_id == profile.id)
    ).first():
        memory = ConfirmedMemory(owner_id=profile.id, category="interest", content=DEMO_MEMORY)
        db.add(memory)
        db.flush()
        [embedding] = registry.get_ai().embed([DEMO_MEMORY])
        db.add(
            MemoryEmbedding(
                memory_id=memory.id, owner_id=profile.id, embedding=embedding, memory_version=1
            )
        )

    # Los enlaces anteriores dejan de servir: cada preparación entrega tokens nuevos.
    db.execute(
        update(Device)
        .where(Device.owner_id == profile.id, Device.status == "active")
        .values(status="revoked", revoked_at=utcnow(), camera_sharing=False)
    )
    _, device_token = service.create_device(db, profile.id, device_name)

    access = db.execute(
        select(FamilyAccess).where(
            FamilyAccess.contact_id == contact.id, FamilyAccess.status == "active"
        )
    ).scalar_one_or_none()
    if access is None:
        access = FamilyAccess(
            owner_id=profile.id,
            contact_id=contact.id,
            status="active",
            delivery="skipped_demo",
            expires_at=utcnow() + service.FAMILY_ACCESS_TTL,
        )
        db.add(access)
    access.can_view_camera = True
    access.expires_at = utcnow() + service.FAMILY_ACCESS_TTL
    db.flush()
    family_token = service.issue_access_token(db, access)

    return DemoLinks(
        device_url=f"{settings.public_app_base_url}/device/#token={device_token}",
        family_url=service.family_link(family_token),
        owner_id=profile.id,
    )
