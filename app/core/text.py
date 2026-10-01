"""Normalización de texto en español compartida por reglas, búsqueda y simuladores."""

import math
import re
import unicodedata

_WORD = re.compile(r"[a-zñ0-9]+")
TOKEN_CHARS = 3.5  # caracteres por token; estimación conservadora para español


def normalize(text: str) -> str:
    """Minúsculas, sin acentos (conserva la ñ) y con espacios simples."""
    lowered = text.lower().replace("ñ", "\0")
    decomposed = unicodedata.normalize("NFKD", lowered)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped.replace("\0", "ñ")).strip()


def words(text: str) -> list[str]:
    return _WORD.findall(normalize(text))


def estimate_tokens(text: str) -> int:
    """Aproximación del número de tokens. No es exacta; ver docs/architecture.md."""
    return math.ceil(len(text) / TOKEN_CHARS)
