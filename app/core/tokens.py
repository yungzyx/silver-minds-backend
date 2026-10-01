"""Tokens de enlace: el valor viaja solo en el correo; la base guarda su hash."""

import hashlib
import secrets


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> tuple[str, str]:
    """Devuelve ``(token, hash)``. 256 bits aleatorios: no se pueden adivinar."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)
