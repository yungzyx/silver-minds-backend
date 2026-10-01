"""Evaluación de escenarios sintéticos de seguridad.

Dos modos:

- Simulado (CI): usa las señales de moderación y clasificador escritas en cada
  escenario y prueba las reglas reales y el enrutador determinista.
- En vivo: llama al proveedor configurado. Es una verificación externa separada.

Falsos negativos y falsos positivos se informan por separado. Ningún resultado de este
conjunto es una garantía de detección.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.integrations import registry
from app.integrations.ai.base import AIError, ModerationResult, SafetyClassification, Turn
from app.modules.safety import rules
from app.modules.safety.routing import SEVERITY, Route, decide

PROTECTIVE = (Route.clarify, Route.support, Route.urgent)


@dataclass
class SafetyEvalReport:
    total: int = 0
    passed: int = 0
    false_negatives: list[str] = field(default_factory=list)  # menos protector de lo esperado
    false_positives: list[str] = field(default_factory=list)  # más protector de lo esperado
    urgent_total: int = 0
    urgent_detected: int = 0
    professional_review: str = "pending"

    @property
    def all_urgent_detected(self) -> bool:
        return self.urgent_total == self.urgent_detected


def load_scenarios(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _simulated_layers(
    scenario: dict,
) -> tuple[ModerationResult | None, SafetyClassification | None]:
    moderation = scenario["layers"]["moderation"]
    classifier = scenario["layers"]["classifier"]
    return (
        None
        if moderation is None
        else ModerationResult(
            flagged=moderation["self_harm"],
            self_harm=moderation["self_harm"],
            self_harm_intent=moderation["self_harm_intent"],
            self_harm_instructions=False,
        ),
        None if classifier is None else SafetyClassification(**classifier),
    )


def _live_layers(scenario: dict) -> tuple[ModerationResult | None, SafetyClassification | None]:
    ai = registry.get_ai()
    turns = [Turn(role="user", content=text) for text in scenario["context"]]
    try:
        moderation = ai.moderate(scenario["message"])
    except AIError:
        moderation = None
    try:
        classification = ai.classify_safety(message=scenario["message"], recent_turns=turns)
    except AIError:
        classification = None
    return moderation, classification


def route_for(scenario: dict, *, live: bool = False) -> Route:
    moderation, classification = (_live_layers if live else _simulated_layers)(scenario)
    return decide(rules.evaluate(scenario["message"]), moderation, classification)


def run_safety_eval(path: Path, *, live: bool = False) -> SafetyEvalReport:
    spec = load_scenarios(path)
    report = SafetyEvalReport(professional_review=spec["professional_review"])
    for scenario in spec["scenarios"]:
        # En vivo no se pueden simular las caídas del proveedor.
        if live and scenario["category"] == "provider_failure":
            continue
        expected = Route(scenario["expected_route"])
        actual = route_for(scenario, live=live)
        report.total += 1
        if expected == Route.urgent:
            report.urgent_total += 1
            report.urgent_detected += actual == Route.urgent
        if actual == expected:
            report.passed += 1
        elif SEVERITY[actual] < SEVERITY[expected]:
            report.false_negatives.append(f"{scenario['id']}: {expected.value} → {actual.value}")
        else:
            report.false_positives.append(f"{scenario['id']}: {expected.value} → {actual.value}")
    return report
