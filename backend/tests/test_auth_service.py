import asyncio

from app.config import Settings
from app.database import Database
from app.services.auth_service import AuthService


def test_admin_bootstrap_login_and_user_management(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(
            f"sqlite+aiosqlite:///{(tmp_path / 'auth.db').as_posix()}"
        )
        settings = Settings(
            _env_file=None,
            database_url="sqlite+aiosqlite:///unused.db",
            admin_email="admin@example.com",
            admin_password="very-secure-admin-password",
        )
        auth = AuthService(database, settings)
        await database.create_tables()
        await auth.bootstrap_admin()

        login = await auth.authenticate(
            "admin@example.com", "very-secure-admin-password"
        )
        assert login is not None
        admin, token = login
        assert admin.role == "admin"
        assert await auth.user_from_token(token) is not None

        user = await auth.create_user(
            actor_id=admin.id,
            name="Test User",
            email="user@example.com",
            password="very-secure-user-password",
            role="user",
            telegram_id=123456789,
            request_limit_per_day=2,
        )
        assert (await auth.user_by_telegram_id(123456789)).id == user.id
        assert await auth.consume_request(user.id) is True
        assert await auth.consume_request(user.id) is True
        assert await auth.consume_request(user.id) is False

        updated = await auth.update_user(
            user.id, admin.id, {"is_active": False, "telegram_id": None}
        )
        assert updated is not None
        assert updated.is_active is False
        assert await auth.user_by_telegram_id(123456789) is None

        logs = await auth.recent_audit_logs()
        assert {log.action for log in logs} >= {
            "auth.login",
            "user.create",
            "user.update",
        }

        await auth.logout(token)
        assert await auth.user_from_token(token) is None
        await database.close()

    asyncio.run(scenario())
