"""Prompts versionados. Viven en ``config/prompts`` y se versionan con el código."""

from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"
AGENT_PROMPT_VERSION = "agent.v1"
CLASSIFIER_PROMPT_VERSION = "safety_classifier.v1"


@lru_cache
def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8").strip()
