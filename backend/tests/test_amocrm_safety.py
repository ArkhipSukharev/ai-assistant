import asyncio

import pytest

from app.config import Settings
from app.database import Database
from app.services.amocrm_coordinator import (
    AmoCRMCircuitOpen,
    AmoCRMRequestCoordinator,
)
from app.services.amocrm_snapshot import AmoCRMSnapshotStore


def test_priority_queue_and_per_request_budget() -> None:
    asyncio.run(_test_priority_queue_and_per_request_budget())


async def _test_priority_queue_and_per_request_budget() -> None:
    coordinator = AmoCRMRequestCoordinator(
        Settings(amocrm_requests_per_second=5, amocrm_queue_max_size=10)
    )
    placeholder = asyncio.create_task(asyncio.sleep(60))
    coordinator._worker = placeholder
    order: list[str] = []

    async def submit(name: str, priority: int):
        async with coordinator.priority(priority):
            return await coordinator.execute(
                f"/{name}",
                lambda: _record(order, name),
            )

    background = asyncio.create_task(submit("background", 2))
    webhook = asyncio.create_task(submit("webhook", 1))
    chat = asyncio.create_task(submit("chat", 0))
    await asyncio.sleep(0)
    placeholder.cancel()
    await asyncio.gather(placeholder, return_exceptions=True)
    coordinator._worker = asyncio.create_task(coordinator._worker_loop())

    assert await asyncio.gather(background, webhook, chat) == [
        "background",
        "webhook",
        "chat",
    ]
    assert order == ["chat", "webhook", "background"]

    async with coordinator.request_budget(1):
        assert await coordinator.execute("/one", lambda: _record([], "ok")) == "ok"
        with pytest.raises(RuntimeError, match="Лимит обращений"):
            await coordinator.execute("/two", lambda: _record([], "no"))
    await coordinator.close()


def test_local_circuit_breaker_stops_requests() -> None:
    asyncio.run(_test_local_circuit_breaker_stops_requests())


async def _test_local_circuit_breaker_stops_requests() -> None:
    coordinator = AmoCRMRequestCoordinator(Settings())
    coordinator._worker = asyncio.create_task(coordinator._worker_loop())
    await coordinator.open_circuit(30, "test 429")
    with pytest.raises(AmoCRMCircuitOpen, match="test 429"):
        await coordinator.execute("/api/v4/leads", lambda: _record([], "no"))
    await coordinator.close()


def test_postgresql_style_snapshot_has_fresh_and_stale_modes(tmp_path) -> None:
    asyncio.run(_test_postgresql_style_snapshot_has_fresh_and_stale_modes(tmp_path))


async def _test_postgresql_style_snapshot_has_fresh_and_stale_modes(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'snapshot.db'}")
    await database.create_tables()
    store = AmoCRMSnapshotStore(database.session_factory)

    await store.put("lead", 42, {"id": 42, "name": "Deal"}, ttl_seconds=60)
    assert await store.get("lead", 42) == {"id": 42, "name": "Deal"}

    await store.invalidate("lead", 42)
    assert await store.get("lead", 42) is None
    assert await store.get("lead", 42, allow_stale=True) == {
        "id": 42,
        "name": "Deal",
    }
    await database.close()


async def _record(target: list[str], value: str) -> str:
    target.append(value)
    return value
