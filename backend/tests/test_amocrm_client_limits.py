import asyncio

import httpx

from app.config import Settings
from app.services.amocrm_client import AmoCRMClient, date_to_timestamp


def test_static_directories_are_cached_and_concurrent_calls_are_deduplicated() -> None:
    async def scenario() -> None:
        settings = Settings(
            _env_file=None,
            amocrm_domain="example.amocrm.ru",
            amocrm_access_token="token",
            amocrm_requests_per_second=5,
            amocrm_static_cache_seconds=3600,
        )
        client = AmoCRMClient(settings)
        await client._client.aclose()
        calls = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                request=request,
                json={"_embedded": {"users": [{"id": 1, "name": "Менеджер"}]}},
            )

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        results = await asyncio.gather(*(client.get_users() for _ in range(10)))
        results[0][0]["name"] = "Изменено"
        cached = await client.get_users()

        assert calls == 1
        assert all(result[0]["id"] == 1 for result in results)
        assert cached[0]["name"] == "Менеджер"
        await client.close()

    asyncio.run(scenario())


def test_lightweight_lead_analysis_skips_expensive_related_requests() -> None:
    class StubAmoCRM(AmoCRMClient):
        def __init__(self) -> None:
            super().__init__(
                Settings(
                    _env_file=None,
                    amocrm_domain="example.amocrm.ru",
                    amocrm_access_token="token",
                )
            )
            self.expensive_calls = 0

        async def get_lead(self, lead_id: int) -> dict:
            return {
                "id": lead_id,
                "_embedded": {
                    "contacts": [{"id": 2}],
                    "companies": [{"id": 3}],
                },
            }

        async def get_lead_tasks(self, lead_id: int, max_items: int = 100) -> list:
            return []

        async def get_lead_notes(self, lead_id: int, max_items: int = 100) -> list:
            return []

        async def get_pipelines(self) -> list:
            return []

        async def get_lead_events(self, lead_id: int, max_items: int = 100) -> list:
            self.expensive_calls += 1
            return []

        async def get_users(self) -> list:
            self.expensive_calls += 1
            return []

        async def get_contact(self, contact_id: int) -> dict:
            self.expensive_calls += 1
            return {}

        async def get_company(self, company_id: int) -> dict:
            self.expensive_calls += 1
            return {}

    async def scenario() -> None:
        client = StubAmoCRM()
        result = await client.get_lead_full_details(1, lightweight=True)
        assert client.expensive_calls == 0
        assert result["contacts"] == [{"id": 2}]
        assert result["companies"] == [{"id": 3}]
        await client.close()

    asyncio.run(scenario())


def test_requests_are_globally_rate_limited() -> None:
    async def scenario() -> None:
        client = AmoCRMClient(
            Settings(
                _env_file=None,
                amocrm_domain="example.amocrm.ru",
                amocrm_access_token="token",
                amocrm_requests_per_second=5,
            )
        )
        await client._client.aclose()
        started_at: list[float] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            started_at.append(asyncio.get_running_loop().time())
            return httpx.Response(200, request=request, json={})

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await asyncio.gather(
            *(client._request("GET", f"/api/v4/test/{index}") for index in range(4))
        )

        intervals = [
            current - previous
            for previous, current in zip(started_at, started_at[1:])
        ]
        assert len(started_at) == 4
        assert all(interval >= 0.18 for interval in intervals)
        await client.close()

    asyncio.run(scenario())


def test_leads_are_verified_against_requested_period_locally() -> None:
    async def scenario() -> None:
        client = AmoCRMClient(
            Settings(
                _env_file=None,
                amocrm_domain="example.amocrm.ru",
                amocrm_access_token="token",
                amocrm_requests_per_second=5,
            )
        )
        await client._client.aclose()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                request=request,
                json={
                    "_embedded": {
                        "leads": [
                            {
                                "id": 1,
                                "created_at": date_to_timestamp("2025-01-11"),
                            },
                            {
                                "id": 2,
                                "created_at": date_to_timestamp("2026-07-15"),
                            },
                        ]
                    }
                },
            )

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        leads = await client.get_leads(
            date_from="2026-07-01",
            date_to="2026-07-24",
        )

        assert [lead["id"] for lead in leads] == [2]
        await client.close()

    asyncio.run(scenario())


def test_multiple_manager_filter_is_sent_and_verified_locally() -> None:
    async def scenario() -> None:
        client = AmoCRMClient(
            Settings(
                _env_file=None,
                amocrm_domain="example.amocrm.ru",
                amocrm_access_token="token",
                amocrm_requests_per_second=5,
            )
        )
        await client._client.aclose()
        received_manager_ids: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            received_manager_ids.extend(
                request.url.params.get_list("filter[responsible_user_id][]")
            )
            return httpx.Response(
                200,
                request=request,
                json={
                    "_embedded": {
                        "leads": [
                            {"id": 1, "responsible_user_id": 55},
                            {"id": 2, "responsible_user_id": 999},
                        ]
                    }
                },
            )

        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        leads = await client.get_leads(manager_ids=[55, 77])

        assert received_manager_ids == ["55", "77"]
        assert [lead["id"] for lead in leads] == [1]
        await client.close()

    asyncio.run(scenario())
