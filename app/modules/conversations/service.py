"""Orquestación de un turno.

Identidad → evaluación de seguridad → memoria y recuperación autorizada → generación →
validación de salida → respuesta. Si corresponde una ruta de apoyo, se omiten las
recomendaciones y se usa el protocolo fijo.
"""

import logging
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.errors import NotFoundError, QuotaExceededError
from app.integrations import registry
from app.integrations.ai.base import AIError, ContactRef, ContextItem, Turn
from app.models import Conversation, Message, Profile
from app.modules.actions import proposals as proposal_service
from app.modules.actions.schemas import ProposalOut
from app.modules.contacts import repository as contacts
from app.modules.conversations.prompting import build_request
from app.modules.conversations.schemas import ConversationReply, SourceRef
from app.modules.memory import service as memory
from app.modules.memory.schemas import CandidateOut
from app.modules.profiles import repository as profiles
from app.modules.rag.retrieval import retrieve
from app.modules.safety import output_validation
from app.modules.safety import service as safety
from app.modules.safety.policy import get_policy
from app.modules.safety.routing import Route

logger = logging.getLogger(__name__)


def create_conversation(db: Session, owner_id: uuid.UUID, title: str | None) -> Conversation:
    conversation = Conversation(owner_id=owner_id, title=title)
    db.add(conversation)
    db.flush()
    return conversation


def list_conversations(db: Session, owner_id: uuid.UUID) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.owner_id == owner_id)
        .order_by(Conversation.updated_at.desc())
    )
    return list(db.execute(statement).scalars())


def get_conversation(db: Session, owner_id: uuid.UUID, conversation_id: uuid.UUID) -> Conversation:
    conversation = db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.owner_id == owner_id
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("La conversación no existe.")
    return conversation


def list_messages(db: Session, owner_id: uuid.UUID, conversation_id: uuid.UUID) -> list[Message]:
    get_conversation(db, owner_id, conversation_id)
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.owner_id == owner_id)
        .order_by(Message.created_at, Message.role.desc())
    )
    return list(db.execute(statement).scalars())


def delete_conversation(db: Session, owner_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    db.delete(get_conversation(db, owner_id, conversation_id))


def _recent_turns(db: Session, conversation_id: uuid.UUID) -> list[Turn]:
    rows = db.execute(
        select(Message.role, Message.content)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.role)
        .limit(get_settings().recent_turns)
    ).all()
    return [Turn(role=role, content=content) for role, content in reversed(rows)]


def _stored_reply(db: Session, conversation_id: uuid.UUID, client_message_id: str) -> dict | None:
    """Respuesta ya entregada para este ``client_message_id``, si existe."""
    user_message_id = db.execute(
        select(Message.id).where(
            Message.conversation_id == conversation_id,
            Message.client_message_id == client_message_id,
        )
    ).scalar_one_or_none()
    if user_message_id is None:
        return None
    return db.execute(
        select(Message.payload).where(Message.reply_to_id == user_message_id)
    ).scalar_one_or_none()


def _enforce_quota(db: Session, owner_id: uuid.UUID) -> None:
    """Cuota diaria de respuestas normales. Las respuestas de apoyo nunca se cuentan."""
    since = utcnow() - timedelta(days=1)
    used = db.execute(
        select(func.count())
        .select_from(Message)
        .where(
            Message.owner_id == owner_id,
            Message.role == "assistant",
            Message.mode == Route.normal.value,
            Message.created_at >= since,
        )
    ).scalar_one()
    if used >= get_settings().daily_message_quota:
        raise QuotaExceededError("Alcanzaste el límite diario de mensajes. Vuelve mañana.")


def _retrieve_items(
    db: Session, profile: Profile, conversation_id: uuid.UUID, query: str
) -> list[ContextItem]:
    """Si la recuperación falla, la conversación sigue sin fuentes y sin inventarlas."""
    try:
        with db.begin_nested():
            results = retrieve(
                db,
                owner_id=profile.id,
                query=query,
                territory=profile.country,
                conversation_id=conversation_id,
            )
    except Exception as exc:  # noqa: BLE001 - el RAG es opcional para responder
        logger.warning("La recuperación falló (%s); se responde sin fuentes", type(exc).__name__)
        return []
    return [result.item for result in results]


def _normal_reply(
    db: Session, profile: Profile, conversation: Conversation, content: str, turns: list[Turn]
) -> dict:
    items = _retrieve_items(db, profile, conversation.id, content)
    contact_refs = [
        ContactRef(id=str(c.id), name=c.name, relationship=c.relationship or "")
        for c in contacts.list_accepted(db, profile.id)
    ]
    request = build_request(
        profile=profile,
        preferences=profiles.list_preferences(db, profile.id),
        contacts=contact_refs,
        items=items,
        turns=turns,
        message=content,
    )
    try:
        draft = registry.get_ai().generate(request)
    except AIError:
        logger.warning("La generación falló; se responde con el texto fijo")
        return {"reply": get_policy().generation_fallback.strip()}

    output = output_validation.validate(draft, items=items, contacts=contact_refs)
    created = proposal_service.create_drafts(db, profile.id, conversation.id, output.proposals)
    candidates = memory.propose_candidates(
        db, profile.id, conversation.id, output.memory_candidates
    )
    return {
        "reply": output.reply,
        "proposals": [ProposalOut.model_validate(p) for p in created],
        "memory_candidates": [CandidateOut.model_validate(c) for c in candidates],
        "source_refs": [
            SourceRef(type=s.type, id=uuid.UUID(s.id), title=s.title, version=s.version)
            for s in output.sources
        ],
    }


def handle_message(
    db: Session,
    profile: Profile,
    conversation_id: uuid.UUID,
    content: str,
    *,
    source: str = "text",
    client_message_id: str | None = None,
) -> ConversationReply:
    conversation = get_conversation(db, profile.id, conversation_id)
    if client_message_id is not None:
        stored = _stored_reply(db, conversation.id, client_message_id)
        if stored is not None:
            return ConversationReply.model_validate(stored)

    turns = _recent_turns(db, conversation.id)
    user_message = Message(
        conversation_id=conversation.id,
        owner_id=profile.id,
        role="user",
        content=content,
        source=source,
        client_message_id=client_message_id,
    )
    db.add(user_message)
    db.flush()

    # La seguridad se evalúa antes de recuperar contexto o generar recomendaciones.
    decision = safety.evaluate_turn(db, profile, conversation.id, content, turns)
    if decision.is_normal:
        _enforce_quota(db, profile.id)
        parts = _normal_reply(db, profile, conversation, content, turns)
    else:
        parts = {"reply": decision.reply}

    reply_id = uuid.uuid4()
    reply = ConversationReply(
        message_id=reply_id,
        reply=parts["reply"],
        mode=decision.mode.value,
        proposals=parts.get("proposals", []),
        memory_candidates=parts.get("memory_candidates", []),
        source_refs=parts.get("source_refs", []),
        support_options=decision.support_options,
    )
    db.add(
        Message(
            id=reply_id,
            conversation_id=conversation.id,
            owner_id=profile.id,
            role="assistant",
            content=reply.reply,
            mode=reply.mode,
            source=source,
            reply_to_id=user_message.id,
            payload=reply.model_dump(mode="json"),
        )
    )
    conversation.updated_at = utcnow()
    db.flush()
    return reply
