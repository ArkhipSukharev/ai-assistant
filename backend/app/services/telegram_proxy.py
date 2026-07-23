from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from uuid import uuid4
from typing import Any
from urllib.parse import quote

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.config import Settings
from app.database import AppSetting, AuditLog, Database

logger = logging.getLogger(__name__)

PROXY_SETTING_KEY = "telegram_proxy"
HOST_PATTERN = re.compile(
    r"^(?=.{1,255}$)(?:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?)$"
)
DEFAULT_PROXY = {
    "enabled": False,
    "scheme": "socks5",
    "host": "",
    "port": 1080,
    "username": "",
    "password": "",
}
DEFAULT_STORE = {"version": 2, "enabled": False, "active_id": None, "profiles": []}


class TelegramProxyService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        secret = (
            settings.settings_encryption_key
            or settings.telegram_bot_token
            or settings.web_api_key
            or settings.admin_password
        )
        if not secret:
            secret = "development-only-change-me"
            logger.warning(
                "No settings encryption secret configured; using development fallback"
            )
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    async def get_store(self) -> dict[str, Any]:
        async with self.database.session_factory() as session:
            encrypted = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == PROXY_SETTING_KEY)
            )
        if not encrypted:
            return {**DEFAULT_STORE, "profiles": []}
        try:
            decoded = self._fernet.decrypt(encrypted.encode()).decode()
            payload = json.loads(decoded)
            if "profiles" in payload:
                return {**DEFAULT_STORE, **payload}
            legacy = {**DEFAULT_PROXY, **payload}
            if not legacy.get("host"):
                return {**DEFAULT_STORE, "profiles": []}
            profile = {
                **legacy,
                "id": "legacy",
                "name": "Основной прокси",
            }
            profile.pop("enabled", None)
            return {
                **DEFAULT_STORE,
                "enabled": bool(legacy.get("enabled")),
                "active_id": "legacy",
                "profiles": [profile],
            }
        except (InvalidToken, ValueError, json.JSONDecodeError):
            logger.error("Could not decrypt Telegram proxy settings")
            return {**DEFAULT_STORE, "profiles": []}

    async def get_config(self) -> dict[str, Any]:
        store = await self.get_store()
        active = next(
            (
                profile
                for profile in store["profiles"]
                if profile["id"] == store.get("active_id")
            ),
            None,
        )
        if not active:
            return dict(DEFAULT_PROXY)
        return {
            **DEFAULT_PROXY,
            **active,
            "enabled": bool(store.get("enabled")),
        }

    def validate(self, config: dict[str, Any], *, require_host: bool = False) -> None:
        if config.get("scheme") not in {"http", "socks5"}:
            raise ValueError("Поддерживаются только HTTP и SOCKS5")
        host = str(config.get("host", "")).strip()
        if (config.get("enabled") or require_host) and not host:
            raise ValueError("Укажите адрес прокси")
        if host and not HOST_PATTERN.fullmatch(host):
            raise ValueError("Некорректный адрес прокси")
        port = int(config.get("port", 0))
        if not 1 <= port <= 65535:
            raise ValueError("Порт должен быть от 1 до 65535")
        for field in ("username", "password"):
            if any(char in str(config.get(field, "")) for char in "\r\n"):
                raise ValueError("Логин и пароль содержат недопустимые символы")

    async def merge_update(self, values: dict[str, Any]) -> dict[str, Any]:
        current = await self.get_config()
        password = values.get("password", "")
        if values.get("clear_password"):
            password = ""
        elif not password:
            password = current.get("password", "")
        merged = {
            **current,
            **{
                key: value
                for key, value in values.items()
                if key not in {"password", "clear_password"}
            },
            "password": password,
        }
        merged["host"] = str(merged.get("host", "")).strip()
        merged["username"] = str(merged.get("username", "")).strip()
        self.validate(merged)
        return merged

    async def _save_store(
        self,
        store: dict[str, Any],
        actor_id: int,
        action: str,
        details: dict[str, Any],
    ) -> None:
        encrypted = self._fernet.encrypt(
            json.dumps(store, ensure_ascii=False).encode()
        ).decode()
        async with self.database.session_factory() as session:
            setting = await session.get(AppSetting, PROXY_SETTING_KEY)
            if setting:
                setting.value = encrypted
                setting.updated_by = actor_id
            else:
                session.add(
                    AppSetting(
                        key=PROXY_SETTING_KEY,
                        value=encrypted,
                        updated_by=actor_id,
                    )
                )
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action=action,
                    details=json.dumps(details, ensure_ascii=False),
                )
            )
            await session.commit()

    async def set_config(
        self, values: dict[str, Any], actor_id: int
    ) -> dict[str, Any]:
        config = await self.merge_update(values)
        store = await self.get_store()
        profile_id = store.get("active_id") or str(uuid4())
        profile = {
            key: value
            for key, value in config.items()
            if key != "enabled"
        }
        profile.update(
            {
                "id": profile_id,
                "name": profile.get("name") or "Основной прокси",
            }
        )
        store["profiles"] = [
            profile if item["id"] == profile_id else item
            for item in store["profiles"]
        ]
        if not any(item["id"] == profile_id for item in store["profiles"]):
            store["profiles"].append(profile)
        store["active_id"] = profile_id
        store["enabled"] = bool(config["enabled"])
        await self._save_store(
            store,
            actor_id,
            "settings.telegram_proxy.update",
            {
                "profile_id": profile_id,
                "enabled": store["enabled"],
                "scheme": profile["scheme"],
                "host": profile["host"],
                "port": profile["port"],
            },
        )
        return config

    async def list_profiles(self) -> dict[str, Any]:
        store = await self.get_store()
        return {
            "enabled": bool(store.get("enabled")),
            "active_id": store.get("active_id"),
            "profiles": [
                self.public_profile(profile) for profile in store["profiles"]
            ],
        }

    async def upsert_profile(
        self,
        values: dict[str, Any],
        actor_id: int,
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        store = await self.get_store()
        existing = next(
            (item for item in store["profiles"] if item["id"] == profile_id),
            None,
        )
        if profile_id and not existing:
            raise KeyError("Прокси не найден")
        password = values.get("password", "")
        if values.get("clear_password"):
            password = ""
        elif not password and existing:
            password = existing.get("password", "")
        profile = {
            **(existing or DEFAULT_PROXY),
            **{
                key: value
                for key, value in values.items()
                if key not in {"password", "clear_password", "enabled"}
            },
            "password": password,
            "id": profile_id or str(uuid4()),
        }
        profile["name"] = str(profile.get("name", "")).strip()
        profile["host"] = str(profile.get("host", "")).strip()
        profile["username"] = str(profile.get("username", "")).strip()
        if not profile["name"]:
            raise ValueError("Укажите название прокси")
        self.validate(profile, require_host=True)
        store["profiles"] = [
            profile if item["id"] == profile["id"] else item
            for item in store["profiles"]
        ]
        if not existing:
            store["profiles"].append(profile)
        await self._save_store(
            store,
            actor_id,
            "settings.telegram_proxy.profile.save",
            {
                "profile_id": profile["id"],
                "name": profile["name"],
                "scheme": profile["scheme"],
                "host": profile["host"],
                "port": profile["port"],
            },
        )
        return self.public_profile(profile)

    async def activate_profile(self, profile_id: str, actor_id: int) -> None:
        store = await self.get_store()
        if not any(item["id"] == profile_id for item in store["profiles"]):
            raise KeyError("Прокси не найден")
        store["active_id"] = profile_id
        store["enabled"] = True
        await self._save_store(
            store,
            actor_id,
            "settings.telegram_proxy.profile.activate",
            {"profile_id": profile_id},
        )

    async def disable_proxy(self, actor_id: int) -> None:
        store = await self.get_store()
        store["enabled"] = False
        await self._save_store(
            store,
            actor_id,
            "settings.telegram_proxy.disable",
            {"active_id": store.get("active_id")},
        )

    async def delete_profile(self, profile_id: str, actor_id: int) -> bool:
        store = await self.get_store()
        remaining = [
            item for item in store["profiles"] if item["id"] != profile_id
        ]
        if len(remaining) == len(store["profiles"]):
            return False
        store["profiles"] = remaining
        if store.get("active_id") == profile_id:
            store["active_id"] = remaining[0]["id"] if remaining else None
            store["enabled"] = False
        await self._save_store(
            store,
            actor_id,
            "settings.telegram_proxy.profile.delete",
            {"profile_id": profile_id},
        )
        return True

    @staticmethod
    def public_config(config: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(config.get("enabled")),
            "scheme": config.get("scheme", "socks5"),
            "host": config.get("host", ""),
            "port": int(config.get("port", 1080)),
            "username": config.get("username", ""),
            "password_configured": bool(config.get("password")),
        }

    @staticmethod
    def public_profile(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": profile["id"],
            "name": profile.get("name", "Прокси"),
            "scheme": profile.get("scheme", "socks5"),
            "host": profile.get("host", ""),
            "port": int(profile.get("port", 1080)),
            "username": profile.get("username", ""),
            "password_configured": bool(profile.get("password")),
        }

    async def get_profile(self, profile_id: str) -> dict[str, Any]:
        store = await self.get_store()
        profile = next(
            (item for item in store["profiles"] if item["id"] == profile_id),
            None,
        )
        if not profile:
            raise KeyError("Прокси не найден")
        return profile

    def build_proxy_url(
        self, config: dict[str, Any], *, require_enabled: bool = True
    ) -> str | None:
        if require_enabled and not config.get("enabled"):
            return None
        self.validate(config, require_host=True)
        credentials = ""
        username = config.get("username", "")
        password = config.get("password", "")
        if username:
            credentials = quote(str(username), safe="")
            if password:
                credentials += f":{quote(str(password), safe='')}"
            credentials += "@"
        return (
            f"{config['scheme']}://{credentials}"
            f"{config['host']}:{int(config['port'])}"
        )

    async def test_config(self, values: dict[str, Any]) -> tuple[str, str]:
        if not self.settings.telegram_bot_token:
            raise ValueError("Telegram Bot Token не настроен")
        config = await self.merge_update(values)
        proxy_url = self.build_proxy_url(config, require_enabled=False)
        session = AiohttpSession(proxy=proxy_url)
        bot = Bot(token=self.settings.telegram_bot_token, session=session)
        try:
            me = await bot.get_me()
            return "Подключение к Telegram успешно", me.username or ""
        except Exception as exc:
            message = str(exc)
            password = str(config.get("password", ""))
            if password:
                message = message.replace(password, "***")
            raise ConnectionError(
                f"Telegram недоступен через прокси: {message[:250]}"
            ) from exc
        finally:
            await bot.session.close()

    async def test_profile(self, profile_id: str) -> tuple[str, str]:
        profile = await self.get_profile(profile_id)
        return await self._test_resolved_config(profile)

    async def _test_resolved_config(
        self, config: dict[str, Any]
    ) -> tuple[str, str]:
        if not self.settings.telegram_bot_token:
            raise ValueError("Telegram Bot Token не настроен")
        proxy_url = self.build_proxy_url(config, require_enabled=False)
        session = AiohttpSession(proxy=proxy_url)
        bot = Bot(token=self.settings.telegram_bot_token, session=session)
        try:
            me = await bot.get_me()
            return "Подключение к Telegram успешно", me.username or ""
        except Exception as exc:
            message = str(exc)
            password = str(config.get("password", ""))
            if password:
                message = message.replace(password, "***")
            raise ConnectionError(
                f"Telegram недоступен через прокси: {message[:250]}"
            ) from exc
        finally:
            await bot.session.close()
