from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import selectinload

from app.database import (
    ChatMessage,
    Conversation,
    Database,
    MessageAttachment,
    utcnow,
)


def make_title(message: str) -> str:
    compact = " ".join(message.strip().split())
    if not compact:
        return "Новый чат"
    return compact[:57] + ("…" if len(compact) > 57 else "")


class ConversationService:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def list_conversations(
        self, user_id: int, query: str | None = None
    ) -> list[Conversation]:
        async with self.database.session_factory() as session:
            statement = (
                select(Conversation)
                .options(
                    selectinload(Conversation.messages).selectinload(
                        ChatMessage.attachments
                    )
                )
                .where(Conversation.user_id == user_id)
            )
            normalized = (query or "").strip()
            if normalized:
                pattern = f"%{normalized[:200]}%"
                statement = statement.where(
                    or_(
                        Conversation.title.ilike(pattern),
                        Conversation.messages.any(ChatMessage.content.ilike(pattern)),
                    )
                )
            result = await session.scalars(
                statement.order_by(
                    Conversation.is_pinned.desc(),
                    Conversation.updated_at.desc(),
                )
            )
            return list(result)

    async def get_conversation(
        self, conversation_id: int, user_id: int
    ) -> Conversation | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(Conversation)
                .options(
                    selectinload(Conversation.messages).selectinload(
                        ChatMessage.attachments
                    )
                )
                .where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )

    async def create_conversation(
        self, user_id: int, title: str = "Новый чат"
    ) -> Conversation:
        async with self.database.session_factory() as session:
            conversation = Conversation(
                user_id=user_id,
                title=make_title(title),
            )
            session.add(conversation)
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def rename_conversation(
        self, conversation_id: int, user_id: int, title: str
    ) -> Conversation | None:
        async with self.database.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if not conversation:
                return None
            conversation.title = make_title(title)
            conversation.updated_at = utcnow()
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def pin_conversation(
        self, conversation_id: int, user_id: int, is_pinned: bool
    ) -> Conversation | None:
        async with self.database.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if not conversation:
                return None
            conversation.is_pinned = is_pinned
            await session.commit()
            await session.refresh(conversation)
            return conversation

    async def delete_conversation(self, conversation_id: int, user_id: int) -> bool:
        async with self.database.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation.id).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if conversation is None:
                return False
            message_ids = select(ChatMessage.id).where(
                ChatMessage.conversation_id == conversation_id
            )
            await session.execute(
                delete(MessageAttachment).where(
                    MessageAttachment.message_id.in_(message_ids)
                )
            )
            await session.execute(
                delete(ChatMessage).where(
                    ChatMessage.conversation_id == conversation_id
                )
            )
            await session.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            await session.commit()
            return True

    async def add_message(
        self,
        conversation_id: int,
        user_id: int,
        role: str,
        content: str,
        attachments: list[dict[str, str]] | None = None,
    ) -> ChatMessage | None:
        if role not in {"user", "assistant"}:
            raise ValueError("Недопустимая роль сообщения")
        async with self.database.session_factory() as session:
            conversation = await session.scalar(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.user_id == user_id,
                )
            )
            if not conversation:
                return None
            unique_attachments: list[MessageAttachment] = []
            seen_attachments: set[tuple[str, str]] = set()
            for attachment in attachments or []:
                key = (attachment["content_type"], attachment["content"])
                if key in seen_attachments:
                    continue
                seen_attachments.add(key)
                unique_attachments.append(
                    MessageAttachment(
                        filename=attachment["filename"],
                        content_type=attachment["content_type"],
                        content=attachment["content"],
                    )
                )
            message = ChatMessage(
                conversation_id=conversation.id,
                role=role,
                content=content,
                attachments=unique_attachments,
            )
            conversation.updated_at = utcnow()
            session.add(message)
            await session.commit()
            await session.refresh(message)
            return message

    async def llm_history(
        self, conversation_id: int, user_id: int, limit: int = 40
    ) -> list[dict[str, str]]:
        conversation = await self.get_conversation(conversation_id, user_id)
        if not conversation:
            return []
        return [
            {"role": message.role, "content": message.content}
            for message in conversation.messages[-limit:]
        ]
