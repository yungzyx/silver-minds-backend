"""Seguridad conversacional: evaluación por capas, estado por persona y reanudación.

La evaluación ocurre antes de cualquier recuperación o generación. Mientras el estado
no sea ``normal`` no hay propuestas, memorias candidatas ni envíos rutinarios.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.errors import ConflictError
from app.integrations import registry
from app.integrations.ai.base import AIError, ModerationResult, SafetyClassification, Turn
from app.models import Profile, SafetyEvent, SafetyState
from app.modules.contacts import repository as contacts
from app.modules.followup import audit
from app.modules.safety import rules as rule_layer
from app.modules.safety.policy import SupportResource, get_policy, get_resources
from app.modules.safety.routing import (
    PAUSING_ROUTES,
    SEVERITY,
    Route,
    contributing_layers,
    decide,
    most_protective,
)
from app.modules.safety.schemas import ResourceOut, SupportContact, SupportOptions

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SafetyDecision:
    route: Route  # ruta de este mensaje
    mode: Route  # modo efectivo: lo más protector entre el mensaje y el estado guardado
    reply: str | None  # respuesta fija del protocolo cuando el modo no es normal
    support_options: SupportOptions | None

    @property
    def is_normal(self) -> bool:
        return self.mode == Route.normal


def _moderate(message: str) -> ModerationResult | None:
    try:
        return registry.get_ai().moderate(message)
    except AIError:
        logger.warning("La moderación falló; se continúa con las demás capas")
        return None


def _classify(message: str, recent_turns: list[Turn]) -> SafetyClassification | None:
    try:
        return registry.get_ai().classify_safety(message=message, recent_turns=recent_turns)
    except AIError:
        logger.warning("El clasificador contextual falló; se continúa con las demás capas")
        return None


def _locked_state(db: Session, owner_id: uuid.UUID) -> SafetyState:
    db.execute(
        insert(SafetyState)
        .values(
            owner_id=owner_id,
            mode="normal",
            last_route="normal",
            policy_version=get_policy().version,
        )
        .on_conflict_do_nothing(index_elements=["owner_id"])
    )
    return db.execute(
        select(SafetyState).where(SafetyState.owner_id == owner_id).with_for_update()
    ).scalar_one()


def current_mode(db: Session, owner_id: uuid.UUID) -> Route:
    mode = db.execute(
        select(SafetyState.mode).where(SafetyState.owner_id == owner_id)
    ).scalar_one_or_none()
    return Route(mode) if mode else Route.normal


def is_paused(db: Session, owner_id: uuid.UUID) -> bool:
    """Indica si las acciones rutinarias de esta persona están en pausa."""
    return current_mode(db, owner_id) in PAUSING_ROUTES


def can_resume(db: Session, owner_id: uuid.UUID) -> bool:
    state = db.execute(
        select(SafetyState).where(SafetyState.owner_id == owner_id)
    ).scalar_one_or_none()
    return (
        state is not None and Route(state.mode) in PAUSING_ROUTES and state.last_route == "normal"
    )


def evaluate_turn(
    db: Session,
    profile: Profile,
    conversation_id: uuid.UUID | None,
    message: str,
    recent_turns: list[Turn],
) -> SafetyDecision:
    policy = get_policy()
    signals = rule_layer.evaluate(message, policy.rules)
    moderation = _moderate(message)
    classification = _classify(message, recent_turns)
    route = decide(signals, moderation, classification)
    bypass = signals.bypass or (classification is not None and classification.bypass_attempt)

    state = _locked_state(db, profile.id)
    previous = Route(state.mode)
    escalated = route in PAUSING_ROUTES and SEVERITY[route] > SEVERITY[previous]
    if escalated:
        state.mode = route.value
        state.since = utcnow()
        state.policy_version = policy.version
    # Un intento de saltarse las políticas nunca cuenta como reevaluación favorable.
    if not (route == Route.normal and bypass):
        state.last_route = route.value

    if route != Route.normal or bypass:
        db.add(
            SafetyEvent(
                owner_id=profile.id,
                conversation_id=conversation_id,
                route=route.value,
                layers=contributing_layers(signals, moderation, classification),
                classifier_version=getattr(registry.get_ai(), "classifier_version", "unknown"),
                policy_version=policy.version,
            )
        )
    db.flush()

    mode = most_protective(route, Route(state.mode))
    if mode == Route.normal:
        return SafetyDecision(route=route, mode=mode, reply=None, support_options=None)

    resumable = Route(state.mode) in PAUSING_ROUTES and state.last_route == "normal"
    options = support_options(db, profile, can_resume=resumable)
    reply = _render_reply(mode, profile.country, entered=escalated, resumable=resumable)
    return SafetyDecision(route=route, mode=mode, reply=reply, support_options=options)


def _render_reply(mode: Route, country: str, *, entered: bool, resumable: bool) -> str:
    policy = get_policy()
    templates = getattr(policy.templates, mode.value)
    if resumable and templates.resumable:
        template = templates.resumable
    elif entered or not templates.followup:
        template = templates.initial
    else:
        template = templates.followup
    resources = get_resources().for_country(country)
    return template.format(
        crisis=_phones(resources, "crisis_line", policy.no_resources_text),
        emergency=_phones(resources, "emergency", policy.no_resources_text),
    )


def _phones(resources: tuple[SupportResource, ...], kind: str, fallback: str) -> str:
    matching = [f"{r.phone} ({r.name})" for r in resources if r.kind == kind]
    return " · ".join(matching) if matching else fallback


def resource_list(country: str) -> list[ResourceOut]:
    catalog = get_resources()
    limit = timedelta(days=catalog.review_interval_days)
    today = utcnow().date()
    return [
        ResourceOut(
            **resource.model_dump(include=set(ResourceOut.model_fields) - {"needs_review"}),
            needs_review=today - resource.verified_at > limit,
        )
        for resource in catalog.for_country(country)
    ]


def support_options(db: Session, profile: Profile, *, can_resume: bool) -> SupportOptions:
    """Recursos del país y contactos que la persona eligió para apoyo.

    El agente no destaca ni recomienda a un contacto: quien elige es la persona.
    """
    chosen = contacts.list_support_contacts(db, profile.id)
    return SupportOptions(
        resources=resource_list(profile.country),
        contacts=[SupportContact(id=contact.id, name=contact.name) for contact in chosen],
        contact_note=get_policy().contact_note.strip(),
        can_resume=can_resume,
    )


def resume(db: Session, owner_id: uuid.UUID) -> Route:
    """Vuelve al flujo normal. Exige reevaluación favorable y decisión explícita.

    No afirma que un riesgo haya desaparecido: solo registra que la persona decidió
    retomar la conversación habitual tras una evaluación normal.
    """
    state = _locked_state(db, owner_id)
    if Route(state.mode) not in PAUSING_ROUTES:
        return Route.normal
    if state.last_route != "normal":
        raise ConflictError(
            "Todavía no es posible retomar. Sigue conversando y vuelve a intentarlo.",
            code="resume_not_available",
        )
    state.mode = "normal"
    state.since = utcnow()
    db.flush()
    audit.record(
        db, owner_id=owner_id, actor="user", action="safety.resumed", entity_type="safety_state"
    )
    return Route.normal
