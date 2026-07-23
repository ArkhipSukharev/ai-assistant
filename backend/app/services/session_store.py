from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

import redis.asyncio as redis

from app.config import Settings

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: redis.Redis | None = None
        self._memory: dict[str, list[dict[str, Any]]] = {}

    async def connect(self) -> bool:
        client: redis.Redis | None = None
        try:
            client = redis.from_url(
                self._settings.redis_url,
                decode_responses=True,
                health_check_interval=30,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            await client.ping()
            self._redis = client
            return True
        except Exception as exc:
            if client:
                await client.aclose()
            self._redis = None
            if self._settings.session_store_require_redis:
                raise RuntimeError("Redis is required but unavailable") from exc
            logger.warning("Redis unavailable, using in-memory sessions: %s", exc)
            return False

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    @property
    def is_redis_connected(self) -> bool:
        return self._redis is not None

    async def ping(self) -> bool:
        if not self._redis:
            return False
        try:
            return bool(await self._redis.ping())
        except Exception:
            return False

    def _key(self, session_id: str) -> str:
        return f"chat:session:{session_id}"

    async def get_history(self, session_id: str) -> list[dict[str, Any]]:
        if self._redis:
            raw = await self._redis.get(self._key(session_id))
            if not raw:
                return []
            return json.loads(raw)

        return list(self._memory.get(session_id, []))

    async def save_history(self, session_id: str, history: list[dict[str, Any]]) -> None:
        trimmed = history[-40:]
        if self._redis:
            await self._redis.set(self._key(session_id), json.dumps(trimmed, ensure_ascii=False), ex=86400 * 7)
            return
        self._memory[session_id] = trimmed

    async def clear(self, session_id: str) -> None:
        if self._redis:
            await self._redis.delete(self._key(session_id))
        self._memory.pop(session_id, None)

    def new_session_id(self) -> str:
        return str(uuid4())
