"""Capa 4: enrutador determinista.

Combina las señales de las otras capas con la tabla de docs/safety-protocol.md.
Es código puro: mismas señales, misma ruta. Las rutas no son diagnósticos.
"""

from enum import StrEnum

from app.integrations.ai.base import ModerationResult, SafetyClassification
from app.modules.safety.rules import RuleSignals


class Route(StrEnum):
    normal = "normal"
    clarify = "clarify"
    support = "support"
    urgent = "urgent"
    unavailable = "unavailable"


# Orden de protección: decide qué modo prevalece entre el mensaje y el estado guardado.
SEVERITY = {
    Route.normal: 0,
    Route.unavailable: 1,
    Route.clarify: 2,
    Route.support: 3,
    Route.urgent: 4,
}
PAUSING_ROUTES = (Route.clarify, Route.support, Route.urgent)


def most_protective(first: Route, second: Route) -> Route:
    return first if SEVERITY[first] >= SEVERITY[second] else second


def decide(
    rules: RuleSignals,
    moderation: ModerationResult | None,
    classification: SafetyClassification | None,
) -> Route:
    """``None`` en moderación o clasificación significa que esa capa falló."""
    if moderation is None or classification is None:
        if rules.urgent:
            return Route.urgent
        if rules.explicit:
            return Route.support
        return Route.unavailable

    about_self_now = (
        classification.subject == "self"
        and classification.timeframe == "present"
        and not classification.negated
    )
    if about_self_now and classification.intent == "imminent":
        return Route.urgent
    if about_self_now and classification.intent == "ideation":
        return Route.support
    if classification.intent == "unclear":
        return Route.clarify
    # Las capas discrepan y el clasificador no explica por qué: se pregunta.
    if about_self_now and (rules.any_explicit or moderation.self_harm_intent):
        return Route.clarify
    # El clasificador da contexto (tercero, cita, pasado, negación) pero la moderación
    # marca intención: también se pregunta.
    if moderation.self_harm_intent:
        return Route.clarify
    return Route.normal


def contributing_layers(
    rules: RuleSignals,
    moderation: ModerationResult | None,
    classification: SafetyClassification | None,
) -> list[str]:
    """Qué capas aportaron señal. Se guarda en el evento; no incluye texto ni puntuaciones."""
    layers = []
    if rules.any_explicit or rules.ambiguous:
        layers.append("rules")
    if rules.bypass or (classification is not None and classification.bypass_attempt):
        layers.append("bypass_attempt")
    if moderation is None:
        layers.append("moderation_failed")
    elif moderation.self_harm or moderation.self_harm_intent:
        layers.append("moderation")
    if classification is None:
        layers.append("classifier_failed")
    elif classification.intent != "none":
        layers.append("classifier")
    return layers
