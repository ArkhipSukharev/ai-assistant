from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.api.auth import get_auth_service, get_current_user, require_admin
from app.api.conversations import get_conversation_service
from app.config import Settings, get_settings
from app.database import User
from app.models.schemas import BalanceResponse, ChatRequest, ChatResponse
from app.services.agent import AgentService
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService
from app.services.llm_client import LLMClient

router = APIRouter(prefix="/api", tags=["chat"])
limiter = Limiter(key_func=get_remote_address)


def get_agent(request: Request) -> AgentService:
    agent = getattr(request.app.state, "agent", None)
    if not agent:
        raise HTTPException(status_code=503, detail="Agent service is not ready")
    return agent


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("20/minute")
async def chat(
    request: Request,
    payload: ChatRequest,
    agent: AgentService = Depends(get_agent),
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
    conversations: ConversationService = Depends(get_conversation_service),
    settings: Settings = Depends(get_settings),
) -> ChatResponse:
    if not settings.llm_api_key or settings.llm_api_key == "sk-llm-...":
        raise HTTPException(status_code=503, detail="LLM API пока не настроен")
    if not await auth.consume_request(user.id):
        raise HTTPException(status_code=429, detail="Дневной лимит запросов исчерпан")

    if payload.conversation_id:
        conversation = await conversations.get_conversation(
            payload.conversation_id, user.id
        )
        if not conversation:
            raise HTTPException(status_code=404, detail="Чат не найден")
    else:
        conversation = await conversations.create_conversation(
            user.id, payload.message
        )

    history = await conversations.llm_history(conversation.id, user.id)
    user_message = await conversations.add_message(
        conversation.id, user.id, "user", payload.message
    )
    internal_session_id = f"conversation:{conversation.id}"
    try:
        session_id, reply, attachments = await agent.chat(
            payload.message,
            internal_session_id,
            user_id=user.id,
            user_role=user.role,
            amocrm_user_id=user.amocrm_user_id,
            amocrm_user_name=user.name,
            history_override=history,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Chat processing failed: {exc}") from exc

    assistant_message = await conversations.add_message(
        conversation.id,
        user.id,
        "assistant",
        reply,
        attachments=[attachment.model_dump() for attachment in attachments],
    )
    return ChatResponse(
        session_id=session_id,
        conversation_id=conversation.id,
        title=conversation.title,
        reply=reply,
        attachments=attachments,
        user_message_created_at=user_message.created_at,
        assistant_message_created_at=assistant_message.created_at,
    )


@router.delete("/chat/{session_id}")
async def clear_chat(
    session_id: str,
    agent: AgentService = Depends(get_agent),
    user: User = Depends(get_current_user),
) -> dict[str, str]:
    if not session_id.startswith(f"web:{user.id}:"):
        raise HTTPException(status_code=403, detail="Недоступная сессия")
    await agent.clear_session(session_id)
    return {"status": "cleared", "session_id": session_id}


@router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    _: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> BalanceResponse:
    if not settings.llm_api_key:
        raise HTTPException(status_code=503, detail="LLM is not configured")
    client = LLMClient(settings)
    try:
        balance = await client.get_balance()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch LLM balance: {exc}") from exc
    return BalanceResponse(balance=balance)
