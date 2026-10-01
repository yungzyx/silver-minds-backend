"""Dispositivo y acceso familiar.

La persona mayor controla qué ve su familia: el acceso al panel es un permiso que ella
otorga a un contacto que aceptó participar, y la cámara solo se comparte mientras ella
la mantiene encendida desde el dispositivo.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.core.tokens import hash_token, new_token
from app.integrations.email.base import EmailMessage
from app.models import (
    Contact,
    Device,
    DeviceEvent,
    FamilyAccess,
    MemoryCandidate,
    Profile,
    Proposal,
)
from app.modules.devices.schemas import DeviceEventIn, DeviceSession, FamilyAccessIn
from app.modules.followup import audit
from app.modules.safety import service as safety
from app.worker import queue
from app.worker.delivery import run_delivery

FAMILY_ACCESS_TTL = timedelta(days=30)
PRESENCE_MIN_INTERVAL = timedelta(seconds=10)
DEFAULT_OWNER_NAME = "Una persona que usa Silver Minds"


# --- Dispositivos -------------------------------------------------------------------


def create_device(db: Session, owner_id: uuid.UUID, name: str) -> tuple[Device, str]:
    token, token_hash = new_token()
    device = Device(owner_id=owner_id, name=name.strip(), token_hash=token_hash, status="active")
    db.add(device)
    db.flush()
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="device.created",
        entity_type="device",
        entity_id=device.id,
    )
    return device, token


def list_devices(db: Session, owner_id: uuid.UUID) -> list[Device]:
    statement = select(Device).where(Device.owner_id == owner_id).order_by(Device.created_at)
    return list(db.execute(statement).scalars())


def revoke_device(db: Session, owner_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = db.execute(
        select(Device).where(Device.id == device_id, Device.owner_id == owner_id).with_for_update()
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError("El dispositivo no existe.")
    if device.status != "revoked":
        device.status = "revoked"
        device.revoked_at = utcnow()
        device.camera_sharing = False
        db.flush()
    return device


def device_by_token(db: Session, token: str) -> Device | None:
    return db.execute(
        select(Device).where(Device.token_hash == hash_token(token), Device.status == "active")
    ).scalar_one_or_none()


def touch(db: Session, device: Device, now: datetime | None = None) -> None:
    device.last_seen_at = now or utcnow()


def session_info(db: Session, device: Device, viewers: list[str]) -> DeviceSession:
    profile = db.get(Profile, device.owner_id)
    drafts = db.execute(
        select(func.count())
        .select_from(Proposal)
        .where(Proposal.owner_id == device.owner_id, Proposal.status == "draft")
    ).scalar_one()
    candidates = db.execute(
        select(func.count())
        .select_from(MemoryCandidate)
        .where(MemoryCandidate.owner_id == device.owner_id, MemoryCandidate.status == "pending")
    ).scalar_one()
    return DeviceSession(
        device_id=device.id,
        device_name=device.name,
        preferred_name=profile.preferred_name,
        camera_sharing=device.camera_sharing,
        viewers=viewers,
        mode=safety.current_mode(db, device.owner_id).value,
        can_resume=safety.can_resume(db, device.owner_id),
        pending_proposals=drafts,
        pending_memory_candidates=candidates,
    )


def record_event(db: Session, device: Device, data: DeviceEventIn) -> bool:
    """Guarda una señal. Las de presencia se limitan a una cada diez segundos."""
    if data.kind == "presence":
        latest = db.execute(
            select(func.max(DeviceEvent.created_at)).where(
                DeviceEvent.device_id == device.id, DeviceEvent.kind == "presence"
            )
        ).scalar_one_or_none()
        if latest is not None and utcnow() - latest < PRESENCE_MIN_INTERVAL:
            return False
        if not device.camera_sharing:
            return False  # con la cámara apagada no se mide nada con ella
    db.add(
        DeviceEvent(
            owner_id=device.owner_id,
            device_id=device.id,
            kind=data.kind,
            value=data.value,
            created_at=utcnow(),
        )
    )
    db.flush()
    return True


def set_camera_sharing(db: Session, device_id: uuid.UUID, enabled: bool) -> bool:
    """La decisión es de la persona mayor y se toma desde el dispositivo."""
    device = db.execute(
        select(Device).where(Device.id == device_id, Device.status == "active").with_for_update()
    ).scalar_one_or_none()
    if device is None:
        return False
    if device.camera_sharing != enabled:
        device.camera_sharing = enabled
        db.add(
            DeviceEvent(
                owner_id=device.owner_id,
                device_id=device.id,
                kind="camera_on" if enabled else "camera_off",
                created_at=utcnow(),
            )
        )
        audit.record(
            db,
            owner_id=device.owner_id,
            actor="user",
            action="camera.enabled" if enabled else "camera.disabled",
            entity_type="device",
            entity_id=device.id,
        )
    db.flush()
    return device.camera_sharing


# --- Acceso familiar ----------------------------------------------------------------


def _accepted_contact(db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == owner_id)
    ).scalar_one_or_none()
    if contact is None:
        raise NotFoundError("El contacto no existe.")
    if contact.status != "accepted":
        raise InvalidInputError("Ese contacto todavía no aceptó participar.")
    return contact


def grant_access(db: Session, owner_id: uuid.UUID, data: FamilyAccessIn) -> FamilyAccess:
    contact = _accepted_contact(db, owner_id, data.contact_id)
    existing = db.execute(
        select(FamilyAccess).where(
            FamilyAccess.contact_id == contact.id, FamilyAccess.status == "active"
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("Ese contacto ya tiene acceso al panel.")
    access = FamilyAccess(
        owner_id=owner_id,
        contact_id=contact.id,
        can_view_camera=data.can_view_camera,
        status="active",
        delivery="queued",
        expires_at=utcnow() + FAMILY_ACCESS_TTL,
    )
    db.add(access)
    db.flush()
    queue.enqueue(
        db,
        "send_family_access",
        {"family_access_id": str(access.id)},
        owner_id=owner_id,
        idempotency_key=f"family_access:{access.id}",
    )
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="family_access.granted",
        entity_type="family_access",
        entity_id=access.id,
    )
    return access


def list_access(db: Session, owner_id: uuid.UUID) -> list[FamilyAccess]:
    statement = (
        select(FamilyAccess)
        .where(FamilyAccess.owner_id == owner_id)
        .order_by(FamilyAccess.created_at.desc())
    )
    return list(db.execute(statement).scalars())


def _owned_access(db: Session, owner_id: uuid.UUID, access_id: uuid.UUID) -> FamilyAccess:
    access = db.execute(
        select(FamilyAccess)
        .where(FamilyAccess.id == access_id, FamilyAccess.owner_id == owner_id)
        .with_for_update()
    ).scalar_one_or_none()
    if access is None:
        raise NotFoundError("El acceso no existe.")
    return access


def edit_access(
    db: Session, owner_id: uuid.UUID, access_id: uuid.UUID, can_view_camera: bool
) -> FamilyAccess:
    access = _owned_access(db, owner_id, access_id)
    if access.status != "active":
        raise ConflictError("El acceso fue revocado.")
    access.can_view_camera = can_view_camera
    db.flush()
    return access


def revoke_access(db: Session, owner_id: uuid.UUID, access_id: uuid.UUID) -> FamilyAccess:
    access = _owned_access(db, owner_id, access_id)
    if access.status != "revoked":
        access.status = "revoked"
        access.revoked_at = utcnow()
        access.token_hash = None
        db.flush()
        audit.record(
            db,
            owner_id=owner_id,
            actor="user",
            action="family_access.revoked",
            entity_type="family_access",
            entity_id=access.id,
        )
    return access


def access_by_token(db: Session, token: str, now: datetime | None = None) -> FamilyAccess | None:
    """Acceso vigente: activo, sin vencer y de un contacto que sigue aceptado."""
    row = db.execute(
        select(FamilyAccess, Contact.status)
        .join(Contact, Contact.id == FamilyAccess.contact_id)
        .where(FamilyAccess.token_hash == hash_token(token), FamilyAccess.status == "active")
    ).one_or_none()
    if row is None:
        return None
    access, contact_status = row
    if contact_status != "accepted" or access.expires_at < (now or utcnow()):
        return None
    return access


def is_access_valid(db: Session, access_id: uuid.UUID, *, camera: bool = False) -> bool:
    row = db.execute(
        select(FamilyAccess, Contact.status)
        .join(Contact, Contact.id == FamilyAccess.contact_id)
        .where(FamilyAccess.id == access_id, FamilyAccess.status == "active")
    ).one_or_none()
    if row is None:
        return False
    access, contact_status = row
    if contact_status != "accepted" or access.expires_at < utcnow():
        return False
    return access.can_view_camera or not camera


def issue_access_token(db: Session, access: FamilyAccess) -> str:
    """Genera un token nuevo para el acceso. El anterior deja de servir."""
    token, token_hash = new_token()
    access.token_hash = token_hash
    db.flush()
    return token


def family_link(token: str) -> str:
    # El token va en el fragmento: el navegador no lo envía al servidor ni queda en logs.
    return f"{get_settings().public_app_base_url}/family/#token={token}"


def _prepare_access_email(db: Session, access: FamilyAccess) -> EmailMessage | None:
    contact = db.get(Contact, access.contact_id)
    if access.status != "active" or contact is None or contact.status != "accepted":
        access.delivery = "cancelled"
        return None
    profile = db.get(Profile, access.owner_id)
    owner = (profile.preferred_name if profile else None) or DEFAULT_OWNER_NAME
    camera = (
        "También podrás ver la cámara, pero solo mientras esa persona la tenga encendida."
        if access.can_view_camera
        else "Este acceso no incluye la cámara."
    )
    return EmailMessage(
        to=contact.email,
        subject=f"{owner} te dio acceso a su panel de Silver Minds",
        text=(
            f"Hola, {contact.name}:\n\n"
            f"{owner} decidió compartir contigo señales de actividad de su dispositivo: "
            "si está encendido, cuándo lo usó y qué actividades aprobó. "
            "No incluye sus conversaciones.\n"
            f"{camera}\n\n"
            f"Abre el panel aquí:\n{family_link(issue_access_token(db, access))}\n\n"
            "El enlace es personal: no lo reenvíes. "
            f"{owner} puede quitar este acceso cuando quiera."
        ),
        idempotency_key=f"family_access:{access.id}",
    )


def deliver_access(payload: dict) -> dict:
    return run_delivery(
        FamilyAccess,
        uuid.UUID(payload["family_access_id"]),
        _prepare_access_email,
        status_attr="delivery",
    )
