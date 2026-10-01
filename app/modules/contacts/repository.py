"""Acceso a datos de contactos."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Contact

ACTIVE_STATUSES = ("pending", "accepted")


def list_contacts(db: Session, owner_id: uuid.UUID) -> list[Contact]:
    statement = select(Contact).where(Contact.owner_id == owner_id).order_by(Contact.created_at)
    return list(db.execute(statement).scalars())


def get_contact(
    db: Session, owner_id: uuid.UUID, contact_id: uuid.UUID, *, lock: bool = False
) -> Contact | None:
    statement = select(Contact).where(Contact.id == contact_id, Contact.owner_id == owner_id)
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def find_active_by_email(db: Session, owner_id: uuid.UUID, email: str) -> Contact | None:
    statement = select(Contact).where(
        Contact.owner_id == owner_id,
        func.lower(Contact.email) == email.lower(),
        Contact.status.in_(ACTIVE_STATUSES),
    )
    return db.execute(statement).scalar_one_or_none()


def get_by_consent_hash(db: Session, token_hash: str, *, lock: bool = False) -> Contact | None:
    statement = select(Contact).where(Contact.consent_token_hash == token_hash)
    if lock:
        statement = statement.with_for_update()
    return db.execute(statement).scalar_one_or_none()


def list_support_contacts(db: Session, owner_id: uuid.UUID) -> list[Contact]:
    """Contactos que aceptaron participar y que la persona marcó para pedir apoyo."""
    statement = (
        select(Contact)
        .where(
            Contact.owner_id == owner_id,
            Contact.status == "accepted",
            Contact.support_opt_in.is_(True),
        )
        .order_by(Contact.created_at)
    )
    return list(db.execute(statement).scalars())


def list_accepted(db: Session, owner_id: uuid.UUID) -> list[Contact]:
    statement = (
        select(Contact)
        .where(Contact.owner_id == owner_id, Contact.status == "accepted")
        .order_by(Contact.created_at)
    )
    return list(db.execute(statement).scalars())
