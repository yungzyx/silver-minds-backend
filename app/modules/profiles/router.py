"""Rutas de perfil y preferencias."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentProfile, DbSession
from app.modules.profiles import repository, service
from app.modules.profiles.schemas import (
    PreferenceIn,
    PreferenceList,
    PreferenceOut,
    PreferenceUpdate,
    ProfileOut,
    ProfileUpdate,
)

router = APIRouter()


@router.get("/profile", response_model=ProfileOut, tags=["perfil"], summary="Perfil propio")
def get_profile(profile: CurrentProfile) -> ProfileOut:
    return ProfileOut.model_validate(profile)


@router.patch("/profile", response_model=ProfileOut, tags=["perfil"], summary="Editar perfil")
def patch_profile(changes: ProfileUpdate, db: DbSession, profile: CurrentProfile) -> ProfileOut:
    return ProfileOut.model_validate(service.update_profile(db, profile, changes))


@router.get("/preferences", response_model=PreferenceList, tags=["preferencias"], summary="Listar")
def list_preferences(db: DbSession, profile: CurrentProfile) -> PreferenceList:
    items = [PreferenceOut.model_validate(p) for p in repository.list_preferences(db, profile.id)]
    return PreferenceList(items=items, total=len(items))


@router.post(
    "/preferences",
    response_model=PreferenceOut,
    status_code=status.HTTP_201_CREATED,
    tags=["preferencias"],
    summary="Declarar una preferencia",
)
def create_preference(data: PreferenceIn, db: DbSession, profile: CurrentProfile) -> PreferenceOut:
    return PreferenceOut.model_validate(service.add_preference(db, profile.id, data))


@router.patch(
    "/preferences/{preference_id}",
    response_model=PreferenceOut,
    tags=["preferencias"],
    summary="Editar",
)
def patch_preference(
    preference_id: uuid.UUID, changes: PreferenceUpdate, db: DbSession, profile: CurrentProfile
) -> PreferenceOut:
    preference = service.update_preference(db, profile.id, preference_id, changes)
    return PreferenceOut.model_validate(preference)


@router.delete(
    "/preferences/{preference_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["preferencias"],
    summary="Borrar",
)
def delete_preference(preference_id: uuid.UUID, db: DbSession, profile: CurrentProfile) -> Response:
    service.delete_preference(db, profile.id, preference_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
