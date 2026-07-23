import asyncio

from app.config import Settings
from app.database import Database
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService


def test_conversations_persist_messages_and_are_user_scoped(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(
            f"sqlite+aiosqlite:///{(tmp_path / 'conversations.db').as_posix()}"
        )
        settings = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///unused.db",
            admin_email="admin@example.com",
            admin_password="very-secure-admin-password",
        )
        auth = AuthService(database, settings)
        service = ConversationService(database)
        await database.create_tables()
        await auth.bootstrap_admin()
        admin, _ = await auth.authenticate(
            "admin@example.com", "very-secure-admin-password"
        )
        other = await auth.create_user(
            actor_id=admin.id,
            name="Other User",
            email="other@example.com",
            password="very-secure-user-password",
            role="user",
            telegram_id=None,
            request_limit_per_day=50,
        )

        conversation = await service.create_conversation(
            admin.id, "Сделай отчёт за июль по всем сделкам"
        )
        await service.add_message(conversation.id, admin.id, "user", "Запрос")
        await service.add_message(
            conversation.id,
            admin.id,
            "assistant",
            "Ответ",
            attachments=[
                {
                    "filename": "report.json",
                    "content_type": "application/vnd.amocrm.report+json",
                    "content": '{"kind":"report"}',
                },
                {
                    "filename": "report-copy.json",
                    "content_type": "application/vnd.amocrm.report+json",
                    "content": '{"kind":"report"}',
                },
            ],
        )

        loaded = await service.get_conversation(conversation.id, admin.id)
        assert loaded is not None
        assert loaded.title == "Сделай отчёт за июль по всем сделкам"
        assert [message.content for message in loaded.messages] == ["Запрос", "Ответ"]
        assert len(loaded.messages[1].attachments) == 1
        assert loaded.messages[1].attachments[0].filename == "report.json"
        assert await service.llm_history(conversation.id, admin.id) == [
            {"role": "user", "content": "Запрос"},
            {"role": "assistant", "content": "Ответ"},
        ]
        assert (await service.pin_conversation(conversation.id, admin.id, True)).is_pinned
        assert (await service.list_conversations(admin.id))[0].is_pinned is True
        assert [item.id for item in await service.list_conversations(admin.id, "Запрос")] == [
            conversation.id
        ]
        assert await service.list_conversations(admin.id, "не найдено") == []
        assert await service.get_conversation(conversation.id, other.id) is None
        assert await service.delete_conversation(conversation.id, other.id) is False
        assert await service.delete_conversation(conversation.id, admin.id) is True
        assert await service.list_conversations(admin.id) == []
        replacement = await service.create_conversation(admin.id, "Новый диалог")
        replacement_loaded = await service.get_conversation(replacement.id, admin.id)
        assert replacement_loaded is not None
        assert replacement_loaded.messages == []
        await database.close()

    asyncio.run(scenario())
