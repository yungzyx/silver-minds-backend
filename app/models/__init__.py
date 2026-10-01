"""Modelos SQLAlchemy. Importarlos aquí los registra en la metadata."""

from app.models.base import Base
from app.models.identity import AuditEvent, Preference, Profile

__all__ = ["AuditEvent", "Base", "Preference", "Profile"]
