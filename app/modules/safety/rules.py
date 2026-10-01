"""Capa 1: reglas para expresiones explícitas.

Son palabras clave sin contexto. Por sí solas no deciden la ruta, salvo cuando las
demás capas fallan.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from app.core.text import normalize
from app.modules.safety.policy import Rules, get_policy


@dataclass(frozen=True)
class RuleSignals:
    urgent: bool = False
    explicit: bool = False
    ambiguous: bool = False
    bypass: bool = False

    @property
    def any_explicit(self) -> bool:
        return self.urgent or self.explicit


@lru_cache
def _compiled(patterns: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{pattern})" for pattern in patterns))


def _matches(patterns: tuple[str, ...], text: str) -> bool:
    return bool(patterns) and _compiled(patterns).search(text) is not None


def evaluate(message: str, rules: Rules | None = None) -> RuleSignals:
    rules = rules or get_policy().rules
    text = normalize(message)
    return RuleSignals(
        urgent=_matches(rules.urgent, text),
        explicit=_matches(rules.explicit, text),
        ambiguous=_matches(rules.ambiguous, text),
        bypass=_matches(rules.bypass, text),
    )
