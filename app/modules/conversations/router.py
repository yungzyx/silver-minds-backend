"""Rutas de conversaciones y mensajes."""

import uuid

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentProfile, DbSession
from app.modules.conversations import service
from app.modules.conversations.schemas import (
    ConversationIn,
    ConversationList,
    ConversationOut,
    ConversationReply,
    MessageIn,
    MessageList,
    MessageOut,
)

router = APIRouter(tags=["conversaciones"])


@router.post(
    "/conversations",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una conversación",
)
def create_conversation(
    db: DbSession, profile: CurrentProfile, data: ConversationIn | None = None
) -> ConversationOut:
    conversation = service.create_conversation(db, profile.id, data.title if data else None)
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=ConversationList, summary="Listar conversaciones")
def list_conversations(db: DbSession, profile: CurrentProfile) -> ConversationList:
    items = [ConversationOut.model_validate(c) for c in service.list_conversations(db, profile.id)]
    return ConversationList(items=items, total=len(items))


@router.get(
    "/conversations/{conversation_id}/messages", response_model=MessageList, summary="Historial"
)
def list_messages(
    conversation_id: uuid.UUID, db: DbSession, profile: CurrentProfile
) -> MessageList:
    messages = service.list_messages(db, profile.id, conversation_id)
    items = [MessageOut.model_validate(m) for m in messages]
    return MessageList(items=items, total=len(items))


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationReply,
    summary="Enviar un mensaje",
    description=(
        "Flujo: identidad → evaluación de seguridad → memoria y recuperación autorizada → "
        "generación → validación de salida. En una ruta distinta de `normal` la respuesta "
        "es el protocolo fijo de apoyo, sin propuestas ni memorias candidatas."
    ),
)
def send_message(
    conversation_id: uuid.UUID, data: MessageIn, db: DbSession, profile: CurrentProfile
) -> ConversationReply:
    return service.handle_message(
        db, profile, conversation_id, data.content, client_message_id=data.client_message_id
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar la conversación y sus mensajes",
)
def delete_conversation(
    conversation_id: uuid.UUID, db: DbSession, profile: CurrentProfile
) -> Response:
    service.delete_conversation(db, profile.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
