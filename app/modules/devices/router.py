"""Rutas REST del dispositivo y del panel familiar."""

import uuid

import anyio.from_thread
from fastapi import APIRouter, status

from app.api.deps import AccountProfile, CurrentDevice, CurrentViewer, DbSession
from app.modules.devices import metrics, service
from app.modules.devices.hub import hub
from app.modules.devices.schemas import (
    DeviceCreated,
    DeviceEventIn,
    DeviceIn,
    DeviceList,
    DeviceOut,
    DeviceSession,
    FamilyAccessEdit,
    FamilyAccessIn,
    FamilyAccessList,
    FamilyAccessOut,
    FamilyOverview,
)

router = APIRouter()
DEVICES = ["dispositivo"]
FAMILY = ["panel familiar"]


# --- Administración: solo con la cuenta de la persona mayor -------------------------


@router.post(
    "/devices",
    response_model=DeviceCreated,
    status_code=status.HTTP_201_CREATED,
    tags=DEVICES,
    summary="Registrar un dispositivo",
    description="Devuelve el token una sola vez. Requiere la cuenta, no un token de dispositivo.",
)
def create_device(data: DeviceIn, db: DbSession, profile: AccountProfile) -> DeviceCreated:
    device, token = service.create_device(db, profile.id, data.name)
    return DeviceCreated(**DeviceOut.model_validate(device).model_dump(), token=token)


@router.get("/devices", response_model=DeviceList, tags=DEVICES, summary="Listar dispositivos")
def list_devices(db: DbSession, profile: AccountProfile) -> DeviceList:
    items = [DeviceOut.model_validate(d) for d in service.list_devices(db, profile.id)]
    return DeviceList(items=items, total=len(items))


@router.post(
    "/devices/{device_id}/revoke", response_model=DeviceOut, tags=DEVICES, summary="Revocar"
)
def revoke_device(device_id: uuid.UUID, db: DbSession, profile: AccountProfile) -> DeviceOut:
    device = service.revoke_device(db, profile.id, device_id)
    db.commit()  # confirmado antes de cortar: una reconexión ya no encuentra el permiso
    anyio.from_thread.run(hub.kick_device, profile.id)
    return DeviceOut.model_validate(device)


@router.post(
    "/family-access",
    response_model=FamilyAccessOut,
    status_code=status.HTTP_201_CREATED,
    tags=FAMILY,
    summary="Dar acceso al panel a un contacto",
    description=(
        "La persona mayor decide quién ve el panel y si incluye la cámara. "
        "El contacto debe haber aceptado participar; recibe un enlace personal por correo."
    ),
)
def grant_access(data: FamilyAccessIn, db: DbSession, profile: AccountProfile) -> FamilyAccessOut:
    return FamilyAccessOut.model_validate(service.grant_access(db, profile.id, data))


@router.get(
    "/family-access", response_model=FamilyAccessList, tags=FAMILY, summary="Listar accesos"
)
def list_access(db: DbSession, profile: AccountProfile) -> FamilyAccessList:
    items = [FamilyAccessOut.model_validate(a) for a in service.list_access(db, profile.id)]
    return FamilyAccessList(items=items, total=len(items))


@router.patch(
    "/family-access/{access_id}",
    response_model=FamilyAccessOut,
    tags=FAMILY,
    summary="Cambiar el permiso de cámara",
)
def edit_access(
    access_id: uuid.UUID, data: FamilyAccessEdit, db: DbSession, profile: AccountProfile
) -> FamilyAccessOut:
    access = service.edit_access(db, profile.id, access_id, data.can_view_camera)
    db.commit()
    if not access.can_view_camera:
        anyio.from_thread.run(hub.kick_access, access.id)
    return FamilyAccessOut.model_validate(access)


@router.post(
    "/family-access/{access_id}/revoke",
    response_model=FamilyAccessOut,
    tags=FAMILY,
    summary="Quitar el acceso",
    description="Corta de inmediato el panel y la transmisión en curso.",
)
def revoke_access(access_id: uuid.UUID, db: DbSession, profile: AccountProfile) -> FamilyAccessOut:
    access = service.revoke_access(db, profile.id, access_id)
    db.commit()
    anyio.from_thread.run(hub.kick_access, access.id)
    return FamilyAccessOut.model_validate(access)


# --- Dispositivo --------------------------------------------------------------------


@router.get(
    "/device/session",
    response_model=DeviceSession,
    tags=DEVICES,
    summary="Estado para la pantalla del dispositivo",
    description="También registra que el dispositivo sigue encendido.",
)
def device_session(db: DbSession, device: CurrentDevice) -> DeviceSession:
    return service.session_info(db, device, hub.viewer_names(device.owner_id))


@router.post(
    "/device/events",
    status_code=status.HTTP_202_ACCEPTED,
    tags=DEVICES,
    summary="Registrar una señal de actividad",
    description=(
        "Llamadas al asistente y presencia frente al dispositivo. Son números y tipos: "
        "no se envía imagen, audio ni texto. La presencia solo se acepta con la cámara encendida."
    ),
)
def device_event(data: DeviceEventIn, db: DbSession, device: CurrentDevice) -> dict[str, bool]:
    return {"recorded": service.record_event(db, device, data)}


# --- Panel familiar -----------------------------------------------------------------


@router.get(
    "/family/overview",
    response_model=FamilyOverview,
    tags=FAMILY,
    summary="Señales de actividad para la familia",
    description=(
        "Conteos y marcas de tiempo. No incluye conversaciones, memorias ni el estado "
        "de seguridad de la persona."
    ),
)
def family_overview(db: DbSession, access: CurrentViewer) -> FamilyOverview:
    return metrics.family_overview(db, access)
