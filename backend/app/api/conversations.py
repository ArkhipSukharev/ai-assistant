from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth import get_current_user
from app.database import Conversation, User
from app.models.schemas import (
    ChatAttachment,
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    PinConversationRequest,
    RenameConversationRequest,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def get_conversation_service(request: Request) -> ConversationService:
    service = getattr(request.app.state, "conversation_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Conversation service is not ready")
    return service


def to_response(conversation: Conversation) -> ConversationResponse:
    last_message = (
        conversation.messages[-1].content[:160]
        if conversation.messages
        else None
    )
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        last_message=last_message,
        messages_count=len(conversation.messages),
        is_pinned=conversation.is_pinned,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def empty_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        last_message=None,
        messages_count=0,
        is_pinned=conversation.is_pinned,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    q: str | None = None,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> list[ConversationResponse]:
    conversations = await service.list_conversations(user.id, q)
    return [to_response(conversation) for conversation in conversations]


@router.post("", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    payload: CreateConversationRequest,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    conversation = await service.create_conversation(user.id, payload.title)
    return empty_response(conversation)


@router.patch("/{conversation_id}/pin", response_model=ConversationResponse)
async def pin_conversation(
    conversation_id: int,
    payload: PinConversationRequest,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    conversation = await service.pin_conversation(
        conversation_id, user.id, payload.is_pinned
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return empty_response(conversation)


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> list[MessageResponse]:
    conversation = await service.get_conversation(conversation_id, user.id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return [
        MessageResponse(
            id=message.id,
            role=message.role,
            content=message.content,
            attachments=[
                ChatAttachment(
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    content=attachment.content,
                )
                for attachment in message.attachments
            ],
            created_at=message.created_at,
        )
        for message in conversation.messages
    ]


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def rename_conversation(
    conversation_id: int,
    payload: RenameConversationRequest,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    conversation = await service.rename_conversation(
        conversation_id, user.id, payload.title
    )
    if not conversation:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return empty_response(conversation)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> None:
    if not await service.delete_conversation(conversation_id, user.id):
        raise HTTPException(status_code=404, detail="Чат не найден")
