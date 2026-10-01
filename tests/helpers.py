"""Utilidades de prueba: tokens firmados como los emitiría Supabase Auth."""

import os
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
