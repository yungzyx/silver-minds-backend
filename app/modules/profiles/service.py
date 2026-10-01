"""Reglas de negocio de perfil y preferencias."""

import uuid

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models import Preference, Profile
from app.modules.followup import audit
from app.modules.profiles import repository
from app.modules.profiles.schemas import PreferenceIn, PreferenceUpdate, ProfileUpdate


def update_profile(db: Session, profile: Profile, changes: ProfileUpdate) -> Profile:
    for field, value in changes.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    audit.record(
        db, owner_id=profile.id, actor="user", action="profile.updated", entity_type="profile"
    )
    db.flush()
    return profile


def add_preference(db: Session, owner_id: uuid.UUID, data: PreferenceIn) -> Preference:
    preference = Preference(owner_id=owner_id, category=data.category.value, value=data.value)
    db.add(preference)
    db.flush()
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="preference.created",
        entity_type="preference",
        entity_id=preference.id,
    )
    return preference


def _owned_preference(db: Session, owner_id: uuid.UUID, preference_id: uuid.UUID) -> Preference:
    preference = repository.get_preference(db, owner_id, preference_id)
    if preference is None:
        raise NotFoundError("La preferencia no existe.")
    return preference


def update_preference(
    db: Session, owner_id: uuid.UUID, preference_id: uuid.UUID, changes: PreferenceUpdate
) -> Preference:
    preference = _owned_preference(db, owner_id, preference_id)
    for field, value in changes.model_dump(exclude_unset=True, mode="json").items():
        setattr(preference, field, value)
    db.flush()
    return preference


def delete_preference(db: Session, owner_id: uuid.UUID, preference_id: uuid.UUID) -> None:
    preference = _owned_preference(db, owner_id, preference_id)
    db.delete(preference)
    audit.record(
        db,
        owner_id=owner_id,
        actor="user",
        action="preference.deleted",
        entity_type="preference",
        entity_id=preference_id,
    )
