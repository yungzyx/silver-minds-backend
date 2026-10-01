"""Solicitudes de apoyo: un mensaje que la persona escribe, revisa y aprueba.

No hay avisos automáticos. Solo se comparte el texto aprobado: nunca la conversación,
el historial ni una clasificación interna. No se pausan por el estado de seguridad ni
por cuotas.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ConflictError, InvalidInputError, NotFoundError
from app.integrations.email.base import EmailMessage
from app.models import Contact, Profile, SupportRequest
from app.modules.followup import audit
from app.modules.safety.schemas import SupportRequestEdit, SupportRequestIn
from app.worker import queue
from app.worker.delivery import run_delivery

DEFAULT_SENDER_NAME = "Una persona que usa Silver Minds"


def list_requests(db: Session, owner_id: uuid.UUID) -> list[SupportRequest]:
    statement = (
        select(SupportRequest)
        .where(SupportRequest.owner_id == owner_id)
        .order_by(SupportRequest.created_at.desc())
    )
    return list(db.execute(statement).scalars())


def _eligible_contact(db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == owner_id)
    ).scalar_one_or_none()
    if contact is None:
        raise NotFoundError("El contacto no existe.")
    if contact.status != "accepted" or not contact.support_opt_in:
        raise InvalidInputError(
            "Ese contacto no aceptó participar o no lo marcaste para pedirle apoyo."
        )
    return contact


def create_request(db: Session, owner_id: uuid.UUID, data: SupportRequestIn) -> SupportRequest:
    contact = _eligible_contact(db, owner_id, data.contact_id)
    request_id = uuid.uuid4()
    request = SupportRequest(
        id=request_id,
        owner_id=owner_id,
        contact_id=contact.id,
        message=data.message.strip(),
        status="draft",
        idempotency_key=f"support_request:{request_id}",
    )
    db.add(request)
    db.flush()
    return request


def _owned_draft(db: Session, owner_id: uuid.UUID, request_id: uuid.UUID) -> SupportRequest:
    request = db.execute(
        select(SupportRequest)
        .where(SupportRequest.id == request_id, SupportRequest.owner_id == owner_id)
        .with_for_update()
    ).scalar_one_or_none()
    if request is None:
        raise NotFoundError("La solicitud no existe.")
    if request.status != "draft":
        raise ConflictError("La solicitud ya no es un borrador.")
    return request


def edit_request(
    db: Session, owner_id: uuid.UUID, request_id: uuid.UUID, changes: SupportRequestEdit
) -> SupportRequest:
    request = _owned_draft(db, owner_id, request_id)
    request.message = changes.message.strip()
    db.flush()
    return request


def approve_request(db: Session, owner_id: uuid.UUID, request_id: uuid.UUID) -> SupportRequest:
    request = _owned_draft(db, owner_id, request_id)
    _eligible_contact(db, owner_id, request.contact_id)
    request.status = "queued"
    request.approved_at = utcnow()
    db.flush()
    queue.enqueue(
        db,
        "send_support_request",
        {"support_request_id": str(request.id)},
        owner_id=owner_id,
        idempotency_key=request.idempotency_key,
    )
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="support_request.approved",
        entity_type="support_request",
        entity_id=request.id,
    )
    return request


def cancel_request(db: Session, owner_id: uuid.UUID, request_id: uuid.UUID) -> SupportRequest:
    request = _owned_draft(db, owner_id, request_id)
    request.status = "cancelled"
    db.flush()
    return request


def _prepare_email(db: Session, request: SupportRequest) -> EmailMessage | None:
    """Vuelve a comprobar el permiso justo antes de enviar."""
    contact = db.get(Contact, request.contact_id)
    if contact is None or contact.status != "accepted" or not contact.support_opt_in:
        request.status = "cancelled"
        return None
    profile = db.get(Profile, request.owner_id)
    sender = (profile.preferred_name if profile else None) or DEFAULT_SENDER_NAME
    return EmailMessage(
        to=contact.email,
        subject=f"{sender} te escribió por Silver Minds",
        text=(
            f"{request.message}\n\n"
            f"— {sender}\n\n"
            "Este mensaje fue escrito y aprobado por quien lo envía, desde Silver Minds."
        ),
        idempotency_key=request.idempotency_key,
    )


def deliver_request(payload: dict) -> dict:
    """Trabajo del worker: envía la solicitud aprobada."""
    return run_delivery(SupportRequest, uuid.UUID(payload["support_request_id"]), _prepare_email)
