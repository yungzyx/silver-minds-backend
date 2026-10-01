"""División de documentos en fragmentos de aproximadamente 400–700 tokens."""

import re

from app.core.text import estimate_tokens

MIN_TOKENS = 400
TARGET_TOKENS = 550
MAX_TOKENS = 700
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _split_long(paragraph: str) -> list[str]:
    """Un párrafo que supera el máximo se divide por oraciones."""
    pieces, current = [], ""
    for sentence in _SENTENCE_END.split(paragraph):
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate) > MAX_TOKENS:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str) -> list[str]:
    """Agrupa párrafos hasta el tamaño objetivo sin cortar a mitad de párrafo."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", text.strip()):
        block = re.sub(r"[ \t]*\n[ \t]*", " ", block).strip()
        if block:
            paragraphs.extend(
                _split_long(block) if estimate_tokens(block) > MAX_TOKENS else [block]
            )

    chunks, current = [], ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip()
        if current and estimate_tokens(candidate) > MAX_TOKENS:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate
        if estimate_tokens(current) >= TARGET_TOKENS:
            chunks.append(current)
            current = ""
    if current:
        # Un resto pequeño se une al fragmento anterior si el resultado sigue siendo razonable.
        merged = f"{chunks[-1]}\n\n{current}" if chunks else current
        if (
            chunks
            and estimate_tokens(current) < MIN_TOKENS
            and estimate_tokens(merged) <= (MAX_TOKENS + MIN_TOKENS // 2)
        ):
            chunks[-1] = merged
        else:
            chunks.append(current)
    return chunks
