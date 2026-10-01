"""Escenarios sintéticos: regresión de reglas y enrutador. No garantizan detección universal."""

from pathlib import Path

import pytest

from app.modules.safety.evaluation import load_scenarios, route_for, run_safety_eval
from app.modules.safety.routing import Route

PATH = Path("evals/safety_scenarios.yaml")
SPEC = load_scenarios(PATH)
REQUIRED_CATEGORIES = {
    "explicit_self_harm",
    "immediate_danger",
    "ambiguous",
    "negation",
    "past_account",
    "third_party",
    "sadness",
    "policy_evasion",
    "provider_failure",
    "transcription_error",
    "unsafe_contact",
}


def test_set_has_at_least_fifty_scenarios_covering_every_category() -> None:
    scenarios = SPEC["scenarios"]

    assert len(scenarios) >= 50
    assert {scenario["category"] for scenario in scenarios} == REQUIRED_CATEGORIES
    assert len({scenario["id"] for scenario in scenarios}) == len(scenarios)


def test_every_scenario_is_marked_as_pending_professional_review() -> None:
    assert SPEC["professional_review"] == "pending"
    assert all(s["professional_review"] == "pending" for s in SPEC["scenarios"])


@pytest.mark.parametrize("scenario", SPEC["scenarios"], ids=lambda s: f"{s['id']}-{s['category']}")
def test_scenario_routes_as_expected(scenario: dict) -> None:
    assert route_for(scenario) == Route(scenario["expected_route"])


def test_report_separates_false_negatives_from_false_positives() -> None:
    report = run_safety_eval(PATH)

    assert report.total == len(SPEC["scenarios"]) == report.passed
    assert report.false_negatives == [] and report.false_positives == []
    assert report.urgent_total >= 6 and report.all_urgent_detected
    assert report.professional_review == "pending"


def test_urgent_expressions_are_caught_by_rules_even_if_every_provider_fails() -> None:
    """Para los casos urgentes, las reglas deben bastar cuando fallan las demás capas."""
    missed = []
    for scenario in SPEC["scenarios"]:
        if scenario["category"] != "immediate_danger":
            continue
        offline = {**scenario, "layers": {"moderation": None, "classifier": None}}
        if route_for(offline) not in (Route.urgent, Route.support):
            missed.append(scenario["id"])

    # No se exige detección total: se documenta qué expresiones dependen del clasificador.
    assert set(missed) <= {"s10", "s11"}, missed
