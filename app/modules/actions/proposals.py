"""Propuestas: todo empieza como borrador y nada se envía sin aprobación."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Proposal
from app.modules.safety.output_validation import ValidatedProposal


def create_drafts(
    db: Session,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    validated: list[ValidatedProposal],
) -> list[Proposal]:
    """Guarda como borradores las propuestas que pasaron la validación de salida."""
    proposals = [
        Proposal(
            owner_id=owner_id,
            conversation_id=conversation_id,
            kind=item.draft.kind,
            title=item.title,
            body=item.draft.body,
            activity_id=item.activity_id,
            is_generic=item.is_generic,
            contact_id=item.contact_id,
            status="draft",
            version=1,
        )
        for item in validated
    ]
    db.add_all(proposals)
    db.flush()
    return proposals


def list_proposals(db: Session, owner_id: uuid.UUID, status: str | None = None) -> list[Proposal]:
    statement = select(Proposal).where(Proposal.owner_id == owner_id)
    if status:
        statement = statement.where(Proposal.status == status)
    return list(db.execute(statement.order_by(Proposal.created_at.desc())).scalars())


def get_proposal(
    db: Session, owner_id: uuid.UUID, proposal_id: uuid.UUID, *, lock: bool = False
) -> Proposal | None:
    statement = select(Proposal).where(Proposal.id == proposal_id, Proposal.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()
