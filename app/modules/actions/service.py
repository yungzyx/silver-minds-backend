"""Acciones aprobadas: propuestas, invitaciones, recordatorios y feedback.

Reglas: toda propuesta empieza como borrador; la persona aprueba contenido,
destinatario y fecha; editar una propuesta aprobada exige aprobarla de nuevo; el worker
vuelve a comprobar permisos y estado de seguridad antes de enviar.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import (
    ActionsPausedError,
    ConflictError,
    InvalidInputError,
    NotFoundError,
    TokenExpiredError,
)
from app.core.tokens import hash_token, new_token
from app.integrations.email.base import EmailMessage
from app.models import (
    Activity,
    ActivityFeedback,
    Contact,
    Invitation,
    Profile,
    Proposal,
    Reminder,
)
from app.modules.actions import proposals as proposal_repo
from app.modules.actions.schemas import FeedbackIn, InvitationView, ProposalEdit
from app.modules.followup import audit
from app.modules.safety import service as safety
from app.worker import queue
from app.worker.delivery import run_delivery
from app.worker.queue import DeferError

REMINDER_LEAD = timedelta(hours=24)
FOLLOWUP_DELAY = timedelta(hours=24)
FOLLOWUP_WITHOUT_DATE = timedelta(days=3)
DEFAULT_INVITER_NAME = "Una persona que usa Silver Minds"
RESPONSE_STATUS = {"accept": "accepted", "decline": "declined"}


def _owned(db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID) -> Proposal:
    proposal = proposal_repo.get_proposal(db, owner_id, proposal_id, lock=True)
    if proposal is None:
        raise NotFoundError("La propuesta no existe.")
    return proposal


def _accepted_contact(db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = db.execute(
        select(Contact).where(Contact.id == contact_id, Contact.owner_id == owner_id)
    ).scalar_one_or_none()
    if contact is None:
        raise NotFoundError("El contacto no existe.")
    if contact.status != "accepted":
        raise InvalidInputError("Ese contacto todavía no aceptó recibir invitaciones.")
    return contact


def _cancel_pending_sends(db: Session, proposal_id: uuid.UUID) -> None:
    """Cancela lo que aún no salió. Lo ya enviado no puede retirarse."""
    db.execute(
        update(Invitation)
        .where(Invitation.proposal_id == proposal_id, Invitation.status == "queued")
        .values(status="cancelled")
    )
    db.execute(
        update(Reminder)
        .where(Reminder.proposal_id == proposal_id, Reminder.status.in_(("queued", "pending")))
        .values(status="cancelled")
    )


def edit_proposal(
    db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID, changes: ProposalEdit
) -> Proposal:
    proposal = _owned(db, owner_id, proposal_id)
    if proposal.status not in ("draft", "approved"):
        raise ConflictError("La propuesta ya no se puede editar.")
    values = changes.model_dump(exclude_unset=True)
    if values.get("contact_id") is not None:
        _accepted_contact(db, owner_id, values["contact_id"])
    for field, value in values.items():
        setattr(proposal, field, value)
    proposal.version += 1
    if proposal.status == "approved":
        # Lo aprobado era otra versión: vuelve a borrador y exige una nueva aprobación.
        proposal.status = "draft"
        proposal.approved_version = None
        proposal.approved_at = None
        _cancel_pending_sends(db, proposal.id)
    db.flush()
    return proposal


def approve_proposal(
    db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID, version: int
) -> Proposal:
    proposal = _owned(db, owner_id, proposal_id)
    if proposal.status != "draft":
        raise ConflictError("Solo se puede aprobar un borrador.")
    if proposal.version != version:
        raise ConflictError(
            "La propuesta cambió desde que la revisaste. Revísala de nuevo.",
            code="version_mismatch",
        )
    if safety.is_paused(db, owner_id):
        raise ActionsPausedError("Las acciones están en pausa por ahora.")
    contact = _accepted_contact(db, owner_id, proposal.contact_id) if proposal.contact_id else None

    now = utcnow()
    proposal.status = "approved"
    proposal.approved_version = proposal.version
    proposal.approved_at = now
    if contact is not None:
        _queue_invitation(db, proposal, contact)
    _schedule_followups(db, proposal, now)
    db.flush()
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="proposal.approved",
        entity_type="proposal",
        entity_id=proposal.id,
    )
    return proposal


def _queue_invitation(db: Session, proposal: Proposal, contact: Contact) -> None:
    key = f"invitation:{proposal.id}:{proposal.version}"
    invitation = Invitation(
        owner_id=proposal.owner_id,
        proposal_id=proposal.id,
        proposal_version=proposal.version,
        contact_id=contact.id,
        status="queued",
        idempotency_key=key,
    )
    db.add(invitation)
    db.flush()
    queue.enqueue(
        db,
        "send_invitation",
        {"invitation_id": str(invitation.id)},
        owner_id=proposal.owner_id,
        idempotency_key=key,
    )


def _schedule_followups(db: Session, proposal: Proposal, now: datetime) -> None:
    when = proposal.scheduled_for
    if when is not None and when - REMINDER_LEAD > now:
        key = f"reminder:{proposal.id}:{proposal.version}"
        reminder = Reminder(
            owner_id=proposal.owner_id,
            proposal_id=proposal.id,
            proposal_version=proposal.version,
            kind="reminder",
            due_at=when - REMINDER_LEAD,
            status="queued",
            idempotency_key=key,
        )
        db.add(reminder)
        db.flush()
        queue.enqueue(
            db,
            "send_reminder",
            {"reminder_id": str(reminder.id)},
            owner_id=proposal.owner_id,
            run_at=reminder.due_at,
            idempotency_key=key,
        )
    db.add(
        Reminder(
            owner_id=proposal.owner_id,
            proposal_id=proposal.id,
            proposal_version=proposal.version,
            kind="followup",
            due_at=(when + FOLLOWUP_DELAY) if when else now + FOLLOWUP_WITHOUT_DATE,
            status="pending",
            idempotency_key=f"followup:{proposal.id}:{proposal.version}",
        )
    )


def reject_proposal(db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID) -> Proposal:
    proposal = _owned(db, owner_id, proposal_id)
    if proposal.status != "draft":
        raise ConflictError("Solo se puede rechazar un borrador.")
    proposal.status = "rejected"
    db.flush()
    return proposal


def cancel_proposal(db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID) -> Proposal:
    proposal = _owned(db, owner_id, proposal_id)
    if proposal.status != "approved":
        raise ConflictError("Solo se puede cancelar una propuesta aprobada.")
    proposal.status = "cancelled"
    _cancel_pending_sends(db, proposal.id)
    db.flush()
    return proposal


def add_feedback(
    db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID, data: FeedbackIn
) -> ActivityFeedback:
    proposal = _owned(db, owner_id, proposal_id)
    if proposal.status not in ("approved", "completed"):
        raise ConflictError("Solo se puede comentar una propuesta aprobada.")
    feedback = ActivityFeedback(owner_id=owner_id, proposal_id=proposal.id, **data.model_dump())
    db.add(feedback)
    proposal.status = "completed"
    db.execute(
        update(Reminder)
        .where(Reminder.proposal_id == proposal.id, Reminder.kind == "followup")
        .values(status="done")
    )
    db.flush()
    return feedback


def list_invitations(db: Session, owner_id: uuid.UUID) -> list[Invitation]:
    statement = (
        select(Invitation)
        .where(Invitation.owner_id == owner_id)
        .order_by(Invitation.created_at.desc())
    )
    return list(db.execute(statement).scalars())


def list_reminders(db: Session, owner_id: uuid.UUID) -> list[Reminder]:
    statement = select(Reminder).where(Reminder.owner_id == owner_id).order_by(Reminder.due_at)
    return list(db.execute(statement).scalars())


def _current_activities(territory: str, now: datetime):
    today = now.date()
    return and_(
        Activity.approval_status == "approved",
        Activity.valid_from <= today,
        or_(Activity.valid_until.is_(None), Activity.valid_until >= today),
        Activity.territory == territory,
    )


def list_activities(db: Session, territory: str) -> list[Activity]:
    statement = (
        select(Activity).where(_current_activities(territory, utcnow())).order_by(Activity.title)
    )
    return list(db.execute(statement).scalars())


def get_activity(db: Session, territory: str, activity_id: uuid.UUID) -> Activity:
    activity = db.execute(
        select(Activity).where(Activity.id == activity_id, _current_activities(territory, utcnow()))
    ).scalar_one_or_none()
    if activity is None:
        raise NotFoundError("La actividad no existe o ya no está vigente.")
    return activity


# --- Respuesta del contacto ---------------------------------------------------------


def _invitation_by_token(db: Session, token: str, *, lock: bool = False) -> Invitation:
    statement = select(Invitation).where(Invitation.token_hash == hash_token(token))
    if lock:
        statement = statement.with_for_update()
    invitation = db.execute(statement).scalar_one_or_none()
    if invitation is None:
        raise NotFoundError("El enlace no es válido.")
    return invitation


def _expired(invitation: Invitation, now: datetime) -> bool:
    return invitation.token_expires_at is not None and invitation.token_expires_at < now


def invitation_view(db: Session, token: str, now: datetime | None = None) -> InvitationView:
    """Solo lectura: abrir el enlace no modifica estado."""
    invitation = _invitation_by_token(db, token)
    proposal = db.get(Proposal, invitation.proposal_id)
    profile = db.get(Profile, invitation.owner_id)
    return InvitationView(
        inviter_name=(profile.preferred_name if profile else None) or DEFAULT_INVITER_NAME,
        title=proposal.title,
        body=proposal.body,
        scheduled_for=proposal.scheduled_for,
        status=invitation.status,
        expired=_expired(invitation, now or utcnow()),
    )


def respond_invitation(
    db: Session, token: str, response: str, now: datetime | None = None
) -> Invitation:
    invitation = _invitation_by_token(db, token, lock=True)
    target = RESPONSE_STATUS[response]
    if invitation.status == target:
        return invitation
    if invitation.status != "sent":
        raise ConflictError("Esta invitación ya fue respondida o ya no está disponible.")
    now = now or utcnow()
    if _expired(invitation, now):
        raise TokenExpiredError("El enlace venció.")
    invitation.status = target
    invitation.responded_at = now
    db.flush()
    audit.record(
        db,
        owner_id=invitation.owner_id,
        actor="contact",
        action=f"invitation.{target}",
        entity_type="invitation",
        entity_id=invitation.id,
    )
    return invitation


# --- Trabajos del worker ------------------------------------------------------------


def _still_approved(db: Session, proposal_id: uuid.UUID, version: int) -> Proposal | None:
    proposal = db.get(Proposal, proposal_id)
    if proposal is None or proposal.status != "approved" or proposal.approved_version != version:
        return None
    return proposal


def _activity_details(db: Session, proposal: Proposal) -> str:
    """Los detalles de una actividad salen del registro vigente."""
    if proposal.activity_id is None:
        return ""
    activity = db.get(Activity, proposal.activity_id)
    if activity is None:
        return ""
    lines = [
        f"Actividad: {activity.title}",
        f"Cuándo: {activity.schedule_text}" if activity.schedule_text else "",
        f"Dónde: {activity.location}" if activity.location else "",
        f"Costo: {activity.cost}" if activity.cost else "",
    ]
    return "\n".join(line for line in lines if line) + "\n\n"


def _prepare_invitation(db: Session, invitation: Invitation) -> EmailMessage | None:
    """Permisos y seguridad se comprueban otra vez, justo antes de enviar."""
    proposal = _still_approved(db, invitation.proposal_id, invitation.proposal_version)
    contact = db.get(Contact, invitation.contact_id)
    if proposal is None or contact is None or contact.status != "accepted":
        invitation.status = "cancelled"
        return None
    if safety.is_paused(db, invitation.owner_id):
        raise DeferError("actions_paused")

    settings = get_settings()
    token, token_hash = new_token()
    invitation.token_hash = token_hash
    invitation.token_expires_at = utcnow() + timedelta(days=settings.link_token_ttl_days)
    profile = db.get(Profile, invitation.owner_id)
    inviter = (profile.preferred_name if profile else None) or DEFAULT_INVITER_NAME
    when = (
        f"Fecha propuesta: {proposal.scheduled_for:%d-%m-%Y %H:%M}\n\n"
        if proposal.scheduled_for
        else ""
    )
    link = f"{settings.public_link_base_url}/invitation-responses/{token}"
    return EmailMessage(
        to=contact.email,
        subject=f"{inviter} te invita: {proposal.title}",
        text=(
            f"Hola, {contact.name}:\n\n"
            f"{proposal.body}\n\n"
            f"{_activity_details(db, proposal)}"
            f"{when}"
            f"Puedes responder aquí:\n{link}\n\n"
            f"— {inviter}, a través de Silver Minds"
        ),
        idempotency_key=invitation.idempotency_key,
    )


def deliver_invitation(payload: dict) -> dict:
    return run_delivery(Invitation, uuid.UUID(payload["invitation_id"]), _prepare_invitation)


def _prepare_reminder(db: Session, reminder: Reminder) -> EmailMessage | None:
    proposal = _still_approved(db, reminder.proposal_id, reminder.proposal_version)
    profile = db.get(Profile, reminder.owner_id)
    if proposal is None or profile is None or not profile.email:
        reminder.status = "cancelled"
        return None
    if safety.is_paused(db, reminder.owner_id):
        raise DeferError("actions_paused")
    when = f" el {proposal.scheduled_for:%d-%m-%Y a las %H:%M}" if proposal.scheduled_for else ""
    return EmailMessage(
        to=profile.email,
        subject=f"Recordatorio: {proposal.title}",
        text=(
            f"Hola{', ' + profile.preferred_name if profile.preferred_name else ''}:\n\n"
            f"Te recordamos lo que aprobaste: {proposal.title}{when}.\n\n"
            f"{_activity_details(db, proposal)}"
            "Si cambiaste de planes, puedes cancelarlo cuando quieras."
        ),
        idempotency_key=reminder.idempotency_key,
    )


def deliver_reminder(payload: dict) -> dict:
    return run_delivery(Reminder, uuid.UUID(payload["reminder_id"]), _prepare_reminder)
