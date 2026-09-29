from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx

from app.config import Settings
from app.services.amocrm_coordinator import (
    AmoCRMCircuitOpen,
    AmoCRMRequestCoordinator,
)

if TYPE_CHECKING:
    from app.services.amocrm_snapshot import AmoCRMSnapshotStore

logger = logging.getLogger(__name__)


def date_to_timestamp(value: date | datetime | str, end_of_day: bool = False) -> int:
    if isinstance(value, str):
        parsed = datetime.strptime(value, "%Y-%m-%d")
    elif isinstance(value, date) and not isinstance(value, datetime):
        parsed = datetime.combine(value, datetime.max.time() if end_of_day else datetime.min.time())
    else:
        parsed = value

    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


class AmoCRMClient:
    PAGE_LIMIT = 250
    MAX_RETRIES = 3

    def __init__(
        self,
        settings: Settings,
        snapshot_store: AmoCRMSnapshotStore | None = None,
    ) -> None:
        self._settings = settings
        self._access_token = settings.amocrm_access_token
        self._domain = settings.amocrm_domain.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)
        self._coordinator = AmoCRMRequestCoordinator(settings)
        self._snapshot_store = snapshot_store
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        await self._coordinator.start()

    async def close(self) -> None:
        await self._coordinator.close()
        await self._client.aclose()

    def priority(self, value: int):
        return self._coordinator.priority(value)

    def request_budget(self, limit: int):
        return self._coordinator.request_budget(limit)

    async def coordinator_metrics(self) -> dict[str, Any]:
        return await self._coordinator.metrics()

    async def invalidate_entity(self, entity_type: str, entity_id: int) -> None:
        self._cache.pop(f"{entity_type}:{entity_id}", None)
        if self._snapshot_store:
            await self._snapshot_store.invalidate(entity_type, entity_id)
        paths = {
            "lead": (
                f"/api/v4/leads/{entity_id}",
                {"with": "contacts,loss_reason,catalog_elements,source_id"},
            ),
            "contact": (
                f"/api/v4/contacts/{entity_id}",
                {"with": "leads,customers,catalog_elements"},
            ),
            "company": (
                f"/api/v4/companies/{entity_id}",
                {"with": "contacts,leads,customers,catalog_elements"},
            ),
        }
        target = paths.get(entity_type)
        if target:
            path, params = target
            key = json.dumps(
                ["GET", path, sorted(params.items())],
                ensure_ascii=False,
                default=str,
            )
            await self._coordinator.invalidate_cache(key)

    async def _cached(
        self,
        key: str,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        now = asyncio.get_running_loop().time()
        cached = self._cache.get(key)
        if cached and now - cached[0] < ttl_seconds:
            return deepcopy(cached[1])
        lock = self._cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = asyncio.get_running_loop().time()
            cached = self._cache.get(key)
            if cached and now - cached[0] < ttl_seconds:
                return deepcopy(cached[1])
            value = await loader()
            self._cache[key] = (now, deepcopy(value))
            return value

    async def _snapshot_cached(
        self,
        entity_type: str,
        entity_id: int,
        ttl_seconds: int,
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        if not self._snapshot_store:
            return await loader()
        snapshot = await self._snapshot_store.get(entity_type, entity_id)
        if snapshot is not None:
            return snapshot
        try:
            value = await loader()
        except Exception:
            stale = await self._snapshot_store.get(
                entity_type,
                entity_id,
                allow_stale=True,
            )
            if stale is not None:
                logger.warning(
                    "Serving stale PostgreSQL snapshot for %s:%s",
                    entity_type,
                    entity_id,
                )
                return stale
            raise
        await self._snapshot_store.put(
            entity_type,
            entity_id,
            value,
            ttl_seconds=ttl_seconds,
        )
        return value

    @property
    def base_url(self) -> str:
        return f"https://{self._domain}"

    def entity_url(self, entity: str, entity_id: int | None) -> str | None:
        if not entity_id:
            return None
        paths = {
            "lead": "leads/detail",
            "contact": "contacts/detail",
            "company": "companies/detail",
        }
        path = paths.get(entity)
        return f"{self.base_url}/{path}/{entity_id}" if path else None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        cache_key = json.dumps(
            [method, path, sorted((params or {}).items())],
            ensure_ascii=False,
            default=str,
        )
        cache_seconds = self._cache_seconds(method, path)
        if cache_seconds:
            cached = await self._coordinator.get_cache(cache_key)
            if cached is not None:
                return cached

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self._coordinator.execute(
                    path,
                    lambda: self._client.request(
                        method,
                        url,
                        headers=self._headers,
                        params=params,
                        json=json_data,
                    ),
                )
                await self._coordinator.record(path, response.status_code)

                if response.status_code == 401 and self._settings.amocrm_refresh_token:
                    await self._refresh_access_token()
                    continue

                response.raise_for_status()
                if response.status_code == 204 or not response.content:
                    return {}
                result = response.json()
                if cache_seconds:
                    await self._coordinator.set_cache(
                        cache_key,
                        result,
                        fresh_seconds=cache_seconds,
                        stale_seconds=self._settings.amocrm_stale_cache_seconds,
                    )
                return result

            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 429:
                    await self._coordinator.open_circuit(
                        self._settings.amocrm_circuit_429_seconds,
                        "amoCRM вернул 429",
                    )
                elif exc.response.status_code == 403:
                    await self._coordinator.open_circuit(
                        self._settings.amocrm_circuit_403_seconds,
                        "amoCRM вернул 403",
                    )
                if exc.response.status_code in {403, 429, 500, 502, 503, 504}:
                    if cache_seconds:
                        stale = await self._coordinator.get_cache(
                            cache_key,
                            allow_stale=True,
                        )
                        if stale is not None:
                            logger.warning(
                                "Serving stale amoCRM cache for %s after %s",
                                path,
                                exc.response.status_code,
                            )
                            return stale
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = (
                        float(retry_after)
                        if retry_after and retry_after.replace(".", "", 1).isdigit()
                        else 2 ** (attempt + 1)
                    )
                    await asyncio.sleep(min(max(delay, 1), 30))
                    continue
                raise
            except AmoCRMCircuitOpen:
                if cache_seconds:
                    stale = await self._coordinator.get_cache(
                        cache_key,
                        allow_stale=True,
                    )
                    if stale is not None:
                        logger.warning(
                            "Serving stale amoCRM cache for %s while circuit is open",
                            path,
                        )
                        return stale
                raise
            except httpx.RequestError as exc:
                last_error = exc
                await asyncio.sleep(2 ** attempt)

        if last_error:
            raise last_error
        return {}

    def _cache_seconds(self, method: str, path: str) -> int:
        if method != "GET" or path == "/api/v4/webhooks":
            return 0
        if path in {"/api/v4/users", "/api/v4/leads/pipelines"}:
            return self._settings.amocrm_static_cache_seconds
        if any(
            marker in path
            for marker in ("/notes", "/events", "/tasks", "/contacts/", "/companies/")
        ):
            return self._settings.amocrm_entity_cache_seconds
        return self._settings.amocrm_query_cache_seconds

    async def get_webhooks(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "/api/v4/webhooks")
        return data.get("_embedded", {}).get("webhooks", [])

    async def register_webhook(
        self, destination: str, settings: list[str]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/api/v4/webhooks",
            json_data={"destination": destination, "settings": settings, "sort": 10},
        )

    async def _refresh_access_token(self) -> None:
        if not all(
            [
                self._settings.amocrm_client_id,
                self._settings.amocrm_client_secret,
                self._settings.amocrm_refresh_token,
            ]
        ):
            raise RuntimeError("amoCRM token expired and OAuth refresh is not configured")

        payload = {
            "client_id": self._settings.amocrm_client_id,
            "client_secret": self._settings.amocrm_client_secret,
            "grant_type": "refresh_token",
            "refresh_token": self._settings.amocrm_refresh_token,
            "redirect_uri": self._settings.amocrm_redirect_uri,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/oauth2/access_token",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            self._access_token = data["access_token"]
            logger.info("amoCRM access token refreshed")

    async def _paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        embedded_key: str,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        base_params = dict(params or {})

        while True:
            page_params = {**base_params, "page": page, "limit": self.PAGE_LIMIT}
            data = await self._request("GET", path, params=page_params)
            batch = data.get("_embedded", {}).get(embedded_key, [])
            if not batch:
                break
            items.extend(batch)
            if max_items and len(items) >= max_items:
                return items[:max_items]
            if len(batch) < self.PAGE_LIMIT:
                break
            page += 1
            await asyncio.sleep(0.15)

        return items

    async def get_leads(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        date_field: str = "created_at",
        pipeline_id: int | None = None,
        manager_id: int | None = None,
        manager_ids: list[int] | None = None,
        query: str | None = None,
        with_contacts: bool = True,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date_from:
            params[f"filter[{date_field}][from]"] = date_to_timestamp(date_from)
        if date_to:
            params[f"filter[{date_field}][to]"] = date_to_timestamp(date_to, end_of_day=True)
        if pipeline_id:
            params["filter[pipeline_id]"] = pipeline_id
        if manager_id:
            params["filter[responsible_user_id]"] = manager_id
        elif manager_ids:
            params["filter[responsible_user_id][]"] = manager_ids
        if query:
            params["query"] = query
        if with_contacts:
            params["with"] = "contacts"

        leads = await self._paginate(
            "/api/v4/leads",
            params=params,
            embedded_key="leads",
            max_items=max_items,
        )
        if date_from or date_to:
            from_timestamp = date_to_timestamp(date_from) if date_from else None
            to_timestamp = (
                date_to_timestamp(date_to, end_of_day=True) if date_to else None
            )

            def in_period(lead: dict[str, Any]) -> bool:
                value = lead.get(date_field)
                if not isinstance(value, (int, float)):
                    return False
                return (
                    (from_timestamp is None or value >= from_timestamp)
                    and (to_timestamp is None or value <= to_timestamp)
                )

            leads = [lead for lead in leads if in_period(lead)]
        if manager_ids:
            allowed_manager_ids = {int(value) for value in manager_ids}
            leads = [
                lead
                for lead in leads
                if lead.get("responsible_user_id") in allowed_manager_ids
            ]
        return leads

    async def get_lead(self, lead_id: int) -> dict[str, Any]:
        return await self._cached(
            f"lead:{lead_id}",
            self._settings.amocrm_entity_cache_seconds,
            lambda: self._snapshot_cached(
                "lead",
                lead_id,
                self._settings.amocrm_entity_cache_seconds,
                lambda: self._request(
                    "GET",
                    f"/api/v4/leads/{lead_id}",
                    params={"with": "contacts,loss_reason,catalog_elements,source_id"},
                ),
            ),
        )

    async def get_contact(self, contact_id: int) -> dict[str, Any]:
        return await self._cached(
            f"contact:{contact_id}",
            self._settings.amocrm_entity_cache_seconds,
            lambda: self._snapshot_cached(
                "contact",
                contact_id,
                self._settings.amocrm_entity_cache_seconds,
                lambda: self._request(
                    "GET",
                    f"/api/v4/contacts/{contact_id}",
                    params={"with": "leads,customers,catalog_elements"},
                ),
            ),
        )

    async def get_company(self, company_id: int) -> dict[str, Any]:
        return await self._cached(
            f"company:{company_id}",
            self._settings.amocrm_entity_cache_seconds,
            lambda: self._snapshot_cached(
                "company",
                company_id,
                self._settings.amocrm_entity_cache_seconds,
                lambda: self._request(
                    "GET",
                    f"/api/v4/companies/{company_id}",
                    params={"with": "contacts,leads,customers,catalog_elements"},
                ),
            ),
        )

    async def get_lead_tasks(
        self, lead_id: int, max_items: int = 100
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            "/api/v4/tasks",
            params={
                "filter[entity_id]": lead_id,
                "filter[entity_type]": "leads",
            },
            embedded_key="tasks",
            max_items=max_items,
        )

    async def get_lead_notes(
        self, lead_id: int, max_items: int = 100
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            f"/api/v4/leads/{lead_id}/notes",
            embedded_key="notes",
            max_items=max_items,
        )

    async def get_lead_events(
        self, lead_id: int, max_items: int = 100
    ) -> list[dict[str, Any]]:
        return await self._paginate(
            "/api/v4/events",
            params={
                "filter[entity]": "lead",
                "filter[entity_id]": lead_id,
            },
            embedded_key="events",
            max_items=max_items,
        )

    async def get_lead_full_details(
        self,
        lead_id: int,
        *,
        lightweight: bool = False,
    ) -> dict[str, Any]:
        lead = await self.get_lead(lead_id)
        embedded = lead.get("_embedded", {})
        contact_ids = [
            contact["id"]
            for contact in embedded.get("contacts", [])
            if contact.get("id")
        ]
        company_ids = [
            company["id"]
            for company in embedded.get("companies", [])
            if company.get("id")
        ]

        async def collect(name: str, awaitable) -> tuple[str, Any, str | None]:
            try:
                return name, await awaitable, None
            except Exception as exc:
                logger.warning("Could not load lead %s data: %s", name, exc)
                list_sections = {
                    "tasks", "notes", "events", "pipelines",
                    "users", "contacts", "companies",
                }
                return name, [] if name in list_sections else {}, str(exc)

        requests = [
            collect("tasks", self.get_lead_tasks(lead_id)),
            collect("notes", self.get_lead_notes(lead_id)),
            collect("pipelines", self.get_pipelines()),
        ]
        if lightweight:
            requests.extend(
                [
                    collect("events", asyncio.sleep(0, result=[])),
                    collect("users", asyncio.sleep(0, result=[])),
                    collect("contacts", asyncio.sleep(0, result=embedded.get("contacts", []))),
                    collect("companies", asyncio.sleep(0, result=embedded.get("companies", []))),
                ]
            )
        else:
            requests.extend(
                [
                    collect("events", self.get_lead_events(lead_id)),
                    collect("users", self.get_users()),
                    collect(
                        "contacts",
                        asyncio.gather(
                            *(self.get_contact(item_id) for item_id in contact_ids)
                        ),
                    ),
                    collect(
                        "companies",
                        asyncio.gather(
                            *(self.get_company(item_id) for item_id in company_ids)
                        ),
                    ),
                ]
            )
        related = await asyncio.gather(*requests)
        result: dict[str, Any] = {"lead": lead, "errors": {}}
        for name, value, error in related:
            result[name] = value
            if error:
                result["errors"][name] = error
        return result

    async def get_contacts(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date_from:
            params["filter[created_at][from]"] = date_to_timestamp(date_from)
        if date_to:
            params["filter[created_at][to]"] = date_to_timestamp(date_to, end_of_day=True)
        if query:
            params["query"] = query

        return await self._paginate("/api/v4/contacts", params=params, embedded_key="contacts")

    async def get_tasks(
        self,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        responsible_user_id: int | None = None,
        is_completed: bool | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if date_from:
            params["filter[complete_till][from]"] = date_to_timestamp(date_from)
        if date_to:
            params["filter[complete_till][to]"] = date_to_timestamp(date_to, end_of_day=True)
        if responsible_user_id:
            params["filter[responsible_user_id]"] = responsible_user_id
        if is_completed is not None:
            params["filter[is_completed]"] = 1 if is_completed else 0

        return await self._paginate("/api/v4/tasks", params=params, embedded_key="tasks")

    async def get_pipelines(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            async def fetch() -> list[dict[str, Any]]:
                data = await self._request("GET", "/api/v4/leads/pipelines")
                return data.get("_embedded", {}).get("pipelines", [])

            return await self._snapshot_cached(
                "pipelines",
                0,
                self._settings.amocrm_static_cache_seconds,
                fetch,
            )

        return await self._cached(
            "pipelines",
            self._settings.amocrm_static_cache_seconds,
            load,
        )

    async def get_users(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            async def fetch() -> list[dict[str, Any]]:
                data = await self._request("GET", "/api/v4/users")
                return data.get("_embedded", {}).get("users", [])

            return await self._snapshot_cached(
                "users",
                0,
                self._settings.amocrm_static_cache_seconds,
                fetch,
            )

        return await self._cached(
            "users",
            self._settings.amocrm_static_cache_seconds,
            load,
        )

    async def get_user_groups(self) -> list[dict[str, Any]]:
        async def load() -> list[dict[str, Any]]:
            data = await self._request(
                "GET",
                "/api/v4/account",
                params={"with": "users_groups"},
            )
            return data.get("_embedded", {}).get("users_groups", [])

        return await self._cached(
            "user_groups",
            self._settings.amocrm_static_cache_seconds,
            load,
        )
