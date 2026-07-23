from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class F5AIClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.f5ai_base_url.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "X-Auth-Token": self._settings.f5ai_api_key,
            "Content-Type": "application/json",
        }

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        model: str | None = None,
        instructions: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self._settings.f5ai_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if instructions:
            payload["instructions"] = instructions

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{self._base_url}/v2/chat/completions",
                headers=self._headers,
                json=payload,
            )
            if response.is_error:
                logger.error(
                    "F5AI chat error %s: %s",
                    response.status_code,
                    response.text[:1500],
                )
                raise RuntimeError(
                    f"F5AI отклонил запрос (HTTP {response.status_code})"
                )
            return response.json()

    async def get_balance(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/v2/balance",
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()

    async def list_models(self, model_type: str = "llm") -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self._base_url}/v2/models",
                headers=self._headers,
                params={"type": model_type},
            )
            response.raise_for_status()
            return response.json()
