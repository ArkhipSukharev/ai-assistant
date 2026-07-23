import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.amocrm_integration import router as integration_router
from app.config import Settings
from app.database import Database
from app.services.amocrm_insights import AmoCRMInsightService, analyze_communications


class FakeAmoCRM:
    def __init__(self) -> None:
        self.registered = None

    def entity_url(self, entity, entity_id):
        return f"https://example.amocrm.ru/{entity}/{entity_id}"

    async def get_lead_full_details(self, lead_id):
        old = int((datetime.now(timezone.utc) - timedelta(days=12)).timestamp())
        return {
            "lead": {
                "id": lead_id,
                "name": "Забытая сделка",
                "updated_at": old,
                "status_id": 100,
                "pipeline_id": 10,
                "responsible_user_id": 7,
                "price": 50000,
            },
            "pipelines": [
                {"id": 10, "_embedded": {"statuses": [{"id": 100, "name": "Переговоры"}]}}
            ],
            "contacts": [],
            "tasks": [
                {
                    "id": 1,
                    "is_completed": False,
                    "complete_till": old,
                }
            ],
            "notes": [
                {
                    "note_type": "call_out",
                    "created_at": old,
                    "params": {"duration": 65, "phone": "+79990000000"},
                },
                {
                    "note_type": "service_message",
                    "created_at": old,
                    "params": {"text": "Клиент попросил перезвонить"},
                },
            ],
            "events": [],
        }

    async def get_leads(self, **kwargs):
        return [{"id": 42}]

    async def get_webhooks(self):
        return []

    async def register_webhook(self, destination, settings):
        self.registered = (destination, settings)
        return {}


def test_communication_analysis_extracts_calls_and_messages() -> None:
    summary = analyze_communications(
        [
            {
                "note_type": "call_in",
                "created_at": 1_700_000_000,
                "params": {"duration": 120},
            },
            {
                "note_type": "sms_out",
                "created_at": 1_700_000_100,
                "params": {"text": "Коммерческое предложение"},
            },
        ],
        [],
    )
    assert summary["calls"]["total"] == 1
    assert summary["calls"]["total_duration_seconds"] == 120
    assert summary["messages"]["total"] == 1


def test_forgotten_deal_risk_and_webhook_deduplication(tmp_path) -> None:
    async def scenario():
        database = Database(
            f"sqlite+aiosqlite:///{(tmp_path / 'insights.db').as_posix()}"
        )
        await database.create_tables()
        settings = Settings(
            _env_file=None,
            public_base_url="https://assistant.example.com",
            amocrm_webhook_secret="secret",
            forgotten_deal_days=7,
        )
        service = AmoCRMInsightService(
            settings, database.session_factory, FakeAmoCRM()
        )

        insight = await service.analyze_lead(42)
        assert insight["risk_level"] == "high"
        assert insight["communication"]["calls"]["total"] == 1
        assert any("Просрочено" in reason for reason in insight["reasons"])

        listed = await service.list_insights(risk_level="high")
        assert [item["lead_id"] for item in listed] == [42]

        raw = b"leads%5Bupdate%5D%5B0%5D%5Bid%5D=42"
        fields = parse_qs(raw.decode())
        first = await service.ingest_webhook(raw, fields)
        second = await service.ingest_webhook(raw, fields)
        assert first == second
        await service.process_event(first[0])

        await database.close()

    asyncio.run(scenario())


def test_webhook_endpoint_validates_secret_and_accepts_form(tmp_path) -> None:
    database = Database(
        f"sqlite+aiosqlite:///{(tmp_path / 'webhook.db').as_posix()}"
    )
    asyncio.run(database.create_tables())
    settings = Settings(_env_file=None, amocrm_webhook_secret="hook-secret")
    service = AmoCRMInsightService(settings, database.session_factory, FakeAmoCRM())
    app = FastAPI()
    app.state.amocrm_insight_service = service
    app.include_router(integration_router)

    with TestClient(app) as client:
        assert client.post("/api/webhooks/amocrm?secret=wrong").status_code == 403
        response = client.post(
            "/api/webhooks/amocrm?secret=hook-secret",
            content="leads%5Bupdate%5D%5B0%5D%5Bid%5D=42",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        assert response.json() == {"status": "accepted", "events": 1}

    asyncio.run(database.close())
