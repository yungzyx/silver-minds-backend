"""Rutas de apoyo: recursos, estado, reanudación y solicitudes a un contacto elegido."""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentProfile, DbSession
from app.core.config import get_settings
from app.modules.safety import service, support_requests
from app.modules.safety.policy import get_resources
from app.modules.safety.schemas import (
    ResourceList,
    ResumeIn,
    SupportRequestEdit,
    SupportRequestIn,
    SupportRequestList,
    SupportRequestOut,
    SupportState,
)

router = APIRouter(tags=["apoyo"])


@router.get(
    "/support-resources",
    response_model=ResourceList,
    summary="Recursos de ayuda verificados (público)",
    description=(
        "Sale de configuración versionada, no del RAG, y no requiere autenticación: "
        "la ayuda no depende de ningún otro componente."
    ),
)
def support_resources(
    country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
) -> ResourceList:
    catalog = get_resources()
    selected = (country or get_settings().default_country).upper()
    return ResourceList(
        country=selected,
        version=catalog.version,
        professional_review=catalog.professional_review,
        items=service.resource_list(selected),
    )


@router.get("/support/state", response_model=SupportState, summary="Estado de las acciones")
def support_state(db: DbSession, profile: CurrentProfile) -> SupportState:
    return SupportState(
        mode=service.current_mode(db, profile.id).value,
        can_resume=service.can_resume(db, profile.id),
    )


@router.post(
    "/support/resume",
    response_model=SupportState,
    summary="Retomar la conversación habitual",
    description=(
        "Requiere que el último mensaje haya sido evaluado como `normal` y la decisión "
        "explícita de la persona (`confirm: true`). No declara que un riesgo desapareció."
    ),
)
def resume(_: ResumeIn, db: DbSession, profile: CurrentProfile) -> SupportState:
    mode = service.resume(db, profile.id)
    return SupportState(mode=mode.value, can_resume=False)


@router.get("/support-requests", response_model=SupportRequestList, summary="Listar solicitudes")
def list_requests(db: DbSession, profile: CurrentProfile) -> SupportRequestList:
    items = [
        SupportRequestOut.model_validate(r) for r in support_requests.list_requests(db, profile.id)
    ]
    return SupportRequestList(items=items, total=len(items))


@router.post(
    "/support-requests",
    response_model=SupportRequestOut,
    status_code=status.HTTP_201_CREATED,
    summary="Redactar un mensaje a un contacto elegido",
    description="Crea un borrador. Nada se envía hasta que la persona lo aprueba.",
)
def create_request(
    data: SupportRequestIn, db: DbSession, profile: CurrentProfile
) -> SupportRequestOut:
    request = support_requests.create_request(db, profile.id, data)
    return SupportRequestOut.model_validate(request)


@router.patch(
    "/support-requests/{request_id}", response_model=SupportRequestOut, summary="Editar el borrador"
)
def edit_request(
    request_id: uuid.UUID, changes: SupportRequestEdit, db: DbSession, profile: CurrentProfile
) -> SupportRequestOut:
    request = support_requests.edit_request(db, profile.id, request_id, changes)
    return SupportRequestOut.model_validate(request)


@router.post(
    "/support-requests/{request_id}/approve",
    response_model=SupportRequestOut,
    summary="Aprobar y enviar",
    description="El correo es complementario: no es atención inmediata.",
)
def approve_request(
    request_id: uuid.UUID, db: DbSession, profile: CurrentProfile
) -> SupportRequestOut:
    request = support_requests.approve_request(db, profile.id, request_id)
    return SupportRequestOut.model_validate(request)


@router.post(
    "/support-requests/{request_id}/cancel",
    response_model=SupportRequestOut,
    summary="Cancelar el borrador",
)
def cancel_request(
    request_id: uuid.UUID, db: DbSession, profile: CurrentProfile
) -> SupportRequestOut:
    request = support_requests.cancel_request(db, profile.id, request_id)
    return SupportRequestOut.model_validate(request)
