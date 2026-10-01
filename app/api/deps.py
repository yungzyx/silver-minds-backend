"""Dependencias compartidas por las rutas.

Tres credenciales, cada una con su alcance:

- ``Bearer`` (JWT de Supabase): la cuenta de la persona mayor. Alcance completo.
- ``Device`` (token del dispositivo): actúa por la persona mayor en su casa. No puede
  administrar dispositivos ni accesos familiares.
- ``Viewer`` (token de acceso familiar): solo el panel familiar.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.auth import TokenVerifier, get_token_verifier
from app.core.clock import utcnow
from app.core.db import get_db
from app.core.errors import UnauthenticatedError
from app.models import Device, FamilyAccess, Profile
from app.modules.devices import service as devices
from app.modules.profiles import repository

# scope="function": la transacción se confirma antes de enviar la respuesta.
DbSession = Annotated[Session, Depends(get_db, scope="function")]


@dataclass(frozen=True)
class Credential:
    scheme: str
    token: str


def get_credential(request: Request) -> Credential:
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if not scheme or not token.strip():
        raise UnauthenticatedError("Falta el token de acceso.")
    return Credential(scheme=scheme.lower(), token=token.strip())


RequestCredential = Annotated[Credential, Depends(get_credential)]
Verifier = Annotated[TokenVerifier, Depends(get_token_verifier)]


def get_account_profile(
    db: DbSession, credential: RequestCredential, verifier: Verifier
) -> Profile:
    """Cuenta de la persona mayor. El perfil se crea en el primer acceso autenticado."""
    if credential.scheme != "bearer":
        raise UnauthenticatedError("Esta acción requiere iniciar sesión con la cuenta.")
    user = verifier.verify(credential.token)
    return repository.get_or_create_profile(db, user.id, user.email)


def get_current_device(db: DbSession, credential: RequestCredential) -> Device:
    if credential.scheme != "device":
        raise UnauthenticatedError("Esta acción requiere el token del dispositivo.")
    device = devices.device_by_token(db, credential.token)
    if device is None:
        raise UnauthenticatedError("El dispositivo no está autorizado.")
    devices.touch(db, device)
    return device


def get_current_profile(
    db: DbSession, credential: RequestCredential, verifier: Verifier
) -> Profile:
    """Identidad de la petición: la cuenta, o el dispositivo que actúa por ella."""
    if credential.scheme == "device":
        device = get_current_device(db, credential)
        return db.get(Profile, device.owner_id)
    return get_account_profile(db, credential, verifier)


def get_current_viewer(db: DbSession, credential: RequestCredential) -> FamilyAccess:
    if credential.scheme != "viewer":
        raise UnauthenticatedError("Esta acción requiere un acceso familiar.")
    access = devices.access_by_token(db, credential.token)
    if access is None:
        raise UnauthenticatedError("El acceso no es válido, venció o fue revocado.")
    access.last_used_at = utcnow()
    return access


CurrentProfile = Annotated[Profile, Depends(get_current_profile)]
AccountProfile = Annotated[Profile, Depends(get_account_profile)]
CurrentDevice = Annotated[Device, Depends(get_current_device)]
CurrentViewer = Annotated[FamilyAccess, Depends(get_current_viewer)]
