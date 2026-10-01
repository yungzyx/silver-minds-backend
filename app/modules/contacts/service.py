"""Contactos: nadie recibe invitaciones sin haber aceptado participar."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, TokenExpiredError
from app.core.tokens import hash_token, new_token
from app.integrations.email.base import EmailMessage
from app.models import Contact, FamilyAccess, Invitation, Profile, SupportRequest
from app.modules.contacts import repository
from app.modules.contacts.schemas import ConsentView, ContactIn, ContactUpdate
from app.modules.followup import audit
from app.worker import queue
from app.worker.delivery import run_delivery

DECISION_STATUS = {"accept": "accepted", "decline": "declined"}
DEFAULT_INVITER_NAME = "Una persona que usa Silver Minds"


def create_contact(db: Session, owner_id: uuid.UUID, data: ContactIn) -> Contact:
    if repository.find_active_by_email(db, owner_id, data.email) is not None:
        raise ConflictError("Ya tienes un contacto activo con ese correo.")
    contact = Contact(
        owner_id=owner_id,
        name=data.name.strip(),
        email=data.email,
        relationship=data.relationship,
        support_opt_in=data.support_opt_in,
        status="pending",
        consent_delivery="queued",
    )
    db.add(contact)
    db.flush()
    queue.enqueue(
        db,
        "send_contact_consent",
        {"contact_id": str(contact.id)},
        owner_id=owner_id,
        idempotency_key=f"contact_consent:{contact.id}",
    )
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="contact.created",
        entity_type="contact",
        entity_id=contact.id,
    )
    return contact


def _owned_contact(db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = repository.get_contact(db, owner_id, contact_id, lock=True)
    if contact is None:
        raise NotFoundError("El contacto no existe.")
    return contact


def update_contact(
    db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID, changes: ContactUpdate
) -> Contact:
    contact = _owned_contact(db, owner_id, contact_id)
    if contact.status == "revoked":
        raise ConflictError("El contacto fue revocado.")
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.flush()
    return contact


def revoke_contact(db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    """Revoca y, en la misma transacción, cancela todo envío que aún no salió."""
    contact = _owned_contact(db, owner_id, contact_id)
    if contact.status == "revoked":
        return contact
    contact.status = "revoked"
    contact.revoked_at = utcnow()
    contact.consent_token_hash = None
    if contact.consent_delivery == "queued":
        contact.consent_delivery = "cancelled"
    db.execute(
        update(Invitation)
        .where(Invitation.contact_id == contact.id, Invitation.status == "queued")
        .values(status="cancelled")
    )
    db.execute(
        update(SupportRequest)
        .where(
            SupportRequest.contact_id == contact.id, SupportRequest.status.in_(("draft", "queued"))
        )
        .values(status="cancelled")
    )
    db.execute(
        update(FamilyAccess)
        .where(FamilyAccess.contact_id == contact.id, FamilyAccess.status == "active")
        .values(status="revoked", revoked_at=utcnow(), token_hash=None)
    )
    db.flush()
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="contact.revoked",
        entity_type="contact",
        entity_id=contact.id,
    )
    return contact


def _inviter_name(db: Session, owner_id: uuid.UUID) -> str:
    profile = db.get(Profile, owner_id)
    return (profile.preferred_name if profile else None) or DEFAULT_INVITER_NAME


def _is_expired(contact: Contact, now: datetime) -> bool:
    return contact.consent_expires_at is not None and contact.consent_expires_at < now


def consent_view(db: Session, token: str, now: datetime | None = None) -> ConsentView:
    """Solo lectura. Abrir el enlace (o que un antivirus lo visite) no cambia estado."""
    contact = repository.get_by_consent_hash(db, hash_token(token))
    if contact is None:
        raise NotFoundError("El enlace no es válido.")
    return ConsentView(
        inviter_name=_inviter_name(db, contact.owner_id),
        contact_name=contact.name,
        status=contact.status,
        expired=_is_expired(contact, now or utcnow()),
    )


def decide_consent(db: Session, token: str, decision: str, now: datetime | None = None) -> Contact:
    contact = repository.get_by_consent_hash(db, hash_token(token), lock=True)
    if contact is None:
        raise NotFoundError("El enlace no es válido.")
    target = DECISION_STATUS[decision]
    if contact.status == target:
        return contact  # repetir la misma respuesta no cambia nada
    if contact.status != "pending":
        raise ConflictError("Esta solicitud ya fue respondida.")
    if _is_expired(contact, now or utcnow()):
        raise TokenExpiredError("El enlace venció. Pide que te inviten de nuevo.")
    contact.status = target
    if target == "accepted":
        contact.accepted_at = now or utcnow()
    db.flush()
    audit.record(
        db,
        owner_id=contact.owner_id,
        actor="contact",
        action=f"contact.{target}",
        entity_type="contact",
        entity_id=contact.id,
    )
    return contact


def _prepare_consent_email(db: Session, contact: Contact) -> EmailMessage | None:
    if contact.status != "pending":
        contact.consent_delivery = "cancelled"
        return None
    settings = get_settings()
    token, token_hash = new_token()
    contact.consent_token_hash = token_hash
    contact.consent_expires_at = utcnow() + timedelta(days=settings.link_token_ttl_days)
    inviter = _inviter_name(db, contact.owner_id)
    link = f"{settings.public_link_base_url}/contact-consents/{token}"
    return EmailMessage(
        to=contact.email,
        subject=f"{inviter} quiere agregarte como contacto en Silver Minds",
        text=(
            f"Hola, {contact.name}:\n\n"
            f"{inviter} quiere poder enviarte invitaciones a actividades y conversaciones "
            "a través de Silver Minds.\n\n"
            "Solo recibirás mensajes que esa persona haya revisado y aprobado. "
            "Puedes aceptar o rechazar aquí:\n"
            f"{link}\n\n"
            f"El enlace vence en {settings.link_token_ttl_days} días. "
            "Si no conoces a esta persona, ignora este mensaje."
        ),
        idempotency_key=f"contact_consent:{contact.id}",
    )


def deliver_consent(payload: dict) -> dict:
    """Trabajo del worker: envía la solicitud de aceptación."""
    return run_delivery(
        Contact,
        uuid.UUID(payload["contact_id"]),
        _prepare_consent_email,
        status_attr="consent_delivery",
    )
