"""Proveedor de IA simulado y determinista.

Sirve para desarrollo sin credenciales y para pruebas. **No es un modelo**: sus
heurísticas de seguridad y de generación son deliberadamente simples y no dicen nada
sobre el comportamiento del proveedor real.
"""

import hashlib
import io
import math
import re
import wave

from app.core.config import EMBEDDING_DIMENSIONS
from app.core.text import normalize, words
from app.integrations.ai.base import (
    AgentDraft,
    AgentRequest,
    AIError,
    MemoryDraft,
    ModerationResult,
    ProposalDraft,
    SafetyClassification,
    Turn,
)

_STOPWORDS = frozenset(
    {
        "a", "al", "algo", "como", "con", "de", "del", "el", "ella", "ellos", "en", "es",
        "esta", "este", "esto", "la", "las", "le", "lo", "los", "me", "mi", "mis", "muy",
        "no", "nos", "o", "para", "pero", "por", "que", "se", "si", "sin", "su", "sus",
        "te", "tu", "un", "una", "uno", "y", "ya", "yo", "hay", "mas",
    }
)  # fmt: skip
_STEM_LENGTH = 5

_IMMINENT = (
    r"ahora mismo|esta noche|ya (me )?tome|tengo las pastillas|tengo el arma|voy a hacerlo|"
    r"me voy a matar|estoy a punto"
)
_IDEATION = (
    r"quiero morir|quiero matarme|matarme|suicid|quitarme la vida|no quiero (seguir )?vivi|"
    r"hacerme daño|acabar con todo|terminar con mi vida"
)
_UNCLEAR = (
    r"no doy mas|no vale la pena|quiero desaparecer|soy una carga|mejor sin mi|"
    r"para que seguir|no tiene sentido|no aguanto mas"
)
_THIRD_PARTY = (
    r"mi vecin|mi amig|mi herman|mi hij|mi niet|mi espos|mi marido|mi señora|un conocid|"
    r"dijo que|se quiere|se queria|se quito"
)
_FICTION = r"pelicula|novela|libro|cancion|teleserie|personaje|noticia|leia|lei que"
_PAST = r"hace años|hace tiempo|hace mucho|cuando era|en el pasado|antes pensaba|una vez pense"
_HYPOTHETICAL = r"si alguna vez|que pasaria si|imagina|supongamos"
_NEGATED = (
    r"no quiero morir|no quiero matarme|no pienso hacerme|nunca me haria|jamas me haria|"
    r"no tengo intencion|no me haria daño|no voy a hacerme"
)
_BYPASS = r"ignora|olvida (tus|las) (reglas|politicas|instrucciones)|sin restricciones|modo sin"

_PROPOSAL_TRIGGERS = (
    r"que puedo hacer|actividad|panorama|salir|invitar|me gustaria|aburrid|algo distinto|"
    r"recomiend|propon|taller|juntar"
)
_LIKE = re.compile(
    r"\b(?:me gusta(?:n)?|me encanta(?:n)?|disfruto)\s+(?:mucho\s+)?(.+?)(?:[.,;!?]|$)"
)
_PREFER = re.compile(r"\bprefiero\s+(.+?)(?:[.,;!?]|$)")
_DISLIKE = re.compile(r"\bno me gusta(?:n)?\s+(.+?)(?:[.,;!?]|$)")
_SCHEDULE_HINT = re.compile(r"mañana|tarde|noche|hora|temprano|dia|lunes|martes|fin de semana")

DEFAULT_TRANSCRIPT = "Hola, me gustaría hacer algo distinto esta semana."


def _stem(word: str) -> str:
    return word[:_STEM_LENGTH]


class FakeAIProvider:
    name = "fake"
    speech_content_type = "audio/wav"

    def __init__(self) -> None:
        # Puntos de control para pruebas.
        self.failing: set[str] = set()
        self.scripted_classification: SafetyClassification | None = None
        self.scripted_moderation: ModerationResult | None = None
        self.scripted_draft: AgentDraft | None = None
        self.next_transcript: str = DEFAULT_TRANSCRIPT
        self.calls: list[str] = []
        self.last_request: AgentRequest | None = None

    def _enter(self, operation: str) -> None:
        self.calls.append(operation)
        if operation in self.failing:
            raise AIError(f"Fallo simulado en {operation}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        self._enter("embed")
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIMENSIONS
        for word in words(text):
            if word in _STOPWORDS or len(word) < 3:
                continue
            digest = hashlib.sha256(_stem(word).encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            vector[0] = 1.0  # pgvector no admite distancia coseno con el vector nulo
            return vector
        return [value / norm for value in vector]

    def moderate(self, text: str) -> ModerationResult:
        self._enter("moderate")
        if self.scripted_moderation is not None:
            return self.scripted_moderation
        normalized = normalize(text)
        intent = bool(re.search(f"{_IDEATION}|{_IMMINENT}", normalized))
        return ModerationResult(
            flagged=intent, self_harm=intent, self_harm_intent=intent, self_harm_instructions=False
        )

    def classify_safety(self, *, message: str, recent_turns: list[Turn]) -> SafetyClassification:
        self._enter("classify_safety")
        if self.scripted_classification is not None:
            return self.scripted_classification
        text = normalize(message)
        subject = "self"
        if re.search(_FICTION, text):
            subject = "quote_or_fiction"
        elif re.search(_THIRD_PARTY, text):
            subject = "third_party"
        timeframe = "present"
        if re.search(_PAST, text):
            timeframe = "past"
        elif re.search(_HYPOTHETICAL, text):
            timeframe = "hypothetical"
        negated = bool(re.search(_NEGATED, text))
        intent = "none"
        if subject == "self" and timeframe == "present" and not negated:
            if re.search(_IMMINENT, text) and re.search(f"{_IDEATION}|pastillas|arma", text):
                intent = "imminent"
            elif re.search(_IDEATION, text):
                intent = "ideation"
            elif re.search(_UNCLEAR, text):
                intent = "unclear"
        return SafetyClassification(
            subject=subject,
            timeframe=timeframe,
            negated=negated,
            intent=intent,
            bypass_attempt=bool(re.search(_BYPASS, text)),
        )

    def generate(self, request: AgentRequest) -> AgentDraft:
        self._enter("generate")
        self.last_request = request
        if self.scripted_draft is not None:
            return self.scripted_draft
        text = normalize(request.message)
        candidates = self._memory_candidates(text)
        proposals, used = self._proposals(text, request)
        greeting = (
            f"Gracias por contarme, {request.preferred_name}."
            if request.preferred_name
            else ("Gracias por contarme.")
        )
        parts = [greeting]
        if candidates:
            parts.append(f"¿Quieres que recuerde esto: «{candidates[0].content}»?")
        if proposals:
            parts.append(
                f"Encontré algo que podría interesarte: {proposals[0].title}. "
                "Te dejo un borrador para que lo revises; tú decides si lo apruebas."
            )
        elif not candidates:
            parts.append("Cuéntame un poco más sobre lo que te gustaría hacer.")
        return AgentDraft(
            reply=" ".join(parts),
            proposals=proposals,
            memory_candidates=candidates,
            used_source_ids=used,
        )

    @staticmethod
    def _memory_candidates(text: str) -> list[MemoryDraft]:
        dislike = _DISLIKE.search(text)
        if dislike:
            return [
                MemoryDraft(category="limit", content=f"No le gusta {dislike.group(1).strip()}")
            ]
        prefer = _PREFER.search(text)
        if prefer:
            value = prefer.group(1).strip()
            category = "schedule" if _SCHEDULE_HINT.search(value) else "interest"
            return [MemoryDraft(category=category, content=f"Prefiere {value}")]
        like = _LIKE.search(text)
        if like:
            return [MemoryDraft(category="interest", content=f"Le gusta {like.group(1).strip()}")]
        return []

    @staticmethod
    def _proposals(text: str, request: AgentRequest) -> tuple[list[ProposalDraft], list[str]]:
        activities = [item for item in request.items if item.type == "activity"]
        if not activities or not re.search(_PROPOSAL_TRIGGERS, text):
            return [], []
        activity = activities[0]
        contact = next((c for c in request.contacts if normalize(c.name) in text), None)
        proposal = ProposalDraft(
            kind="invitation" if contact else "activity",
            title=activity.title,
            body=(
                f"¿Te gustaría ir conmigo a «{activity.title}»?"
                if contact
                else f"Participar en «{activity.title}»."
            ),
            activity_id=activity.id,
            contact_id=contact.id if contact else None,
        )
        used = [activity.id] + [item.id for item in request.items if item.type == "memory"][:2]
        return [proposal], used

    def transcribe(self, *, audio: bytes, filename: str, language: str) -> str:
        self._enter("transcribe")
        return self.next_transcript

    def synthesize(self, text: str) -> bytes:
        self._enter("synthesize")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8000)
            output.writeframes(b"\x00\x00" * 800)  # 0,1 s de silencio: audio simulado
        return buffer.getvalue()
