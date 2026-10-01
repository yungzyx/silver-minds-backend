"""Carga de la política y de los recursos de ayuda desde configuración versionada.

Nada de esto pasa por el RAG: está siempre disponible aunque falle la recuperación.
"""

import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"
POLICY_FILE = CONFIG_DIR / "safety_policy.v1.yaml"
RESOURCES_FILE = CONFIG_DIR / "support_resources.v1.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rules(_Strict):
    urgent: tuple[str, ...]
    explicit: tuple[str, ...]
    ambiguous: tuple[str, ...]
    bypass: tuple[str, ...]

    @field_validator("*")
    @classmethod
    def _patterns_compile(cls, patterns: tuple[str, ...]) -> tuple[str, ...]:
        for pattern in patterns:
            re.compile(pattern)
        return patterns


class RouteTemplates(_Strict):
    initial: str
    followup: str | None = None
    resumable: str | None = None


class Templates(_Strict):
    clarify: RouteTemplates
    support: RouteTemplates
    urgent: RouteTemplates
    unavailable: RouteTemplates


class SafetyPolicy(_Strict):
    version: str
    professional_review: Literal["pending", "approved"]
    rules: Rules
    templates: Templates
    no_resources_text: str
    contact_note: str
    output_fallback: str
    generation_fallback: str
    output_forbidden: tuple[str, ...]


class SupportResource(_Strict):
    id: str
    kind: Literal["emergency", "crisis_line"]
    name: str
    phone: str
    purpose: str
    availability: str
    cost: str
    operator: str
    territory: str
    source: str
    source_url: str
    verified_at: date
    notes: str | None = None


class SupportResources(_Strict):
    version: str
    professional_review: Literal["pending", "approved"]
    review_interval_days: int
    countries: dict[str, tuple[SupportResource, ...]]

    def for_country(self, country: str) -> tuple[SupportResource, ...]:
        return self.countries.get(country.upper(), ())


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@lru_cache
def get_policy() -> SafetyPolicy:
    return SafetyPolicy.model_validate(_load(POLICY_FILE))


@lru_cache
def get_resources() -> SupportResources:
    return SupportResources.model_validate(_load(RESOURCES_FILE))
