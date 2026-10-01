"""Comprobación del proveedor de IA configurado: una llamada mínima por capacidad.

Sirve para verificar credenciales y disponibilidad de modelos antes de habilitar a
nadie. Usa textos neutros y no guarda nada.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import EMBEDDING_DIMENSIONS
from app.integrations import registry
from app.integrations.ai.base import AgentRequest, AIError, AIProvider

SAMPLE = "Hola, me gustaría saber qué actividades hay esta semana."


@dataclass(frozen=True)
class CheckResult:
    capability: str
    ok: bool
    detail: str


def _embeddings(ai: AIProvider) -> str:
    [vector] = ai.embed([SAMPLE])
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise AIError(f"dimensión {len(vector)}, se esperaba {EMBEDDING_DIMENSIONS}")
    return f"{len(vector)} dimensiones"


def _moderation(ai: AIProvider) -> str:
    return "marcado" if ai.moderate(SAMPLE).flagged else "sin marcas"


def _classifier(ai: AIProvider) -> str:
    return f"intent={ai.classify_safety(message=SAMPLE, recent_turns=[]).intent}"


def _generation(ai: AIProvider) -> str:
    draft = ai.generate(
        AgentRequest(
            instructions="Responde en una oración breve y sin propuestas.",
            context_text="<datos_recuperados>\n(sin resultados)\n</datos_recuperados>",
            message=SAMPLE,
        )
    )
    return f"{len(draft.reply)} caracteres"


def _speech_and_transcription(_: AIProvider) -> str:
    ai = registry.get_speech()
    audio = ai.synthesize("Hola, esta es una prueba de voz.")
    if not audio:
        raise AIError("síntesis vacía")
    extension = "mp3" if ai.speech_content_type == "audio/mpeg" else "wav"
    text = ai.transcribe(audio=audio, filename=f"prueba.{extension}", language="es")
    return f"{ai.name}: {len(audio)} bytes de audio; transcripción de {len(text)} caracteres"


CHECKS: list[tuple[str, Callable[[AIProvider], str]]] = [
    ("embeddings", _embeddings),
    ("moderación", _moderation),
    ("clasificador de seguridad", _classifier),
    ("generación estructurada", _generation),
    ("voz y transcripción", _speech_and_transcription),
]


def run_checks() -> list[CheckResult]:
    ai = registry.get_ai()
    results = []
    for capability, check in CHECKS:
        try:
            results.append(CheckResult(capability, True, check(ai)))
        except AIError as exc:
            # Solo el tipo de error del proveedor: nunca la clave ni el contenido.
            results.append(CheckResult(capability, False, str(exc)))
    return results
