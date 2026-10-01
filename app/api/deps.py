"""Dependencias compartidas por las rutas."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.auth import AuthUser, get_auth_user
from app.core.db import get_db
from app.models import Profile
from app.modules.profiles import repository

# scope="function": la transacción se confirma antes de enviar la respuesta.
DbSession = Annotated[Session, Depends(get_db, scope="function")]


def get_current_profile(
    db: DbSession, user: Annotated[AuthUser, Depends(get_auth_user)]
) -> Profile:
    """Identidad de la petición. El perfil se crea en el primer acceso autenticado."""
    return repository.get_or_create_profile(db, user.id, user.email)


CurrentProfile = Annotated[Profile, Depends(get_current_profile)]
