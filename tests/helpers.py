"""Utilidades de prueba: tokens firmados como los emitiría Supabase Auth."""

import os
import re
import uuid
from datetime import UTC, datetime, timedelta

import jwt


def make_token(user_id: uuid.UUID | str | None = None, **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(user_id or uuid.uuid4()),
        "email": "persona@example.com",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, os.environ["SUPABASE_JWT_SECRET"], algorithm="HS256")


def auth_headers(user_id: uuid.UUID | str | None = None, **overrides: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, **overrides)}"}


API = "/api/v1"


def new_user(client, **profile: object) -> tuple[uuid.UUID, dict[str, str]]:
    """Crea una persona usuaria (perfil incluido) y devuelve su id y sus cabeceras."""
    user_id = uuid.uuid4()
    headers = auth_headers(user_id, email=f"{user_id.hex[:8]}@example.com")
    client.get(f"{API}/profile", headers=headers)
    if profile:
        client.patch(f"{API}/profile", headers=headers, json=profile)
    return user_id, headers


def link_token(email_text: str) -> str:
    """Extrae el token del enlace incluido en un correo."""
    match = re.search(r"/(?:contact-consents|invitation-responses)/([\w-]+)", email_text)
    assert match, "El correo no contiene un enlace con token"
    return match.group(1)
