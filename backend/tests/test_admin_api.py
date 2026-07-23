import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.settings import router as settings_router
from app.config import Settings
from app.database import Database
from app.services.app_settings import AppSettingsService
from app.services.auth_service import AuthService
from app.services.f5ai_client import F5AIClient
from app.services.telegram_proxy import TelegramProxyService


def test_login_cookie_and_admin_users_api(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{(tmp_path / 'api-auth.db').as_posix()}"
    )
    settings = Settings(
        _env_file=None,
        admin_email="admin@example.com",
        admin_password="very-secure-admin-password",
    )
    auth = AuthService(database, settings)
    asyncio.run(database.create_tables())
    asyncio.run(auth.bootstrap_admin())

    app = FastAPI()
    app.state.auth_service = auth
    app.state.app_settings_service = AppSettingsService(
        database, settings, F5AIClient(settings)
    )
    proxy_service = TelegramProxyService(database, settings)
    app.state.telegram_proxy_service = proxy_service
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(admin_router)

    with TestClient(app) as client:
        assert client.get("/api/auth/me").status_code == 401
        login = client.post(
            "/api/auth/login",
            json={
                "email": "admin@example.com",
                "password": "very-secure-admin-password",
            },
        )
        assert login.status_code == 200
        assert login.json()["role"] == "admin"
        assert client.get("/api/auth/me").status_code == 200

        created = client.post(
            "/api/admin/users",
            json={
                "name": "Sales User",
                "email": "sales@example.com",
                "password": "very-secure-sales-password",
                "role": "user",
                "telegram_id": 111222333,
                "request_limit_per_day": 25,
            },
        )
        assert created.status_code == 201
        assert created.json()["telegram_id"] == 111222333
        assert len(client.get("/api/admin/users").json()) == 2

        model_settings = client.get("/api/admin/settings")
        assert model_settings.status_code == 200
        assert model_settings.json()["selected_model"] == "gpt-4.1-mini"
        updated_settings = client.patch(
            "/api/admin/settings",
            json={"selected_model": "gpt-4o"},
        )
        assert updated_settings.status_code == 200
        assert updated_settings.json()["selected_model"] == "gpt-4o"

        proxy_settings = client.get("/api/admin/telegram-proxy")
        assert proxy_settings.status_code == 200
        assert proxy_settings.json()["enabled"] is False
        saved_proxy = client.patch(
            "/api/admin/telegram-proxy",
            json={
                "enabled": True,
                "scheme": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "proxy-user",
                "password": "secret-password",
            },
        )
        assert saved_proxy.status_code == 200
        assert saved_proxy.json()["password_configured"] is True
        assert "password" not in saved_proxy.json()
        decrypted_proxy = asyncio.run(proxy_service.get_config())
        assert decrypted_proxy["password"] == "secret-password"
        proxy_profiles = client.get("/api/admin/telegram-proxies")
        assert proxy_profiles.status_code == 200
        assert len(proxy_profiles.json()["profiles"]) == 1
        created_profile = client.post(
            "/api/admin/telegram-proxies",
            json={
                "name": "Резервный прокси",
                "scheme": "http",
                "host": "backup.example.com",
                "port": 8080,
                "username": "",
                "password": "",
            },
        )
        assert created_profile.status_code == 201
        profile_id = created_profile.json()["id"]
        activated = client.post(
            f"/api/admin/telegram-proxies/{profile_id}/activate"
        )
        assert activated.status_code == 200
        assert activated.json()["active_id"] == profile_id
        assert activated.json()["enabled"] is True
        assert client.delete(
            f"/api/admin/telegram-proxies/{profile_id}"
        ).status_code == 204
        assert client.post(
            "/api/admin/telegram-proxy/test",
            json={
                "enabled": True,
                "scheme": "socks5",
                "host": "",
                "port": 1080,
            },
        ).status_code == 400

        personal_settings = client.get("/api/settings/model")
        assert personal_settings.status_code == 200
        assert len(personal_settings.json()["available_models"]) == 2
        assert client.patch(
            "/api/settings/model", json={"selected_model": "gpt-4o"}
        ).status_code == 200
        quick_actions = client.get("/api/settings/quick-actions")
        assert quick_actions.status_code == 200
        assert len(quick_actions.json()["actions"]) == 4
        updated_actions = client.put(
            "/api/settings/quick-actions",
            json={
                "actions": [
                    {
                        "label": "Мои продажи",
                        "prompt": "Покажи мои продажи за неделю",
                    }
                ]
            },
        )
        assert updated_actions.status_code == 200
        assert updated_actions.json()["actions"][0]["label"] == "Мои продажи"

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/admin/users").status_code == 401

        assert client.post(
            "/api/auth/login",
            json={
                "email": "sales@example.com",
                "password": "very-secure-sales-password",
            },
        ).status_code == 200
        regular_settings = client.get("/api/settings/model")
        assert regular_settings.status_code == 200
        assert [
            model["code"] for model in regular_settings.json()["available_models"]
        ] == ["gpt-4.1-mini"]
        assert client.patch(
            "/api/settings/model", json={"selected_model": "gpt-4o"}
        ).status_code == 400
        assert len(client.get("/api/settings/quick-actions").json()["actions"]) == 4
        assert client.put(
            "/api/settings/quick-actions",
            json={
                "actions": [
                    {
                        "label": "Мои задачи",
                        "prompt": "Покажи только мои открытые задачи",
                    }
                ]
            },
        ).status_code == 200
        assert client.get("/api/settings/quick-actions").json()["actions"] == [
            {
                "label": "Мои задачи",
                "prompt": "Покажи только мои открытые задачи",
            }
        ]
        reset_actions = client.delete("/api/settings/quick-actions")
        assert reset_actions.status_code == 200
        assert len(reset_actions.json()["actions"]) == 4
        assert client.get("/api/admin/telegram-proxy").status_code == 403

    asyncio.run(database.close())
