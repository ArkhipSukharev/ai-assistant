import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.services.agent import (
    AgentService,
    REPORT_CONTENT_TYPE,
    ToolExecutor,
    _clean_assistant_content,
    _compact_history,
    _instructions_with_current_time,
    _resolve_group_by_name,
    _serialize_tool_result,
    _should_auto_table,
    _wants_visualization,
)
from app.services.report_builder import ReportBuilder


class FakeLLM:
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
        self.users_calls = 0

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

    async def get_leads(self, **kwargs) -> list[dict]:
        self.manager_id = kwargs.get("manager_id")
        return [
            {
                "id": 77,
                "name": "Моя сделка",
                "price": 120000,
                "pipeline_id": 1,
                "status_id": 142,
                "responsible_user_id": self.manager_id or 55,
            }
        ]

    async def get_pipelines(self) -> list[dict]:
        return [
            {
                "id": 1,
                "_embedded": {
                    "statuses": [{"id": 142, "name": "Успешно", "type": 142}]
                },
            }
        ]

    async def get_users(self) -> list[dict]:
        self.users_calls += 1
        return [
            {
                "id": 55,
                "name": "Иван Менеджер",
                "rights": {"group_id": 7, "is_active": True},
            }
        ]

    async def get_user_groups(self) -> list[dict]:
        return [{"id": 7, "name": "Паршагин Александр"}]

    async def get_tasks(self, **kwargs) -> list[dict]:
        assert kwargs["responsible_user_id"] == 55
        return [{"id": 1, "is_completed": True, "complete_till": 1}]


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
            llm_api_key="test-key",
            amocrm_domain="example.amocrm.ru",
            amocrm_access_token="test-token",
        )
        llm = FakeLLM()
        amocrm = FakeAmoCRM()
        agent = AgentService(
            settings,
            llm,
            amocrm,
            FakeSessions(),
            FakeAppSettings(),
        )

        _, reply, attachments = await agent.chat(
            "Проанализируй сделку 42 и покажи карточку",
            "session",
            user_id=1,
        )

        assert reply == "Анализ готов."
        assert amocrm.calls == 1
        assert llm.calls == 3
        assert llm.tools_by_call[-1] is None
        assert len(attachments) == 1
        assert attachments[0].content_type == REPORT_CONTENT_TYPE
        report = json.loads(attachments[0].content)
        assert report["validation"]["status"] == "verified"
        assert report["provenance"]["source"] == "amoCRM"
        assert report["provenance"]["links"][0]["url"].endswith("/leads/detail/42")

    asyncio.run(scenario())


def test_visualization_requires_explicit_request() -> None:
    assert _wants_visualization("Построй график продаж")
    assert _wants_visualization("Покажи данные в таблице")
    assert not _wants_visualization("Проанализируй сделку 42")
    assert not _wants_visualization("Напиши мою статистику за месяц")
    assert not _wants_visualization("Ответь кратко, без визуализации")
    assert not _wants_visualization("Покажи продажи без графика")
    presentation = {"table": {"rows": [{"id": 1}, {"id": 2}]}}
    assert _should_auto_table("Какие сделки найдены?", presentation)
    assert not _should_auto_table("Ответь без таблицы", presentation)
    assert not _should_auto_table(
        "Покажи сделку",
        {"table": {"rows": [{"id": 1}]}},
    )


def test_current_moscow_time_is_added_to_instructions() -> None:
    instructions = _instructions_with_current_time(
        datetime(2026, 7, 23, 20, 18, 45, tzinfo=ZoneInfo("Europe/Moscow"))
    )
    assert "2026-07-23 20:18:45 MSK" in instructions
    assert "Europe/Moscow" in instructions


def test_list_tables_use_status_and_responsible_names() -> None:
    async def scenario() -> None:
        executor = ToolExecutor(FakeAmoCRM(), ReportBuilder())
        result = await executor.execute("get_leads", {})
        item = result["items"][0]
        assert item["status_name"] == "Успешно"
        assert item["responsible_user_name"] == "Иван Менеджер"

        presentation = ReportBuilder().build_presentation("get_leads", result)
        columns = presentation["table"]["columns"]
        keys = {column["key"] for column in columns}
        assert "status_name" in keys
        assert "responsible_user_name" in keys
        assert "status_id" not in keys
        assert "responsible_user_id" not in keys

    asyncio.run(scenario())


def test_department_report_resolves_rop_name_and_builds_zero_report() -> None:
    assert _resolve_group_by_name(
        "Паршагин Алескандр",
        [{"id": 7, "name": "Паршагин Александр"}],
    )["id"] == 7

    class EmptyDepartmentAmo(FakeAmoCRM):
        async def get_leads(self, **kwargs) -> list[dict]:
            return []

    async def scenario() -> None:
        executor = ToolExecutor(EmptyDepartmentAmo(), ReportBuilder())
        result = await executor.execute(
            "generate_department_sales_report",
            {
                "department_name": "Паршагин Алескандр",
                "date_from": "2026-07-01",
                "date_to": "2026-09-30",
            },
        )
        assert result["department"]["name"] == "Паршагин Александр"
        assert result["members"] == [{"id": 55, "name": "Иван Менеджер"}]
        assert result["summary"]["total_deals"] == 0
        assert result["summary"]["total_budget"] == 0
        assert "error" not in result
        presentation = ReportBuilder().build_presentation(
            "generate_department_sales_report",
            result,
        )
        assert presentation["title"].startswith(
            "Продажи отдела «Паршагин Александр»"
        )

    asyncio.run(scenario())


def test_internal_tool_names_are_not_shown_to_user() -> None:
    cleaned = _clean_assistant_content(
        "## **Результат**\n* Нужно использовать communication_analysis "
        "и analyze_lead из TOOL_RESULT."
    )
    assert "communication_analysis" not in cleaned
    assert "analyze_lead" not in cleaned
    assert "TOOL_RESULT" not in cleaned
    assert "#" not in cleaned
    assert "*" not in cleaned
    assert cleaned.startswith("Результат")

    link = _clean_assistant_content(
        "https://example.amocrm.ru/api/v4/leads/42\n"
        "Если нужна информация, уточните запрос."
    )
    assert link == "https://example.amocrm.ru/leads/detail/42"


def test_find_deals_with_communications_checks_multiple_deals() -> None:
    class CommunicationAmoCRM(FakeAmoCRM):
        async def get_lead(self, lead_id: int) -> dict:
            return {
                "id": lead_id,
                "name": "Со звонком" if lead_id == 1 else "Без активности",
            }

        async def get_lead_notes(self, lead_id: int) -> list[dict]:
            if lead_id == 1:
                return [
                    {
                        "note_type": "call_in",
                        "created_at": 1_700_000_000,
                        "params": {"duration": 60},
                    }
                ]
            return []

        async def get_lead_events(self, lead_id: int) -> list[dict]:
            return []

    async def scenario() -> None:
        executor = ToolExecutor(CommunicationAmoCRM(), ReportBuilder())
        result = await executor.execute(
            "find_deals_with_communications",
            {"lead_ids": [1, 2]},
        )
        assert result["scope"] == {"lead_ids": [1, 2]}
        assert result["summary"] == {
            "checked_deals": 2,
            "deals_with_communications": 1,
        }
        assert result["items"][0]["name"] == "Со звонком"
        assert result["items"][0]["calls"] == 1
        assert result["validation"]["status"] == "verified"

    asyncio.run(scenario())


def test_personal_statistics_uses_linked_manager_only() -> None:
    async def scenario() -> None:
        amocrm = FakeAmoCRM()
        executor = ToolExecutor(amocrm, ReportBuilder())
        missing = await executor.execute("get_my_statistics", {})
        assert "не привязан" in missing["error"]

        result = await executor.execute(
            "get_my_statistics",
            {"date_from": "2026-07-01", "date_to": "2026-07-31"},
            amocrm_user_id=55,
            amocrm_user_name="Иван Менеджер",
        )
        assert amocrm.manager_id == 55
        assert amocrm.users_calls == 0
        assert result["manager"]["name"] == "Иван Менеджер"
        assert result["summary"]["total_deals"] == 1
        assert result["summary"]["won_deals"] == 1
        assert result["tasks"]["completed"] == 1
        assert result["validation"]["status"] == "verified"

    asyncio.run(scenario())


def test_forbidden_manager_directory_does_not_break_reports() -> None:
    class ForbiddenUsersAmo(FakeAmoCRM):
        async def get_users(self) -> list[dict]:
            request = httpx.Request("GET", "https://example.amocrm.ru/api/v4/users")
            response = httpx.Response(403, request=request)
            raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    async def scenario() -> None:
        executor = ToolExecutor(ForbiddenUsersAmo(), ReportBuilder())
        result = await executor.execute("get_users", {})
        assert result["total"] == 0
        assert result["validation"]["status"] == "verified"

    asyncio.run(scenario())


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
