import pytest

from app.services.report_builder import ReportBuilder
from app.services.chart_renderer import render_chart_png
from app.services.agent import _wants_csv


@pytest.fixture
def builder() -> ReportBuilder:
    return ReportBuilder()


def test_sales_report_summary(builder: ReportBuilder) -> None:
    leads = [
        {"id": 1, "price": 1000, "status_id": 10, "pipeline_id": 1, "responsible_user_id": 1, "created_at": 1700000000},
        {"id": 2, "price": 2000, "status_id": 11, "pipeline_id": 1, "responsible_user_id": 2, "created_at": 1700001000},
    ]
    pipelines = [
        {
            "id": 1,
            "name": "Sales",
            "_embedded": {
                "statuses": [
                    {"id": 10, "name": "New", "type": 0},
                    {"id": 11, "name": "Won", "type": 142},
                ]
            },
        }
    ]
    users = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    report = builder.build_sales_report(leads, group_by="manager", pipelines=pipelines, users=users)

    assert report["summary"]["total_deals"] == 2
    assert report["summary"]["total_budget"] == 3000
    assert report["summary"]["won_deals"] == 1
    assert "Alice" in report["groups"]
    validation = builder.validate_result("generate_sales_report", report)
    assert validation["status"] == "verified"
    assert all(check["passed"] for check in validation["checks"])


def test_funnel_report(builder: ReportBuilder) -> None:
    leads = [
        {"id": 1, "price": 100, "status_id": 10, "pipeline_id": 1},
        {"id": 2, "price": 200, "status_id": 11, "pipeline_id": 1},
    ]
    pipelines = [
        {
            "id": 1,
            "name": "Main",
            "_embedded": {
                "statuses": [
                    {"id": 10, "name": "Lead", "sort": 1, "type": 0},
                    {"id": 11, "name": "Deal", "sort": 2, "type": 0},
                ]
            },
        }
    ]

    report = builder.build_funnel_report(leads, pipelines)

    assert report["pipeline"] == "Main"
    assert len(report["stages"]) == 2
    assert report["stages"][0]["count"] == 1


def test_manager_report(builder: ReportBuilder) -> None:
    leads = [
        {"id": 1, "price": 500, "responsible_user_id": 1},
        {"id": 2, "price": 1500, "responsible_user_id": 2},
        {"id": 3, "price": 300, "responsible_user_id": 2},
    ]
    users = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

    report = builder.build_manager_report(leads, users, top_n=2)

    assert report["managers_total"] == 2
    assert report["top_managers"][0]["manager_name"] == "Bob"
    assert report["top_managers"][0]["total_budget"] == 1800
    assert report["summary"]["deals_count"] == 3
    assert builder.validate_result("generate_manager_report", report)["status"] == "verified"


def test_validation_detects_inconsistent_report(builder: ReportBuilder) -> None:
    result = {
        "summary": {
            "total_deals": 3,
            "total_budget": 500,
            "won_deals": 1,
            "lost_deals": 0,
        },
        "groups": {"Новые": {"count": 2, "budget": 400}},
    }
    validation = builder.validate_result("generate_sales_report", result)
    assert validation["status"] == "warning"
    assert len(validation["warnings"]) == 2


def test_report_presentation_csv_and_png(builder: ReportBuilder) -> None:
    result = {
        "period": {"from": "2026-07-01", "to": "2026-07-22"},
        "managers_total": 2,
        "top_managers": [
            {
                "manager_id": 1,
                "manager_name": "Анна",
                "deals_count": 5,
                "total_budget": 150000,
                "average_check": 30000,
            },
            {
                "manager_id": 2,
                "manager_name": "Иван",
                "deals_count": 3,
                "total_budget": 90000,
                "average_check": 30000,
            },
        ],
    }

    presentation = builder.build_presentation("generate_manager_report", result)

    assert presentation is not None
    assert presentation["chart"]["labels"] == ["Анна", "Иван"]
    assert presentation["table"]["rows"][0]["total_budget"] == 150000
    csv_content = builder.table_to_csv(presentation["table"])
    assert "Менеджер,Сделки,Бюджет,Средний чек" in csv_content
    assert "Анна,5,150000,30000" in csv_content
    png = render_chart_png(presentation)
    assert png is not None
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_csv_is_only_requested_explicitly() -> None:
    assert _wants_csv("Сделай отчёт за месяц") is False
    assert _wants_csv("Сделай отчёт и выгрузи CSV") is True
    assert _wants_csv("Хочу скачать данные") is True


def test_lead_analysis_presentation(builder: ReportBuilder) -> None:
    result = {
        "deal": {
            "id": 42,
            "name": "Крупная сделка",
            "price": 250000,
            "pipeline_id": 1,
            "status_id": 10,
            "responsible_user_id": 7,
            "created_at": 1700000000,
            "custom_fields_values": [
                {
                    "field_name": "Источник",
                    "values": [{"value": "Сайт"}],
                }
            ],
        },
        "resolved": {
            "pipeline": {"id": 1, "name": "Продажи"},
            "current_status": {"id": 10, "name": "Переговоры"},
            "responsible_user": {"id": 7, "name": "Анна"},
        },
        "contacts": [{"id": 8, "name": "Иван"}],
        "companies": [{"id": 9, "name": "ООО Ромашка"}],
        "tasks": [{"id": 1, "is_completed": False, "complete_till": 1}],
        "notes": [{"id": 2}],
    }

    presentation = builder.build_presentation("analyze_lead", result)

    assert presentation is not None
    assert presentation["title"] == "Сделка #42 · Крупная сделка"
    assert presentation["metrics"][0]["value"] == 250000
    assert presentation["metrics"][3]["value"] == 1
    assert {"field": "Источник", "value": "Сайт"} in presentation["table"]["rows"]
