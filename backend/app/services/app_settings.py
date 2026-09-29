from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import delete, select

from app.config import Settings
from app.database import (
    AppSetting,
    AuditLog,
    Database,
    UserQuickActions,
    UserModelPreference,
)
from app.services.llm_client import LLMClient

MODEL_SETTING_KEY = "llm_model"
MODEL_CODE_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,100}$")

FALLBACK_MODELS = [
    {
        "code": "gpt-4.1-mini",
        "name": "GPT-4.1 mini",
        "vendor": "OpenAI",
        "description": "Быстрая модель для ежедневных запросов и отчётов",
        "is_light": True,
    },
    {
        "code": "gpt-4o",
        "name": "GPT-4o",
        "vendor": "OpenAI",
        "description": "Более мощная модель для сложной аналитики",
        "is_light": False,
    },
]

DEFAULT_QUICK_ACTIONS = [
    {
        "label": "Сделай отчёт по продажам за месяц",
        "prompt": "Сделай отчёт по продажам за текущий месяц",
    },
    {
        "label": "Покажи конверсию по воронке",
        "prompt": "Покажи конверсию по воронке за текущий месяц",
    },
    {
        "label": "Кто из менеджеров продал больше?",
        "prompt": "Покажи рейтинг менеджеров по сумме продаж за текущий месяц",
    },
    {
        "label": "Какие задачи просрочены?",
        "prompt": "Покажи просроченные невыполненные задачи",
    },
]

LIGHT_MODEL_HINTS = (
    "mini",
    "nano",
    "flash",
    "lite",
    "small",
    "haiku",
)


def is_light_model(model_code: str) -> bool:
    normalized = model_code.lower()
    return any(hint in normalized for hint in LIGHT_MODEL_HINTS)


class AppSettingsService:
    def __init__(
        self, database: Database, settings: Settings, llm: LLMClient
    ) -> None:
        self.database = database
        self.settings = settings
        self.llm = llm

    async def get_model(self) -> str:
        async with self.database.session_factory() as session:
            value = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == MODEL_SETTING_KEY)
            )
            return value or self.settings.llm_model

    async def get_model_for_user(self, user_id: int, role: str) -> str:
        async with self.database.session_factory() as session:
            preference = await session.get(UserModelPreference, user_id)
            if preference:
                if role == "admin" or is_light_model(preference.model_code):
                    return preference.model_code
            default_model = await session.scalar(
                select(AppSetting.value).where(AppSetting.key == MODEL_SETTING_KEY)
            )
        default_model = default_model or self.settings.llm_model
        if role != "admin" and not is_light_model(default_model):
            return FALLBACK_MODELS[0]["code"]
        return default_model

    async def set_model(self, model: str, actor_id: int) -> str:
        model = model.strip()
        if not MODEL_CODE_PATTERN.fullmatch(model):
            raise ValueError("Некорректный код модели")

        async with self.database.session_factory() as session:
            setting = await session.get(AppSetting, MODEL_SETTING_KEY)
            if setting:
                setting.value = model
                setting.updated_by = actor_id
            else:
                setting = AppSetting(
                    key=MODEL_SETTING_KEY,
                    value=model,
                    updated_by=actor_id,
                )
                session.add(setting)
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action="settings.model.update",
                    details=json.dumps({"model": model}, ensure_ascii=False),
                )
            )
            await session.commit()
        return model

    async def set_user_model(self, user_id: int, role: str, model: str) -> str:
        model = model.strip()
        if not MODEL_CODE_PATTERN.fullmatch(model):
            raise ValueError("Некорректный код модели")
        allowed_codes = {
            item["code"] for item in await self.available_models(role=role)
        }
        if model not in allowed_codes:
            raise ValueError("Эта модель недоступна для вашей роли")

        async with self.database.session_factory() as session:
            preference = await session.get(UserModelPreference, user_id)
            if preference:
                preference.model_code = model
            else:
                session.add(
                    UserModelPreference(user_id=user_id, model_code=model)
                )
            session.add(
                AuditLog(
                    actor_user_id=user_id,
                    target_user_id=user_id,
                    action="user.model.update",
                    details=json.dumps({"model": model}, ensure_ascii=False),
                )
            )
            await session.commit()
        return model

    async def get_quick_actions(self, user_id: int) -> list[dict[str, str]]:
        async with self.database.session_factory() as session:
            preference = await session.get(UserQuickActions, user_id)
            return (
                [dict(item) for item in preference.actions]
                if preference
                else [dict(item) for item in DEFAULT_QUICK_ACTIONS]
            )

    async def set_quick_actions(
        self, user_id: int, actions: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        if len(actions) > 8:
            raise ValueError("Можно сохранить не более 8 быстрых кнопок")
        normalized = []
        for item in actions:
            label = item.get("label", "").strip()
            prompt = item.get("prompt", "").strip()
            if not label or not prompt:
                raise ValueError("Название и текст запроса не могут быть пустыми")
            normalized.append({"label": label, "prompt": prompt})

        async with self.database.session_factory() as session:
            preference = await session.get(UserQuickActions, user_id)
            if preference:
                preference.actions = normalized
            else:
                session.add(UserQuickActions(user_id=user_id, actions=normalized))
            session.add(
                AuditLog(
                    actor_user_id=user_id,
                    target_user_id=user_id,
                    action="user.quick_actions.update",
                    details=json.dumps(
                        {"actions_count": len(normalized)}, ensure_ascii=False
                    ),
                )
            )
            await session.commit()
        return normalized

    async def reset_quick_actions(self, user_id: int) -> list[dict[str, str]]:
        async with self.database.session_factory() as session:
            await session.execute(
                delete(UserQuickActions).where(UserQuickActions.user_id == user_id)
            )
            session.add(
                AuditLog(
                    actor_user_id=user_id,
                    target_user_id=user_id,
                    action="user.quick_actions.reset",
                    details="{}",
                )
            )
            await session.commit()
        return [dict(item) for item in DEFAULT_QUICK_ACTIONS]

    async def available_models(self, role: str = "admin") -> list[dict[str, Any]]:
        if not self.settings.llm_api_key or self.settings.llm_api_key == "sk-llm-...":
            models = FALLBACK_MODELS
            if role == "admin":
                return models
            return [item for item in models if item["is_light"]]
        try:
            response = await self.llm.list_models()
            models = []
            for code, item in response.items():
                if not isinstance(item, dict) or not item.get("available", True):
                    continue
                models.append(
                    {
                        "code": code,
                        "name": item.get("name") or code,
                        "vendor": item.get("vendor") or "",
                        "description": (
                            f"Контекст: {item.get('context_window', '—')}; "
                            f"макс. ответ: {item.get('max_output', '—')}"
                        ),
                        "is_light": is_light_model(code),
                    }
                )
            models = sorted(models, key=lambda item: item["name"]) or FALLBACK_MODELS
        except Exception:
            models = FALLBACK_MODELS
        if role == "admin":
            return models
        light_models = [item for item in models if item["is_light"]]
        return light_models or [FALLBACK_MODELS[0]]
