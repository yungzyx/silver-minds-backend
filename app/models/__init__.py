"""Modelos SQLAlchemy. Importarlos aquí los registra en la metadata."""

from app.models.actions import (
    ActivityFeedback,
    AudioUpload,
    Invitation,
    Job,
    Proposal,
    Reminder,
    SafetyEvent,
    SafetyState,
    SupportRequest,
)
from app.models.base import Base
from app.models.devices import Device, DeviceEvent, FamilyAccess
from app.models.identity import AuditEvent, Preference, Profile
from app.models.knowledge import (
    Activity,
    IndexState,
    KnowledgeChunk,
    KnowledgeDocument,
    RetrievalRun,
)
from app.models.memory import (
    ConfirmedMemory,
    Contact,
    Conversation,
    MemoryCandidate,
    MemoryEmbedding,
    Message,
)

__all__ = [
    "Activity",
    "ActivityFeedback",
    "AudioUpload",
    "AuditEvent",
    "Base",
    "ConfirmedMemory",
    "Contact",
    "Conversation",
    "Device",
    "DeviceEvent",
    "FamilyAccess",
    "IndexState",
    "Invitation",
    "Job",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "MemoryCandidate",
    "MemoryEmbedding",
    "Message",
    "Preference",
    "Profile",
    "Proposal",
    "Reminder",
    "RetrievalRun",
    "SafetyEvent",
    "SafetyState",
    "SupportRequest",
]
