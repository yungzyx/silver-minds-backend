"""Validación de salida: ninguna respuesta normal se muestra sin pasar por aquí.

No confía en que el modelo haya obedecido. Lo que el borrador diga sobre actividades,
contactos o fuentes solo sobrevive si coincide con el contexto autorizado de la persona.
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.text import normalize
from app.integrations import registry
from app.integrations.ai.base import (
    AgentDraft,
    AIError,
    ContactRef,
    ContextItem,
    MemoryDraft,
    ProposalDraft,
)
from app.modules.safety import rules as rule_layer
from app.modules.safety.policy import get_policy

logger = logging.getLogger(__name__)

MAX_REPLY_CHARS = 1200
MAX_PROPOSALS = 1
MAX_CANDIDATES = 2


@dataclass(frozen=True)
class ValidatedProposal:
    draft: ProposalDraft
    activity_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    title: str

    @property
    def is_generic(self) -> bool:
        return self.activity_id is None


@dataclass(frozen=True)
class ValidatedOutput:
    reply: str
    proposals: list[ValidatedProposal] = field(default_factory=list)
    memory_candidates: list[MemoryDraft] = field(default_factory=list)
    sources: list[ContextItem] = field(default_factory=list)
    replaced: bool = False


@lru_cache
def _forbidden() -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in get_policy().output_forbidden))


@lru_cache
def _memory_forbidden() -> re.Pattern[str]:
    patterns = (*get_policy().memory_forbidden, *get_policy().output_forbidden)
    return re.compile("|".join(f"(?:{p})" for p in patterns))


_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


def _proposal_is_acceptable(proposal: ProposalDraft) -> bool:
    """El texto de una propuesta termina en un correo: sin enlaces ni afirmaciones clínicas."""
    text = f"{proposal.title}\n{proposal.body}"
    return not _LINK.search(text) and _forbidden().search(normalize(text)) is None


def _reply_is_acceptable(reply: str, proposals: list[ProposalDraft]) -> bool:
    if not reply.strip() or _forbidden().search(normalize(reply)):
        return False
    # Se modera todo lo que la persona verá o podría enviar, no solo la respuesta.
    visible = "\n".join([reply, *(f"{p.title}\n{p.body}" for p in proposals)])
    try:
        return not registry.get_ai().moderate(visible).flagged
    except AIError:
        # Sin poder comprobar el texto, no se muestra.
        logger.warning("La moderación de salida falló; se usa el texto fijo")
        return False


def _is_safe_memory(candidate: MemoryDraft) -> bool:
    """Un mensaje de crisis o una conclusión clínica nunca se vuelve memoria."""
    signals = rule_layer.evaluate(candidate.content)
    if signals.any_explicit or signals.ambiguous:
        return False
    return _memory_forbidden().search(normalize(candidate.content)) is None


def validate(
    draft: AgentDraft, *, items: list[ContextItem], contacts: list[ContactRef]
) -> ValidatedOutput:
    if not _reply_is_acceptable(draft.reply, draft.proposals):
        return ValidatedOutput(reply=get_policy().output_fallback.strip(), replaced=True)

    by_id = {item.id: item for item in items}
    activities = {item.id: item for item in items if item.type == "activity"}
    contact_ids = {contact.id for contact in contacts}

    proposals = []
    for proposal in draft.proposals:
        if len(proposals) >= MAX_PROPOSALS:
            break
        if not _proposal_is_acceptable(proposal):
            continue
        activity = None
        if proposal.activity_id is not None:
            activity = activities.get(proposal.activity_id)
            if activity is None:
                # Cita una actividad que no está en el contexto vigente: se descarta entera.
                continue
        contact_id = proposal.contact_id if proposal.contact_id in contact_ids else None
        proposals.append(
            ValidatedProposal(
                draft=proposal,
                activity_id=uuid.UUID(activity.id) if activity else None,
                contact_id=uuid.UUID(contact_id) if contact_id else None,
                # El título de una actividad concreta sale del registro, no del modelo.
                title=activity.title if activity else proposal.title,
            )
        )

    candidates = [c for c in draft.memory_candidates if _is_safe_memory(c)][:MAX_CANDIDATES]

    used = list(dict.fromkeys(draft.used_source_ids))
    used += [
        str(p.activity_id) for p in proposals if p.activity_id and str(p.activity_id) not in used
    ]
    sources = [by_id[source_id] for source_id in used if source_id in by_id]

    return ValidatedOutput(
        reply=draft.reply.strip()[:MAX_REPLY_CHARS],
        proposals=proposals,
        memory_candidates=candidates,
        sources=sources,
    )
