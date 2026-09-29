from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import AmoCRMSnapshot


class AmoCRMSnapshotStore:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get(
        self,
        entity_type: str,
        entity_id: int,
        *,
        allow_stale: bool = False,
    ) -> Any | None:
        async with self._session_factory() as session:
            snapshot = await session.get(
                AmoCRMSnapshot,
                {"entity_type": entity_type, "entity_id": entity_id},
            )
        if not snapshot:
            return None
        expires_at = snapshot.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if not allow_stale and expires_at <= datetime.now(timezone.utc):
            return None
        return snapshot.data

    async def put(
        self,
        entity_type: str,
        entity_id: int,
        data: Any,
        *,
        ttl_seconds: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            snapshot = await session.get(
                AmoCRMSnapshot,
                {"entity_type": entity_type, "entity_id": entity_id},
            )
            if snapshot:
                snapshot.data = data
                snapshot.fetched_at = now
                snapshot.expires_at = now + timedelta(seconds=ttl_seconds)
            else:
                session.add(
                    AmoCRMSnapshot(
                        entity_type=entity_type,
                        entity_id=entity_id,
                        data=data,
                        fetched_at=now,
                        expires_at=now + timedelta(seconds=ttl_seconds),
                    )
                )
            await session.commit()

    async def invalidate(self, entity_type: str, entity_id: int) -> None:
        async with self._session_factory() as session:
            snapshot = await session.get(
                AmoCRMSnapshot,
                {"entity_type": entity_type, "entity_id": entity_id},
            )
            if snapshot:
                snapshot.expires_at = datetime.now(timezone.utc)
                await session.commit()
