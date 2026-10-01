"""Acceso a datos de perfiles y preferencias. Toda consulta filtra por propietario."""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Preference, Profile


def get_or_create_profile(db: Session, user_id: uuid.UUID, email: str | None) -> Profile:
    # ON CONFLICT evita la carrera entre dos primeras peticiones simultáneas.
    db.execute(
        insert(Profile)
        .values(id=user_id, email=email)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    return db.execute(select(Profile).where(Profile.id == user_id)).scalar_one()


def list_preferences(db: Session, owner_id: uuid.UUID) -> list[Preference]:
    statement = (
        select(Preference).where(Preference.owner_id == owner_id).order_by(Preference.created_at)
    )
    return list(db.execute(statement).scalars())


def get_preference(db: Session, owner_id: uuid.UUID, preference_id: uuid.UUID) -> Preference | None:
    statement = select(Preference).where(
        Preference.id == preference_id, Preference.owner_id == owner_id
    )
    return db.execute(statement).scalar_one_or_none()
