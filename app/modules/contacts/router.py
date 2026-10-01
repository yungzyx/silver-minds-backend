"""Rutas de contactos y de aceptación mediante enlace."""

import uuid

import anyio.from_thread
from fastapi import APIRouter, status

from app.api.deps import AccountProfile, CurrentProfile, DbSession
from app.modules.contacts import repository, service
from app.modules.contacts.schemas import (
    ConsentDecision,
    ConsentView,
    ContactIn,
    ContactList,
    ContactOut,
    ContactUpdate,
)
from app.modules.devices.hub import hub

router = APIRouter(tags=["contactos"])


@router.get("/contacts", response_model=ContactList, summary="Listar contactos")
def list_contacts(db: DbSession, profile: CurrentProfile) -> ContactList:
    items = [ContactOut.model_validate(c) for c in repository.list_contacts(db, profile.id)]
    return ContactList(items=items, total=len(items))


@router.post(
    "/contacts",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
    summary="Agregar un contacto",
    description="Queda `pending` y se le envía un correo para que acepte o rechace participar.",
)
def create_contact(data: ContactIn, db: DbSession, profile: AccountProfile) -> ContactOut:
    return ContactOut.model_validate(service.create_contact(db, profile.id, data))


@router.patch("/contacts/{contact_id}", response_model=ContactOut, summary="Editar un contacto")
def update_contact(
    contact_id: uuid.UUID, changes: ContactUpdate, db: DbSession, profile: AccountProfile
) -> ContactOut:
    return ContactOut.model_validate(service.update_contact(db, profile.id, contact_id, changes))


@router.post(
    "/contacts/{contact_id}/revoke",
    response_model=ContactOut,
    summary="Revocar un contacto",
    description="Cancela además los envíos pendientes hacia ese contacto.",
)
def revoke_contact(contact_id: uuid.UUID, db: DbSession, profile: AccountProfile) -> ContactOut:
    contact, access_ids = service.revoke_contact(db, profile.id, contact_id)
    db.commit()  # la revocación queda confirmada antes de cortar la transmisión
    for access_id in access_ids:
        anyio.from_thread.run(hub.kick_access, access_id)
    return ContactOut.model_validate(contact)


@router.get(
    "/contact-consents/{token}",
    response_model=ConsentView,
    summary="Ver la solicitud (público, solo lectura)",
    description="Abrir el enlace no modifica estado.",
)
def view_consent(token: str, db: DbSession) -> ConsentView:
    return service.consent_view(db, token)


@router.post(
    "/contact-consents/{token}",
    response_model=ConsentView,
    summary="Aceptar o rechazar (público)",
    description="Quien aceptó puede retirar su aceptación más tarde con `decline`.",
)
def decide_consent(token: str, body: ConsentDecision, db: DbSession) -> ConsentView:
    _, access_ids = service.decide_consent(db, token, body.decision)
    view = service.consent_view(db, token)
    db.commit()
    for access_id in access_ids:
        anyio.from_thread.run(hub.kick_access, access_id)
    return view
