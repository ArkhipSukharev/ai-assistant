from __future__ import annotations

import asyncio
import contextvars
import hashlib
import itertools
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from redis.asyncio import Redis

from app.config import Settings

logger = logging.getLogger(__name__)

_priority: contextvars.ContextVar[int] = contextvars.ContextVar(
    "amocrm_request_priority", default=0
)
_request_budget: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar(
    "amocrm_request_budget", default=None
)


class AmoCRMCircuitOpen(RuntimeError):
    pass


@dataclass(order=True)
class _QueueItem:
    priority: int
    sequence: int
    endpoint: str = field(compare=False)
    operation: Callable[[], Awaitable[Any]] = field(compare=False)
    future: asyncio.Future = field(compare=False)


class AmoCRMRequestCoordinator:
    RATE_LIMIT_SCRIPT = """
local current = redis.call('TIME')
local now_ms = current[1] * 1000 + math.floor(current[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now_ms - 1000)
local count = redis.call('ZCARD', KEYS[1])
if count < tonumber(ARGV[1]) then
  redis.call('ZADD', KEYS[1], now_ms, ARGV[2])
  redis.call('PEXPIRE', KEYS[1], 2000)
  return 0
end
local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
return math.max(1, 1000 - (now_ms - tonumber(first[2])))
"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._redis: Redis | None = None
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue(
            maxsize=settings.amocrm_queue_max_size
        )
        self._sequence = itertools.count()
        self._worker: asyncio.Task | None = None
        self._local_rate_lock = asyncio.Lock()
        self._last_local_request = 0.0
        self._local_circuit_until = 0.0
        self._local_circuit_reason = ""

    async def start(self) -> None:
        if self._worker:
            return
        try:
            redis = Redis.from_url(
                self._settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await redis.ping()
            self._redis = redis
        except Exception:
            logger.exception(
                "Redis unavailable for amoCRM coordinator; using local limiter"
            )
            self._redis = None
        self._worker = asyncio.create_task(self._worker_loop())

    async def close(self) -> None:
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @asynccontextmanager
    async def priority(self, value: int):
        token = _priority.set(max(0, value))
        try:
            yield
        finally:
            _priority.reset(token)

    @asynccontextmanager
    async def request_budget(self, limit: int):
        token = _request_budget.set({"remaining": max(1, limit)})
        try:
            yield
        finally:
            _request_budget.reset(token)

    async def execute(
        self,
        endpoint: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        if not self._worker:
            await self.start()
        budget = _request_budget.get()
        if budget is not None:
            if budget["remaining"] <= 0:
                raise RuntimeError(
                    "Лимит обращений к amoCRM для одного запроса исчерпан. "
                    "Уточните период или запросите более узкий отчёт."
                )
            budget["remaining"] -= 1
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        item = _QueueItem(
            priority=_priority.get(),
            sequence=next(self._sequence),
            endpoint=endpoint,
            operation=operation,
            future=future,
        )
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise RuntimeError(
                "Очередь запросов amoCRM переполнена. Повторите запрос позже."
            ) from exc
        return await asyncio.wait_for(
            future,
            timeout=self._settings.amocrm_queue_timeout_seconds,
        )

    async def _worker_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._ensure_circuit_closed()
                await self._acquire_rate_slot()
                result = await item.operation()
                if not item.future.done():
                    item.future.set_result(result)
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _ensure_circuit_closed(self) -> None:
        if self._redis:
            ttl = await self._redis.ttl("amocrm:circuit")
            if ttl and ttl > 0:
                reason = await self._redis.get("amocrm:circuit")
                raise AmoCRMCircuitOpen(
                    f"Доступ к amoCRM временно приостановлен ({reason or 'защита API'}), "
                    f"повторите через {ttl} сек."
                )
            return
        remaining = self._local_circuit_until - time.monotonic()
        if remaining > 0:
            raise AmoCRMCircuitOpen(
                f"Доступ к amoCRM временно приостановлен "
                f"({self._local_circuit_reason}), повторите через {int(remaining) + 1} сек."
            )

    async def _acquire_rate_slot(self) -> None:
        limit = max(1, min(int(self._settings.amocrm_requests_per_second), 5))
        if self._redis:
            while True:
                member = f"{time.time_ns()}:{next(self._sequence)}"
                wait_ms = await self._redis.eval(
                    self.RATE_LIMIT_SCRIPT,
                    1,
                    "amocrm:rate",
                    limit,
                    member,
                )
                if not wait_ms:
                    return
                await asyncio.sleep(float(wait_ms) / 1000)
        else:
            interval = 1.0 / limit
            async with self._local_rate_lock:
                now = time.monotonic()
                wait_for = interval - (now - self._last_local_request)
                if wait_for > 0:
                    await asyncio.sleep(wait_for)
                self._last_local_request = time.monotonic()

    async def open_circuit(self, seconds: int, reason: str) -> None:
        seconds = max(1, seconds)
        if self._redis:
            await self._redis.set("amocrm:circuit", reason, ex=seconds)
        self._local_circuit_until = time.monotonic() + seconds
        self._local_circuit_reason = reason

    async def record(self, endpoint: str, status_code: int) -> None:
        if not self._redis:
            return
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        endpoint_key = endpoint.split("?", 1)[0]
        key = f"amocrm:metrics:{day}"
        field = f"{status_code}:{endpoint_key}"
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.hincrby(key, field, 1)
            pipe.expire(key, 8 * 86400)
            await pipe.execute()

    async def metrics(self) -> dict[str, Any]:
        circuit_seconds = 0
        circuit_reason = None
        if self._redis:
            ttl = await self._redis.ttl("amocrm:circuit")
            circuit_seconds = max(0, ttl or 0)
            circuit_reason = await self._redis.get("amocrm:circuit")
            day = datetime.now(timezone.utc).strftime("%Y%m%d")
            counters = await self._redis.hgetall(f"amocrm:metrics:{day}")
        else:
            circuit_seconds = max(
                0, int(self._local_circuit_until - time.monotonic())
            )
            circuit_reason = self._local_circuit_reason or None
            counters = {}
        return {
            "queue_size": self._queue.qsize(),
            "queue_limit": self._queue.maxsize,
            "circuit_open": circuit_seconds > 0,
            "circuit_seconds": circuit_seconds,
            "circuit_reason": circuit_reason,
            "requests_today": sum(int(value) for value in counters.values()),
            "by_endpoint_status": counters,
        }

    async def get_cache(
        self,
        key: str,
        *,
        allow_stale: bool = False,
    ) -> Any | None:
        if not self._redis:
            return None
        cache_key = f"amocrm:cache:{hashlib.sha256(key.encode()).hexdigest()}"
        raw = await self._redis.get(cache_key)
        if not raw:
            return None
        payload = json.loads(raw)
        if not allow_stale and payload["fresh_until"] < time.time():
            return None
        return payload["data"]

    async def set_cache(
        self,
        key: str,
        value: Any,
        *,
        fresh_seconds: int,
        stale_seconds: int,
    ) -> None:
        if not self._redis:
            return
        cache_key = f"amocrm:cache:{hashlib.sha256(key.encode()).hexdigest()}"
        payload = {
            "fresh_until": time.time() + fresh_seconds,
            "data": value,
        }
        await self._redis.set(
            cache_key,
            json.dumps(payload, ensure_ascii=False),
            ex=fresh_seconds + stale_seconds,
        )

    async def invalidate_cache(self, key: str) -> None:
        if not self._redis:
            return
        cache_key = f"amocrm:cache:{hashlib.sha256(key.encode()).hexdigest()}"
        await self._redis.delete(cache_key)
