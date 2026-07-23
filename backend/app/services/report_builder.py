from __future__ import annotations

import csv
import io
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


def _parse_date(value: int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d")


def _is_won(status_id: int, pipeline: dict[str, Any]) -> bool:
    for status in pipeline.get("_embedded", {}).get("statuses", []):
        if status.get("id") == status_id:
            return status.get("type") == 142
    return False


def _is_lost(status_id: int, pipeline: dict[str, Any]) -> bool:
    for status in pipeline.get("_embedded", {}).get("statuses", []):
        if status.get("id") == status_id:
            return status.get("type") == 143
    return False


class ReportBuilder:
    @staticmethod
    def _presentation_quality(result: dict[str, Any]) -> dict[str, Any]:
        return {
            "provenance": result.get("_meta", {}),
            "validation": result.get(
                "validation",
                {"status": "not_checked", "checks": [], "warnings": []},
            ),
        }

    def validate_result(
        self, tool_name: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []

        def add(name: str, actual: Any, expected: Any) -> None:
            checks.append(
                {
                    "name": name,
                    "passed": actual == expected,
                    "actual": actual,
                    "expected": expected,
                }
            )

        if tool_name == "generate_sales_report":
            summary = result.get("summary", {})
            groups = result.get("groups", {})
            add(
                "Количество сделок совпадает с суммой групп",
                sum(group.get("count", 0) for group in groups.values()),
                summary.get("total_deals", 0),
            )
            add(
                "Бюджет совпадает с суммой групп",
                sum(group.get("budget", 0) for group in groups.values()),
                summary.get("total_budget", 0),
            )
            closed = summary.get("won_deals", 0) + summary.get("lost_deals", 0)
            checks.append(
                {
                    "name": "Закрытых сделок не больше общего количества",
                    "passed": closed <= summary.get("total_deals", 0),
                    "actual": closed,
                    "expected": f"≤ {summary.get('total_deals', 0)}",
                }
            )
        elif tool_name == "generate_funnel_report":
            add(
                "Количество сделок совпадает с суммой этапов",
                sum(stage.get("count", 0) for stage in result.get("stages", [])),
                result.get("total_deals", 0),
            )
        elif tool_name == "generate_manager_report":
            summary = result.get("summary", {})
            add(
                "Количество менеджеров рассчитано по данным",
                result.get("managers_total", 0),
                summary.get("managers_count", 0),
            )
            for row in result.get("top_managers", []):
                expected_average = (
                    round(row.get("total_budget", 0) / row["deals_count"], 2)
                    if row.get("deals_count")
                    else 0
                )
                add(
                    f"Средний чек: {row.get('manager_name', row.get('manager_id'))}",
                    row.get("average_check", 0),
                    expected_average,
                )
        elif tool_name == "analyze_lead":
            loaded = result.get("loaded_counts", {})
            for section in ("contacts", "companies", "tasks", "notes", "events"):
                add(
                    f"Количество загруженных записей: {section}",
                    len(result.get(section, [])),
                    loaded.get(section, len(result.get(section, []))),
                )
        elif tool_name in {"get_leads", "get_contacts", "get_tasks", "get_users"}:
            checks.append(
                {
                    "name": "Показанная выборка не превышает найденное количество",
                    "passed": len(result.get("items", [])) <= result.get("total", 0),
                    "actual": len(result.get("items", [])),
                    "expected": f"≤ {result.get('total', 0)}",
                }
            )

        failed = [check["name"] for check in checks if not check["passed"]]
        return {
            "status": "verified" if not failed else "warning",
            "checks": checks,
            "warnings": failed,
        }

    def table_to_csv(self, table: dict[str, Any]) -> str:
        output = io.StringIO()
        columns = table.get("columns", [])
        writer = csv.writer(output)
        writer.writerow([column.get("label", column["key"]) for column in columns])
        for row in table.get("rows", []):
            writer.writerow([row.get(column["key"], "") for column in columns])
        return output.getvalue()

    def build_presentation(
        self, tool_name: str, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        if tool_name == "analyze_lead":
            deal = result.get("deal", {})
            resolved = result.get("resolved", {})
            pipeline = resolved.get("pipeline") or {}
            status = resolved.get("current_status") or {}
            manager = resolved.get("responsible_user") or {}
            tasks = result.get("tasks", [])
            communication = result.get("communication_analysis", {})
            now_timestamp = int(datetime.now().timestamp())
            overdue_tasks = sum(
                1
                for task in tasks
                if not task.get("is_completed")
                and task.get("complete_till")
                and task["complete_till"] < now_timestamp
            )
            rows = [
                {"field": "Воронка", "value": pipeline.get("name") or deal.get("pipeline_id")},
                {"field": "Этап", "value": status.get("name") or deal.get("status_id")},
                {
                    "field": "Ответственный",
                    "value": manager.get("name") or deal.get("responsible_user_id"),
                },
                {"field": "Создана", "value": _parse_date(deal.get("created_at"))},
                {"field": "Обновлена", "value": _parse_date(deal.get("updated_at"))},
                {"field": "Закрыта", "value": _parse_date(deal.get("closed_at"))},
                {
                    "field": "Контакты",
                    "value": ", ".join(
                        contact.get("name", str(contact.get("id")))
                        for contact in result.get("contacts", [])
                    )
                    or "—",
                },
                {
                    "field": "Компании",
                    "value": ", ".join(
                        company.get("name", str(company.get("id")))
                        for company in result.get("companies", [])
                    )
                    or "—",
                },
            ]
            for custom_field in deal.get("custom_fields_values") or []:
                values = ", ".join(
                    str(item.get("value", ""))
                    for item in custom_field.get("values", [])
                )
                rows.append(
                    {
                        "field": custom_field.get("field_name")
                        or custom_field.get("field_code")
                        or f"Поле {custom_field.get('field_id')}",
                        "value": values or "—",
                    }
                )
            return {
                **self._presentation_quality(result),
                "kind": "lead",
                "title": f"Сделка #{deal.get('id')} · {deal.get('name', 'Без названия')}",
                "metrics": [
                    {"label": "Бюджет", "value": deal.get("price", 0), "format": "currency"},
                    {"label": "Контакты", "value": len(result.get("contacts", [])), "format": "number"},
                    {"label": "Задачи", "value": len(tasks), "format": "number"},
                    {"label": "Просрочено", "value": overdue_tasks, "format": "number"},
                    {"label": "Примечания", "value": len(result.get("notes", [])), "format": "number"},
                    {"label": "Звонки", "value": communication.get("calls", {}).get("total", 0), "format": "number"},
                    {"label": "Сообщения", "value": communication.get("messages", {}).get("total", 0), "format": "number"},
                ],
                "table": {
                    "columns": [
                        {"key": "field", "label": "Поле"},
                        {"key": "value", "label": "Значение"},
                    ],
                    "rows": rows,
                },
            }

        if tool_name == "find_forgotten_deals":
            items = result.get("items", [])
            rows = [
                {
                    "deal": item.get("name"),
                    "risk": item.get("risk_score"),
                    "level": item.get("risk_level"),
                    "reasons": "; ".join(item.get("reasons") or []),
                    "recommendation": "; ".join(item.get("recommendations") or []),
                    "url": item.get("url"),
                }
                for item in items
            ]
            return {
                **self._presentation_quality(result),
                "kind": "report",
                "title": "Забытые и рискованные сделки",
                "metrics": [
                    {"label": "Найдено", "value": len(rows), "format": "number"},
                    {
                        "label": "Высокий риск",
                        "value": sum(1 for row in rows if row["level"] == "high"),
                        "format": "number",
                    },
                ],
                "table": {
                    "columns": [
                        {"key": "deal", "label": "Сделка"},
                        {"key": "risk", "label": "Риск", "format": "number"},
                        {"key": "reasons", "label": "Причины"},
                        {"key": "recommendation", "label": "Рекомендации"},
                    ],
                    "rows": rows,
                },
                "chart": {
                    "type": "bar",
                    "title": "Риск по сделкам",
                    "labels": [row["deal"] for row in rows[:12]],
                    "values": [row["risk"] for row in rows[:12]],
                    "format": "number",
                },
            }

        period = result.get("period") or {}
        period_label = (
            f" · {period.get('from')} — {period.get('to')}"
            if period.get("from") and period.get("to")
            else ""
        )

        if tool_name == "generate_sales_report":
            summary = result.get("summary", {})
            rows = [
                {"group": name, "count": values["count"], "budget": values["budget"]}
                for name, values in result.get("groups", {}).items()
            ]
            rows.sort(key=lambda row: row["budget"], reverse=True)
            return {
                **self._presentation_quality(result),
                "kind": "report",
                "title": f"Продажи{period_label}",
                "metrics": [
                    {"label": "Сделки", "value": summary.get("total_deals", 0), "format": "number"},
                    {"label": "Бюджет", "value": summary.get("total_budget", 0), "format": "currency"},
                    {"label": "Успешные", "value": summary.get("won_deals", 0), "format": "number"},
                    {"label": "Конверсия", "value": summary.get("win_rate_percent", 0), "format": "percent"},
                ],
                "table": {
                    "columns": [
                        {"key": "group", "label": "Группа"},
                        {"key": "count", "label": "Сделки", "format": "number"},
                        {"key": "budget", "label": "Бюджет", "format": "currency"},
                    ],
                    "rows": rows[:20],
                },
                "chart": {
                    "type": "bar",
                    "title": "Бюджет по группам",
                    "labels": [row["group"] for row in rows[:12]],
                    "values": [row["budget"] for row in rows[:12]],
                    "format": "currency",
                },
            }

        if tool_name == "generate_funnel_report":
            rows = result.get("stages", [])
            return {
                **self._presentation_quality(result),
                "kind": "report",
                "title": f"Воронка «{result.get('pipeline', '')}»{period_label}",
                "metrics": [
                    {"label": "Всего сделок", "value": result.get("total_deals", 0), "format": "number"},
                    {"label": "Этапов", "value": len(rows), "format": "number"},
                ],
                "table": {
                    "columns": [
                        {"key": "stage", "label": "Этап"},
                        {"key": "count", "label": "Сделки", "format": "number"},
                        {"key": "budget", "label": "Бюджет", "format": "currency"},
                        {
                            "key": "conversion_from_top_percent",
                            "label": "Конверсия",
                            "format": "percent",
                        },
                    ],
                    "rows": rows,
                },
                "chart": {
                    "type": "bar",
                    "title": "Сделки по этапам",
                    "labels": [row["stage"] for row in rows],
                    "values": [row["count"] for row in rows],
                    "format": "number",
                },
            }

        if tool_name == "generate_manager_report":
            rows = result.get("top_managers", [])
            return {
                **self._presentation_quality(result),
                "kind": "report",
                "title": f"Результаты менеджеров{period_label}",
                "metrics": [
                    {
                        "label": "Менеджеров",
                        "value": result.get("managers_total", 0),
                        "format": "number",
                    },
                    {
                        "label": "Сделки",
                        "value": result.get("summary", {}).get("deals_count", 0),
                        "format": "number",
                    },
                    {
                        "label": "Бюджет",
                        "value": result.get("summary", {}).get("total_budget", 0),
                        "format": "currency",
                    },
                ],
                "table": {
                    "columns": [
                        {"key": "manager_name", "label": "Менеджер"},
                        {"key": "deals_count", "label": "Сделки", "format": "number"},
                        {"key": "total_budget", "label": "Бюджет", "format": "currency"},
                        {"key": "average_check", "label": "Средний чек", "format": "currency"},
                    ],
                    "rows": rows,
                },
                "chart": {
                    "type": "bar",
                    "title": "Бюджет по менеджерам",
                    "labels": [row["manager_name"] for row in rows],
                    "values": [row["total_budget"] for row in rows],
                    "format": "currency",
                },
            }

        list_tables = {
            "get_leads": (
                "Сделки",
                [
                    {"key": "name", "label": "Сделка"},
                    {"key": "price", "label": "Бюджет", "format": "currency"},
                    {"key": "status_id", "label": "Статус"},
                    {"key": "created_at", "label": "Создана", "format": "timestamp"},
                ],
            ),
            "get_contacts": (
                "Контакты",
                [
                    {"key": "name", "label": "Контакт"},
                    {"key": "responsible_user_id", "label": "Ответственный ID"},
                    {"key": "created_at", "label": "Создан", "format": "timestamp"},
                ],
            ),
            "get_tasks": (
                "Задачи",
                [
                    {"key": "text", "label": "Задача"},
                    {"key": "complete_till", "label": "Срок", "format": "timestamp"},
                    {"key": "responsible_user_id", "label": "Ответственный ID"},
                    {"key": "is_completed", "label": "Выполнена", "format": "boolean"},
                ],
            ),
            "get_users": (
                "Менеджеры",
                [{"key": "name", "label": "Менеджер"}, {"key": "id", "label": "ID"}],
            ),
        }
        if tool_name in list_tables:
            title, columns = list_tables[tool_name]
            return {
                **self._presentation_quality(result),
                "kind": "table",
                "title": title,
                "metrics": [
                    {"label": "Найдено", "value": result.get("total", 0), "format": "number"}
                ],
                "table": {"columns": columns, "rows": result.get("items", [])[:20]},
            }
        return None

    def build_sales_report(
        self,
        leads: list[dict[str, Any]],
        *,
        group_by: str = "status",
        pipelines: list[dict[str, Any]] | None = None,
        users: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        pipelines = pipelines or []
        users = users or []
        pipeline_map = {p["id"]: p for p in pipelines}
        user_map = {u["id"]: u.get("name", str(u["id"])) for u in users}

        total_count = len(leads)
        total_budget = sum(lead.get("price") or 0 for lead in leads)
        won_count = 0
        lost_count = 0
        groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "budget": 0})

        for lead in leads:
            pipeline = pipeline_map.get(lead.get("pipeline_id"))
            status_id = lead.get("status_id")
            if pipeline and status_id:
                if _is_won(status_id, pipeline):
                    won_count += 1
                elif _is_lost(status_id, pipeline):
                    lost_count += 1

            if group_by == "manager":
                key = user_map.get(lead.get("responsible_user_id"), "unknown")
            elif group_by == "day":
                key = _parse_date(lead.get("created_at")) or "unknown"
            elif group_by == "week":
                created = lead.get("created_at")
                if created:
                    dt = datetime.fromtimestamp(created, timezone.utc)
                    key = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
                else:
                    key = "unknown"
            else:
                status_name = str(status_id)
                if pipeline:
                    for status in pipeline.get("_embedded", {}).get("statuses", []):
                        if status.get("id") == status_id:
                            status_name = status.get("name", status_name)
                            break
                key = status_name

            groups[key]["count"] += 1
            groups[key]["budget"] += lead.get("price") or 0

        closed = won_count + lost_count
        win_rate = round(won_count / closed * 100, 2) if closed else 0.0
        avg_check = round(total_budget / total_count, 2) if total_count else 0.0

        return {
            "summary": {
                "total_deals": total_count,
                "total_budget": total_budget,
                "won_deals": won_count,
                "lost_deals": lost_count,
                "win_rate_percent": win_rate,
                "average_check": avg_check,
            },
            "groups": dict(groups),
        }

    def build_funnel_report(
        self,
        leads: list[dict[str, Any]],
        pipelines: list[dict[str, Any]],
        *,
        pipeline_id: int | None = None,
    ) -> dict[str, Any]:
        pipeline = next((p for p in pipelines if p["id"] == pipeline_id), pipelines[0] if pipelines else None)
        if not pipeline:
            return {"error": "Pipeline not found", "stages": {}}

        statuses = pipeline.get("_embedded", {}).get("statuses", [])
        stage_counts = Counter()
        stage_budgets: dict[str, int] = defaultdict(int)

        status_names = {s["id"]: s.get("name", str(s["id"])) for s in statuses}
        for lead in leads:
            if lead.get("pipeline_id") != pipeline["id"]:
                continue
            name = status_names.get(lead.get("status_id"), "unknown")
            stage_counts[name] += 1
            stage_budgets[name] += lead.get("price") or 0

        ordered_stages = []
        for status in sorted(statuses, key=lambda s: s.get("sort", 0)):
            name = status.get("name", str(status["id"]))
            count = stage_counts.get(name, 0)
            ordered_stages.append(
                {
                    "stage": name,
                    "count": count,
                    "budget": stage_budgets.get(name, 0),
                    "type": status.get("type"),
                }
            )

        first_count = ordered_stages[0]["count"] if ordered_stages else 0
        for stage in ordered_stages:
            stage["conversion_from_top_percent"] = (
                round(stage["count"] / first_count * 100, 2) if first_count else 0.0
            )

        return {
            "pipeline": pipeline.get("name"),
            "pipeline_id": pipeline.get("id"),
            "total_deals": sum(stage_counts.values()),
            "stages": ordered_stages,
        }

    def build_manager_report(
        self,
        leads: list[dict[str, Any]],
        users: list[dict[str, Any]],
        *,
        top_n: int = 10,
    ) -> dict[str, Any]:
        user_map = {u["id"]: u.get("name", str(u["id"])) for u in users}
        stats: dict[int, dict[str, Any]] = defaultdict(lambda: {"count": 0, "budget": 0})

        for lead in leads:
            manager_id = lead.get("responsible_user_id")
            if not manager_id:
                continue
            stats[manager_id]["count"] += 1
            stats[manager_id]["budget"] += lead.get("price") or 0

        rows = []
        for manager_id, data in stats.items():
            rows.append(
                {
                    "manager_id": manager_id,
                    "manager_name": user_map.get(manager_id, str(manager_id)),
                    "deals_count": data["count"],
                    "total_budget": data["budget"],
                    "average_check": round(data["budget"] / data["count"], 2) if data["count"] else 0,
                }
            )

        rows.sort(key=lambda row: row["total_budget"], reverse=True)
        return {
            "managers_total": len(rows),
            "summary": {
                "managers_count": len(rows),
                "deals_count": sum(row["deals_count"] for row in rows),
                "total_budget": sum(row["total_budget"] for row in rows),
            },
            "top_managers": rows[:top_n],
        }

    def leads_to_csv(self, leads: list[dict[str, Any]], users: list[dict[str, Any]] | None = None) -> str:
        user_map = {u["id"]: u.get("name", str(u["id"])) for u in (users or [])}
        lines = ["id,name,price,status_id,pipeline_id,manager,created_at,closed_at"]
        for lead in leads:
            manager = user_map.get(lead.get("responsible_user_id"), "")
            lines.append(
                ",".join(
                    [
                        str(lead.get("id", "")),
                        f"\"{(lead.get('name') or '').replace('\"', '')}\"",
                        str(lead.get("price") or 0),
                        str(lead.get("status_id") or ""),
                        str(lead.get("pipeline_id") or ""),
                        f"\"{manager.replace('\"', '')}\"",
                        str(lead.get("created_at") or ""),
                        str(lead.get("closed_at") or ""),
                    ]
                )
            )
        return "\n".join(lines)
