import asyncio

from sqlalchemy import select

from app.config import Settings
from app.database import AppSetting, Database, User
from app.services.telegram_proxy import (
    PROXY_SETTING_KEY,
    TelegramProxyService,
)


def test_proxy_settings_are_encrypted_and_password_is_preserved(tmp_path) -> None:
    async def scenario() -> None:
        database = Database(
            f"sqlite+aiosqlite:///{(tmp_path / 'proxy.db').as_posix()}"
        )
        await database.create_tables()
        settings = Settings(
            _env_file=None,
            admin_password="encryption-secret-for-tests",
        )
        service = TelegramProxyService(database, settings)
        async with database.session_factory() as session:
            admin = User(
                name="Admin",
                email="admin@example.com",
                password_hash="not-used",
                role="admin",
            )
            session.add(admin)
            await session.commit()
            await session.refresh(admin)

        config = await service.set_config(
            {
                "enabled": True,
                "scheme": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "user@example.com",
                "password": "password:value",
                "clear_password": False,
            },
            actor_id=admin.id,
        )
        proxy_url = service.build_proxy_url(config)
        assert proxy_url == (
            "socks5://user%40example.com:password%3Avalue@"
            "proxy.example.com:1080"
        )

        async with database.session_factory() as session:
            encrypted = await session.scalar(
                select(AppSetting.value).where(
                    AppSetting.key == PROXY_SETTING_KEY
                )
            )
        assert encrypted
        assert "password:value" not in encrypted

        preserved = await service.merge_update(
            {
                "enabled": True,
                "scheme": "http",
                "host": "proxy.example.com",
                "port": 8080,
                "username": "user@example.com",
                "password": "",
                "clear_password": False,
            }
        )
        assert preserved["password"] == "password:value"

        cleared = await service.merge_update(
            {**preserved, "password": "", "clear_password": True}
        )
        assert cleared["password"] == ""
        await database.close()

    asyncio.run(scenario())
