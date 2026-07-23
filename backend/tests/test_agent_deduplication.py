import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.agent import (
    AgentService,
    REPORT_CONTENT_TYPE,
    _compact_history,
    _instructions_with_current_time,
    _serialize_tool_result,
)


class FakeF5AI:
    def __init__(self) -> None:
        self.calls = 0
        self.tools_by_call = []

    async def chat_completion(self, **kwargs) -> dict:
        self.calls += 1
        self.tools_by_call.append(kwargs.get("tools"))
        if self.calls <= 2:
            tool_call = {"name": "analyze_lead", "arguments": {"lead_id": 42}}
            return {
                "finish_reason": "TOOL_CALL",
                "tools_calls": [tool_call, tool_call, tool_call],
            }
        return {
            "finish_reason": "COMPLETE",
            "message": {
                "content": (
                    "Получаю данные amoCRM через analyze_lead.\n"
                    "Получаю данные amoCRM через analyze_lead.\n"
                    "Анализ готов."
                )
            },
        }


class FakeAmoCRM:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def base_url(self) -> str:
        return "https://example.amocrm.ru"

    def entity_url(self, entity: str, entity_id: int | None) -> str | None:
        return (
            f"{self.base_url}/leads/detail/{entity_id}"
            if entity == "lead" and entity_id
            else None
        )

    async def get_lead_full_details(self, lead_id: int) -> dict:
        self.calls += 1
        return {
            "lead": {
                "id": lead_id,
                "name": "Сделка",
                "price": 100,
                "pipeline_id": 1,
                "status_id": 2,
                "responsible_user_id": 3,
            },
            "pipelines": [],
            "users": [],
            "contacts": [],
            "companies": [],
            "tasks": [],
            "notes": [],
            "events": [],
            "errors": {},
        }


class FakeSessions:
    def new_session_id(self) -> str:
        return "session"

    async def get_history(self, _) -> list:
        return []

    async def save_history(self, *_):
        return None


class FakeAppSettings:
    async def get_model_for_user(self, *_):
        return "test-model"


def test_duplicate_tool_calls_are_cached_and_have_one_card() -> None:
    async def scenario() -> None:
        settings = Settings(
            _env_file=None,
            f5ai_api_key="test-key",
            amocrm_domain="example.amocrm.ru",
            amocrm_access_token="test-token",
        )
        f5ai = FakeF5AI()
        amocrm = FakeAmoCRM()
        agent = AgentService(
            settings,
            f5ai,
            amocrm,
            FakeSessions(),
            FakeAppSettings(),
        )

        _, reply, attachments = await agent.chat(
            "Проанализируй сделку 42",
            "session",
            user_id=1,
        )

        assert reply == "Анализ готов."
        assert amocrm.calls == 1
        assert f5ai.calls == 3
        assert f5ai.tools_by_call[-1] is None
        assert len(attachments) == 1
        assert attachments[0].content_type == REPORT_CONTENT_TYPE
        report = json.loads(attachments[0].content)
        assert report["validation"]["status"] == "verified"
        assert report["provenance"]["source"] == "amoCRM"
        assert report["provenance"]["links"][0]["url"].endswith("/leads/detail/42")

    asyncio.run(scenario())


def test_current_moscow_time_is_added_to_instructions() -> None:
    instructions = _instructions_with_current_time(
        datetime(2026, 7, 23, 20, 18, 45, tzinfo=ZoneInfo("Europe/Moscow"))
    )
    assert "2026-07-23 20:18:45 MSK" in instructions
    assert "Europe/Moscow" in instructions


def test_large_history_and_tool_results_are_bounded() -> None:
    history = [
        {"role": "user", "content": f"message-{index}-" + ("x" * 4_000)}
        for index in range(30)
    ]
    compacted = _compact_history(history)
    assert len(compacted) <= 18
    assert sum(len(item["content"]) for item in compacted) <= 36_000
    assert compacted[-1]["content"].startswith("message-29-")

    serialized = _serialize_tool_result(
        {"items": [{"text": "я" * 10_000} for _ in range(100)]}
    )
    assert len(serialized) <= 48_000
    assert len(json.loads(serialized)["items"]) == 31
