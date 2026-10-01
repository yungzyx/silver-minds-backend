"""Reloj de la aplicación. Centralizado para poder fijar el tiempo en pruebas."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
