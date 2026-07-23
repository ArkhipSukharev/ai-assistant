import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database import Database, ReportTemplate, User
from app.services.report_exporter import render_pdf, render_xlsx
from app.services.report_schedule import (
    ReportScheduleService,
    calculate_next_run,
    previous_period,
    resolve_period,
)


class FakeAmoCRM:
    base_url = "https://example.amocrm.ru"

    def entity_url(self, entity, entity_id):
        return f"{self.base_url}/leads/detail/{entity_id}" if entity_id else None

    async def get_leads(self, **arguments):
        if arguments["date_from"] == "2026-07-10":
            return [
                {
                    "id": 1,
                    "name": "A",
                    "price": 60,
                    "pipeline_id": 1,
                    "status_id": 10,
                    "responsible_user_id": 1,
                },
                {
                    "id": 2,
                    "name": "B",
                    "price": 40,
                    "pipeline_id": 1,
                    "status_id": 10,
                    "responsible_user_id": 1,
                },
            ]
        return [
            {
                "id": 3,
                "name": "Previous",
                "price": 50,
                "pipeline_id": 1,
                "status_id": 10,
                "responsible_user_id": 1,
            }
        ]

    async def get_pipelines(self):
        return [
            {
                "id": 1,
                "name": "Продажи",
                "_embedded": {
                    "statuses": [{"id": 10, "name": "Успешно", "type": 142}]
                },
            }
        ]

    async def get_users(self):
        return [{"id": 1, "name": "Анна"}]


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_report(self, chat_id, text, presentation, files):
        self.sent.append((chat_id, text, presentation, files))


def template_payload():
    return {
        "name": "Продажи за период",
        "report_type": "sales",
        "period_mode": "fixed",
        "fixed_date_from": "2026-07-10",
        "fixed_date_to": "2026-07-20",
        "comparison_mode": "previous_period",
        "plan_value": 200,
        "pipeline_id": None,
        "manager_id": None,
        "group_by": "status",
        "top_n": 10,
        "schedule_frequency": "manual",
        "schedule_time": "09:00",
        "schedule_weekday": 0,
        "schedule_month_day": 1,
        "telegram_chat_id": 123456,
        "export_formats": ["xlsx", "pdf"],
        "is_enabled": True,
    }


def test_period_and_schedule_calculations():
    assert resolve_period("current_month", today=date(2026, 7, 23)) == (
        date(2026, 7, 1),
        date(2026, 7, 23),
    )
    assert previous_period(date(2026, 7, 10), date(2026, 7, 20)) == (
        date(2026, 6, 29),
        date(2026, 7, 9),
    )
    next_run = calculate_next_run(
        "weekly",
        "09:00",
        0,
        1,
        now=datetime(2026, 7, 23, 21, 0, tzinfo=ZoneInfo("Europe/Moscow")),
    )
    assert next_run.astimezone(ZoneInfo("Europe/Moscow")).isoformat().startswith(
        "2026-07-27T09:00:00"
    )


def test_template_run_comparison_plan_and_exports(tmp_path):
    async def scenario():
        database = Database(f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}")
        await database.create_tables()
        async with database.session_factory() as session:
            session.add(
                User(
                    name="Admin",
                    email="admin@example.com",
                    password_hash="hash",
                    role="admin",
                )
            )
            await session.commit()

        telegram = FakeTelegram()
        service = ReportScheduleService(
            database,
            Settings(_env_file=None),
            FakeAmoCRM(),
            telegram,
        )
        template = await service.create_template(template_payload(), 1)
        presentation, sent = await service.run_template(template.id, deliver=True)

        assert sent is True
        assert presentation["plan"]["actual"] == 100
        assert presentation["plan"]["completion_percent"] == 50
        budget_comparison = next(
            item
            for item in presentation["comparison"]["metrics"]
            if item["label"] == "Бюджет"
        )
        assert budget_comparison["previous"] == 50
        assert budget_comparison["delta_percent"] == 100
        assert telegram.sent[0][0] == 123456
        assert telegram.sent[0][3][0][0].startswith(b"PK")
        assert telegram.sent[0][3][1][0].startswith(b"%PDF")

        stored = await service.get_template(template.id)
        assert stored.last_status == "success"
        assert len(await service.list_templates()) == 1
        assert await service.delete_template(template.id, 1) is True
        await database.close()

    asyncio.run(scenario())


def test_export_renderers():
    presentation = {
        "title": "Тестовый отчёт",
        "metrics": [{"label": "Сделки", "value": 2, "format": "number"}],
        "table": {
            "columns": [{"key": "name", "label": "Название"}],
            "rows": [{"name": "Сделка"}],
        },
        "provenance": {"source": "amoCRM", "fetched_at": "2026-07-23T21:00:00+03:00"},
    }
    assert render_xlsx(presentation).startswith(b"PK")
    assert render_pdf(presentation).startswith(b"%PDF")
