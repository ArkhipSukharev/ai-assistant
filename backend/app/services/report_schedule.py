from __future__ import annotations

import asyncio
import calendar
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from app.config import Settings
from app.database import AuditLog, Database, ReportTemplate, utcnow
from app.services.agent import ToolExecutor
from app.services.amocrm_client import AmoCRMClient
from app.services.chart_renderer import render_chart_png
from app.services.report_builder import ReportBuilder
from app.services.report_exporter import render_pdf, render_xlsx

logger = logging.getLogger(__name__)
MOSCOW = ZoneInfo("Europe/Moscow")
REPORT_TOOLS = {
    "sales": "generate_sales_report",
    "funnel": "generate_funnel_report",
    "managers": "generate_manager_report",
}


def resolve_period(
    mode: str,
    fixed_from: str | None = None,
    fixed_to: str | None = None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    current = today or datetime.now(MOSCOW).date()
    if mode == "current_month":
        return current.replace(day=1), current
    if mode == "previous_month":
        previous_end = current.replace(day=1) - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    if mode == "last_7_days":
        return current - timedelta(days=6), current
    if mode == "last_30_days":
        return current - timedelta(days=29), current
    if mode == "fixed" and fixed_from and fixed_to:
        start, end = date.fromisoformat(fixed_from), date.fromisoformat(fixed_to)
        if start > end:
            raise ValueError("Начало периода не может быть позже окончания")
        return start, end
    raise ValueError("Для фиксированного периода укажите обе даты")


def previous_period(start: date, end: date) -> tuple[date, date]:
    duration = end - start
    previous_end = start - timedelta(days=1)
    return previous_end - duration, previous_end


def calculate_next_run(
    frequency: str,
    schedule_time: str,
    weekday: int,
    month_day: int,
    *,
    now: datetime | None = None,
) -> datetime | None:
    if frequency == "manual":
        return None
    current = (now or datetime.now(MOSCOW)).astimezone(MOSCOW)
    hour, minute = (int(value) for value in schedule_time.split(":"))
    run_time = time(hour=hour, minute=minute, tzinfo=MOSCOW)

    if frequency == "daily":
        candidate = datetime.combine(current.date(), run_time)
        if candidate <= current:
            candidate += timedelta(days=1)
    elif frequency == "weekly":
        days = (weekday - current.weekday()) % 7
        candidate = datetime.combine(current.date() + timedelta(days=days), run_time)
        if candidate <= current:
            candidate += timedelta(days=7)
    elif frequency == "monthly":
        day = min(month_day, calendar.monthrange(current.year, current.month)[1])
        candidate = datetime.combine(current.date().replace(day=day), run_time)
        if candidate <= current:
            year = current.year + (1 if current.month == 12 else 0)
            month = 1 if current.month == 12 else current.month + 1
            day = min(month_day, calendar.monthrange(year, month)[1])
            candidate = datetime.combine(date(year, month, day), run_time)
    else:
        raise ValueError("Неизвестная частота расписания")
    return candidate.astimezone(timezone.utc)


class ReportScheduleService:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        amocrm: AmoCRMClient,
        telegram_manager: Any,
    ) -> None:
        self._database = database
        self._settings = settings
        self._builder = ReportBuilder()
        self._executor = ToolExecutor(amocrm, self._builder)
        self._telegram = telegram_manager
        self._task: asyncio.Task | None = None
        self._run_lock = asyncio.Lock()

    async def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @staticmethod
    def _arguments(payload: dict[str, Any]) -> dict[str, Any]:
        report_type = payload["report_type"]
        if report_type == "sales":
            return {
                key: value
                for key, value in {
                    "pipeline_id": payload.get("pipeline_id"),
                    "manager_id": payload.get("manager_id"),
                    "group_by": payload.get("group_by", "status"),
                }.items()
                if value is not None
            }
        if report_type == "funnel":
            return (
                {"pipeline_id": payload["pipeline_id"]}
                if payload.get("pipeline_id")
                else {}
            )
        return {"top_n": payload.get("top_n", 10)}

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> None:
        resolve_period(
            payload["period_mode"],
            payload.get("fixed_date_from"),
            payload.get("fixed_date_to"),
        )
        if (
            payload["schedule_frequency"] != "manual"
            and payload.get("is_enabled", True)
            and not payload.get("telegram_chat_id")
        ):
            raise ValueError("Для автоматического отчёта укажите Telegram ID получателя")

    async def list_templates(self) -> list[ReportTemplate]:
        async with self._database.session_factory() as session:
            result = await session.execute(
                select(ReportTemplate).order_by(ReportTemplate.created_at.desc())
            )
            return list(result.scalars())

    async def get_template(self, template_id: int) -> ReportTemplate | None:
        async with self._database.session_factory() as session:
            return await session.get(ReportTemplate, template_id)

    async def create_template(
        self, payload: dict[str, Any], actor_id: int
    ) -> ReportTemplate:
        self._validate_payload(payload)
        template = ReportTemplate(
            owner_user_id=actor_id,
            name=payload["name"],
            report_type=payload["report_type"],
            arguments=self._arguments(payload),
            period_mode=payload["period_mode"],
            fixed_date_from=payload.get("fixed_date_from"),
            fixed_date_to=payload.get("fixed_date_to"),
            comparison_mode=payload.get("comparison_mode", "none"),
            plan_value=payload.get("plan_value"),
            schedule_frequency=payload["schedule_frequency"],
            schedule_time=payload["schedule_time"],
            schedule_weekday=payload["schedule_weekday"],
            schedule_month_day=payload["schedule_month_day"],
            telegram_chat_id=payload.get("telegram_chat_id"),
            export_formats=list(dict.fromkeys(payload.get("export_formats", []))),
            is_enabled=payload.get("is_enabled", True),
            next_run_at=calculate_next_run(
                payload["schedule_frequency"],
                payload["schedule_time"],
                payload["schedule_weekday"],
                payload["schedule_month_day"],
            )
            if payload.get("is_enabled", True)
            else None,
        )
        async with self._database.session_factory() as session:
            session.add(template)
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action="report_template_created",
                    details=json.dumps({"name": template.name}, ensure_ascii=False),
                )
            )
            await session.commit()
            await session.refresh(template)
        return template

    async def update_template(
        self, template_id: int, payload: dict[str, Any], actor_id: int
    ) -> ReportTemplate | None:
        self._validate_payload(payload)
        async with self._database.session_factory() as session:
            template = await session.get(ReportTemplate, template_id)
            if not template:
                return None
            template.name = payload["name"]
            template.report_type = payload["report_type"]
            template.arguments = self._arguments(payload)
            template.period_mode = payload["period_mode"]
            template.fixed_date_from = payload.get("fixed_date_from")
            template.fixed_date_to = payload.get("fixed_date_to")
            template.comparison_mode = payload.get("comparison_mode", "none")
            template.plan_value = payload.get("plan_value")
            template.schedule_frequency = payload["schedule_frequency"]
            template.schedule_time = payload["schedule_time"]
            template.schedule_weekday = payload["schedule_weekday"]
            template.schedule_month_day = payload["schedule_month_day"]
            template.telegram_chat_id = payload.get("telegram_chat_id")
            template.export_formats = list(
                dict.fromkeys(payload.get("export_formats", []))
            )
            template.is_enabled = payload.get("is_enabled", True)
            template.next_run_at = (
                calculate_next_run(
                    template.schedule_frequency,
                    template.schedule_time,
                    template.schedule_weekday,
                    template.schedule_month_day,
                )
                if template.is_enabled
                else None
            )
            template.updated_at = utcnow()
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action="report_template_updated",
                    details=json.dumps({"template_id": template_id}),
                )
            )
            await session.commit()
            await session.refresh(template)
            return template

    async def delete_template(self, template_id: int, actor_id: int) -> bool:
        async with self._database.session_factory() as session:
            result = await session.execute(
                delete(ReportTemplate).where(ReportTemplate.id == template_id)
            )
            if not result.rowcount:
                return False
            session.add(
                AuditLog(
                    actor_user_id=actor_id,
                    action="report_template_deleted",
                    details=json.dumps({"template_id": template_id}),
                )
            )
            await session.commit()
            return True

    async def _build_presentation(
        self, template: ReportTemplate
    ) -> dict[str, Any]:
        start, end = resolve_period(
            template.period_mode,
            template.fixed_date_from,
            template.fixed_date_to,
        )
        tool_name = REPORT_TOOLS[template.report_type]
        arguments = {
            **template.arguments,
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
        }
        result = await self._executor.execute(tool_name, arguments)
        presentation = self._builder.build_presentation(tool_name, result)
        if not presentation:
            raise RuntimeError("Не удалось построить представление отчёта")

        if template.comparison_mode == "previous_period":
            comparison_start, comparison_end = previous_period(start, end)
            comparison_result = await self._executor.execute(
                tool_name,
                {
                    **template.arguments,
                    "date_from": comparison_start.isoformat(),
                    "date_to": comparison_end.isoformat(),
                },
            )
            comparison_presentation = self._builder.build_presentation(
                tool_name, comparison_result
            ) or {}
            previous_metrics = {
                metric["label"]: metric
                for metric in comparison_presentation.get("metrics", [])
            }
            comparison_metrics = []
            for metric in presentation.get("metrics", []):
                previous = previous_metrics.get(metric["label"], {}).get("value")
                current = metric.get("value")
                if not isinstance(current, (int, float)) or not isinstance(
                    previous, (int, float)
                ):
                    continue
                comparison_metrics.append(
                    {
                        "label": metric["label"],
                        "current": current,
                        "previous": previous,
                        "delta": current - previous,
                        "delta_percent": (
                            round((current - previous) / abs(previous) * 100, 2)
                            if previous
                            else None
                        ),
                        "format": metric.get("format", "number"),
                    }
                )
            presentation["comparison"] = {
                "period": {
                    "from": comparison_start.isoformat(),
                    "to": comparison_end.isoformat(),
                },
                "metrics": comparison_metrics,
            }

        if template.plan_value is not None:
            if template.report_type == "funnel":
                actual = result.get("total_deals", 0)
                format_name = "number"
            else:
                actual = result.get("summary", {}).get("total_budget", 0)
                format_name = "currency"
            presentation["plan"] = {
                "target": template.plan_value,
                "actual": actual,
                "completion_percent": (
                    round(actual / template.plan_value * 100, 2)
                    if template.plan_value
                    else 0
                ),
                "format": format_name,
            }
        return presentation

    @staticmethod
    def _summary_text(presentation: dict[str, Any]) -> str:
        lines = [f"📊 {presentation.get('title', 'Отчёт')}"]
        for metric in presentation.get("metrics", []):
            lines.append(f"• {metric.get('label')}: {metric.get('value')}")
        plan = presentation.get("plan")
        if plan:
            lines.append(
                f"• План/факт: {plan['actual']} / {plan['target']} "
                f"({plan['completion_percent']}%)"
            )
        provenance = presentation.get("provenance", {})
        lines.append(
            f"Источник: {provenance.get('source', 'amoCRM')} · "
            f"{provenance.get('fetched_at', '')}"
        )
        return "\n".join(lines)

    @staticmethod
    def export_bytes(
        presentation: dict[str, Any], export_format: str
    ) -> tuple[bytes, str, str]:
        if export_format == "xlsx":
            return (
                render_xlsx(presentation),
                "report.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        if export_format == "pdf":
            return render_pdf(presentation), "report.pdf", "application/pdf"
        if export_format == "png":
            content = render_chart_png(presentation)
            if not content:
                raise ValueError("В этом отчёте нет графика")
            return content, "report.png", "image/png"
        raise ValueError("Поддерживаются форматы xlsx, pdf и png")

    async def run_template(
        self, template_id: int, *, deliver: bool = True
    ) -> tuple[dict[str, Any], bool]:
        template = await self.get_template(template_id)
        if not template:
            raise KeyError("Шаблон отчёта не найден")
        try:
            presentation = await self._build_presentation(template)
            sent = False
            if deliver and template.telegram_chat_id:
                files = []
                for export_format in template.export_formats:
                    content, filename, content_type = self.export_bytes(
                        presentation, export_format
                    )
                    files.append((content, filename, content_type))
                await self._telegram.send_report(
                    template.telegram_chat_id,
                    self._summary_text(presentation),
                    presentation,
                    files,
                )
                sent = True
            await self._record_run(template, "success", None)
            return presentation, sent
        except Exception as exc:
            await self._record_run(template, "error", str(exc)[:2_000])
            raise

    async def _record_run(
        self, template: ReportTemplate, status: str, error: str | None
    ) -> None:
        async with self._database.session_factory() as session:
            stored = await session.get(ReportTemplate, template.id)
            if not stored:
                return
            stored.last_run_at = utcnow()
            stored.last_status = status
            stored.last_error = error
            stored.next_run_at = (
                calculate_next_run(
                    stored.schedule_frequency,
                    stored.schedule_time,
                    stored.schedule_weekday,
                    stored.schedule_month_day,
                )
                if stored.is_enabled
                else None
            )
            await session.commit()

    async def _scheduler_loop(self) -> None:
        logger.info("Scheduled report runner started")
        while True:
            try:
                await self.run_due_templates()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled report cycle failed")
            await asyncio.sleep(30)

    async def run_due_templates(self) -> None:
        async with self._run_lock:
            async with self._database.session_factory() as session:
                result = await session.execute(
                    select(ReportTemplate.id).where(
                        ReportTemplate.is_enabled.is_(True),
                        ReportTemplate.next_run_at.is_not(None),
                        ReportTemplate.next_run_at <= utcnow(),
                    )
                )
                due_ids = list(result.scalars())
            for template_id in due_ids:
                try:
                    await self.run_template(template_id, deliver=True)
                except Exception:
                    logger.exception("Scheduled report %s failed", template_id)
