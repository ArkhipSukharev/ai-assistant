import asyncio

from app.config import Settings
from app.database import Database
from app.services.app_settings import AppSettingsService
from app.services.auth_service import AuthService
from app.services.llm_client import LLMClient


def test_model_setting_is_persisted(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(
            f"sqlite+aiosqlite:///{(tmp_path / 'settings.db').as_posix()}"
        )
        settings = Settings(
            _env_file=None,
            llm_api_key="",
            llm_model="gpt-4.1-mini",
            admin_email="admin@example.com",
            admin_password="very-secure-admin-password",
        )
        await database.create_tables()
        auth = AuthService(database, settings)
        await auth.bootstrap_admin()
        admin = (await auth.list_users())[0]
        user = await auth.create_user(
            actor_id=admin.id,
            name="Regular User",
            email="user@example.com",
            password="very-secure-user-password",
            role="user",
            telegram_id=None,
            request_limit_per_day=50,
        )

        service = AppSettingsService(database, settings, LLMClient(settings))
        assert await service.get_model() == "gpt-4.1-mini"
        assert len(await service.available_models()) >= 2

        await service.set_model("gpt-4o", admin.id)
        assert await service.get_model() == "gpt-4o"
        assert await service.get_model_for_user(user.id, user.role) == "gpt-4.1-mini"
        assert len(await service.available_models(role="user")) == 1
        await service.set_user_model(
            user.id, user.role, "gpt-4.1-mini"
        )
        assert (
            await service.get_model_for_user(user.id, user.role)
            == "gpt-4.1-mini"
        )
        try:
            await service.set_user_model(user.id, user.role, "gpt-4o")
            raise AssertionError("Regular user selected a full model")
        except ValueError:
            pass
        actions = {log.action for log in await auth.recent_audit_logs()}
        assert {"settings.model.update", "user.model.update"} <= actions

        await database.close()

    asyncio.run(scenario())
