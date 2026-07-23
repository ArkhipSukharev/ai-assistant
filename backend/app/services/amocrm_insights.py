from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.database import AmoCRMWebhookEvent, DealInsight
from app.services.amocrm_client import AmoCRMClient

logger = logging.getLogger(__name__)

WEBHOOK_SETTINGS = [
    "add_lead",
    "update_lead",
    "status_lead",
    "responsible_lead",
    "note_lead",
    "add_task",
    "update_task",
    "add_talk",
    "update_talk",
    "add_message",
    "add_outgoing_message",
]
CALL_TYPES = {"call_in", "call_out"}
MESSAGE_TYPES = {
    "sms_in",
    "sms_out",
    "service_message",
    "extended_service_message",
    "message_cashier",
}
WEBHOOK_KEY_RE = re.compile(
    r"^(?P<entity>[^\[]+)\[(?P<action>[^\]]+)\](?:\[\d+\])?\[(?P<field>id|entity_id|element_id)\]$"
)


def _from_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc) if value else None
    except (TypeError, ValueError, OSError):
        return None


def analyze_communications(
    notes: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    activity_times: list[datetime] = []

    for note in notes:
        note_type = str(note.get("note_type") or "")
        params = note.get("params") or {}
        created_at = _from_timestamp(note.get("created_at") or note.get("updated_at"))
        if created_at:
            activity_times.append(created_at)
        if note_type in CALL_TYPES:
            calls.append(
                {
                    "direction": "incoming" if note_type == "call_in" else "outgoing",
                    "duration_seconds": int(params.get("duration") or 0),
                    "result": params.get("result") or params.get("text"),
                    "phone": params.get("phone"),
                    "responsible": params.get("call_responsible"),
                    "recording_url": params.get("link"),
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        elif note_type in MESSAGE_TYPES:
            text = params.get("text") or params.get("message") or ""
            messages.append(
                {
                    "channel": note_type,
                    "direction": "incoming" if note_type.endswith("_in") else "outgoing",
                    "text": str(text)[:2000],
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        elif note_type == "common":
            comments.append(
                {
                    "text": str(params.get("text") or "")[:2000],
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )

    for event in events:
        created_at = _from_timestamp(event.get("created_at"))
        if created_at:
            activity_times.append(created_at)

    calls.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    messages.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    comments.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    total_duration = sum(item["duration_seconds"] for item in calls)
    answered_calls = sum(1 for item in calls if item["duration_seconds"] > 0)
    last_activity = max(activity_times) if activity_times else None
    return {
        "calls": {
            "total": len(calls),
            "incoming": sum(1 for item in calls if item["direction"] == "incoming"),
            "outgoing": sum(1 for item in calls if item["direction"] == "outgoing"),
            "answered": answered_calls,
            "total_duration_seconds": total_duration,
            "average_duration_seconds": round(total_duration / len(calls)) if calls else 0,
            "recent": calls[:10],
        },
        "messages": {"total": len(messages), "recent": messages[:10]},
        "comments": {"total": len(comments), "recent": comments[:10]},
        "last_activity_at": last_activity.isoformat() if last_activity else None,
    }


class AmoCRMInsightService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker,
        amocrm: AmoCRMClient,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._amocrm = amocrm
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    @property
    def webhook_destination(self) -> str | None:
        base = self._settings.public_base_url.strip().rstrip("/")
        secret = self._settings.amocrm_webhook_secret.strip()
        if not base or not secret:
            return None
        return f"{base}/api/webhooks/amocrm?secret={secret}"

    def validate_webhook_secret(self, secret: str) -> bool:
        expected = self._settings.amocrm_webhook_secret
        return bool(expected) and hmac.compare_digest(secret, expected)

    async def webhook_status(self) -> dict[str, Any]:
        destination = self.webhook_destination
        result: dict[str, Any] = {
            "configured": bool(destination),
            "public_base_url": self._settings.public_base_url or None,
            "destination": (
                f"{destination.split('?')[0]}?secret=***" if destination else None
            ),
            "registered": False,
            "settings": WEBHOOK_SETTINGS,
        }
        if not destination:
            result["message"] = "Укажите PUBLIC_BASE_URL с публичным HTTPS-адресом"
            return result
        if not destination.startswith("https://"):
            result["message"] = "PUBLIC_BASE_URL должен использовать HTTPS"
            return result
        webhooks = await self._amocrm.get_webhooks()
        current = next(
            (item for item in webhooks if item.get("destination") == destination), None
        )
        result["registered"] = bool(current and not current.get("disabled"))
        result["webhook"] = current
        return result

    async def register_webhook(self) -> dict[str, Any]:
        destination = self.webhook_destination
        if not destination or not destination.startswith("https://"):
            raise ValueError("Для регистрации задайте публичный HTTPS PUBLIC_BASE_URL")
        await self._amocrm.register_webhook(destination, WEBHOOK_SETTINGS)
        return await self.webhook_status()

    async def ingest_webhook(
        self, raw_body: bytes, fields: dict[str, list[str]]
    ) -> list[int]:
        base_key = hashlib.sha256(raw_body).hexdigest()
        detected: list[tuple[str, str, int | None]] = []
        for key, values in fields.items():
            match = WEBHOOK_KEY_RE.match(key)
            if not match:
                continue
            for value in values:
                try:
                    entity_id = int(value)
                except (TypeError, ValueError):
                    entity_id = None
                detected.append(
                    (match.group("entity"), match.group("action"), entity_id)
                )
        detected = list(dict.fromkeys(detected))
        concrete = {(entity, action) for entity, action, entity_id in detected if entity_id}
        detected = [
            item for item in detected
            if item[2] is not None or (item[0], item[1]) not in concrete
        ]
        if not detected:
            detected = [("unknown", "unknown", None)]

        payload = {key: value if len(value) > 1 else value[0] for key, value in fields.items()}
        event_ids: list[int] = []
        async with self._session_factory() as session:
            for index, (entity, action, entity_id) in enumerate(detected):
                event_key = hashlib.sha256(f"{base_key}:{index}".encode()).hexdigest()
                existing = await session.scalar(
                    select(AmoCRMWebhookEvent).where(
                        AmoCRMWebhookEvent.event_key == event_key
                    )
                )
                if existing:
                    event_ids.append(existing.id)
                    continue
                event = AmoCRMWebhookEvent(
                    event_key=event_key,
                    event_type=f"{action}_{entity}",
                    entity_type=entity,
                    entity_id=entity_id,
                    payload=payload,
                    status="received",
                )
                session.add(event)
                await session.flush()
                event_ids.append(event.id)
            await session.commit()
        return event_ids

    async def process_event(self, event_id: int) -> None:
        async with self._session_factory() as session:
            event = await session.get(AmoCRMWebhookEvent, event_id)
            if not event or event.status == "processed":
                return
            try:
                if event.entity_id and event.entity_type in {
                    "leads", "lead", "message", "outgoing_message"
                }:
                    await self.analyze_lead(event.entity_id)
                event.status = "processed"
                event.processed_at = datetime.now(timezone.utc)
                event.error = None
            except Exception as exc:
                logger.exception("Failed to process amoCRM webhook event %s", event_id)
                event.status = "failed"
                event.error = str(exc)[:2000]
                event.processed_at = datetime.now(timezone.utc)
            await session.commit()

    async def analyze_lead(self, lead_id: int) -> dict[str, Any] | None:
        details = await self._amocrm.get_lead_full_details(lead_id)
        lead = details.get("lead") or {}
        if not lead:
            return None
        pipelines = details.get("pipelines") or []
        status = next(
            (
                status
                for pipeline in pipelines
                for status in pipeline.get("_embedded", {}).get("statuses", [])
                if status.get("id") == lead.get("status_id")
            ),
            {},
        )
        if lead.get("status_id") in {142, 143} or status.get("id") in {142, 143}:
            async with self._session_factory() as session:
                await session.execute(
                    delete(DealInsight).where(DealInsight.lead_id == lead_id)
                )
                await session.commit()
            return None

        now = datetime.now(timezone.utc)
        communication = analyze_communications(
            details.get("notes", []), details.get("events", [])
        )
        webhook_messages = await self._get_webhook_messages(lead_id)
        if webhook_messages:
            combined = webhook_messages + communication["messages"]["recent"]
            combined.sort(key=lambda item: item.get("created_at") or "", reverse=True)
            communication["messages"]["total"] += len(webhook_messages)
            communication["messages"]["recent"] = combined[:10]
            message_times = [
                datetime.fromisoformat(item["created_at"])
                for item in webhook_messages
                if item.get("created_at")
            ]
            if message_times:
                current = (
                    datetime.fromisoformat(communication["last_activity_at"])
                    if communication.get("last_activity_at")
                    else None
                )
                communication["last_activity_at"] = max(
                    message_times + ([current] if current else [])
                ).isoformat()
        updated_at = _from_timestamp(lead.get("updated_at")) or now
        communication_at = datetime.fromisoformat(communication["last_activity_at"]) if communication.get("last_activity_at") else None
        last_activity = max(filter(None, [updated_at, communication_at]), default=updated_at)
        inactivity_days = max(0, (now - last_activity).days)
        tasks = details.get("tasks", [])
        open_tasks = [task for task in tasks if not task.get("is_completed")]
        overdue_tasks = [
            task
            for task in open_tasks
            if (_from_timestamp(task.get("complete_till")) or now) < now
        ]
        contacts = details.get("contacts", [])
        score = min(55, inactivity_days * 5)
        reasons: list[str] = []
        recommendations: list[str] = []
        if inactivity_days >= self._settings.forgotten_deal_days:
            reasons.append(f"Нет активности {inactivity_days} дн.")
            recommendations.append("Связаться с клиентом и зафиксировать результат контакта")
        if overdue_tasks:
            score += 25
            reasons.append(f"Просрочено задач: {len(overdue_tasks)}")
            recommendations.append("Закрыть или перенести просроченные задачи")
        elif not open_tasks:
            score += 20
            reasons.append("Нет запланированной задачи")
            recommendations.append("Поставить следующую задачу с конкретным сроком")
        if not contacts:
            score += 15
            reasons.append("Не привязан контакт")
            recommendations.append("Добавить контакт и проверить телефон или email")
        if communication["calls"]["total"] + communication["messages"]["total"] == 0:
            score += 10
            reasons.append("Не найдено звонков или сообщений")
            recommendations.append("Выбрать канал связи и начать коммуникацию")
        score = min(100, score)
        level = "high" if score >= 70 else "medium" if score >= 40 else "low"
        insight_data = {
            "lead_id": lead_id,
            "name": lead.get("name") or f"Сделка #{lead_id}",
            "url": self._amocrm.entity_url("lead", lead_id) or "",
            "manager_id": lead.get("responsible_user_id"),
            "risk_score": score,
            "risk_level": level,
            "reasons": reasons,
            "recommendations": list(dict.fromkeys(recommendations)),
            "communication": communication,
            "last_activity_at": last_activity,
            "analyzed_at": now,
            "source": {
                "status_id": lead.get("status_id"),
                "pipeline_id": lead.get("pipeline_id"),
                "price": lead.get("price"),
                "inactivity_days": inactivity_days,
                "open_tasks": len(open_tasks),
                "overdue_tasks": len(overdue_tasks),
            },
        }
        async with self._session_factory() as session:
            insight = await session.get(DealInsight, lead_id)
            if insight is None:
                insight = DealInsight(**insight_data)
                session.add(insight)
            else:
                for key, value in insight_data.items():
                    setattr(insight, key, value)
            await session.commit()
        return self._serialize_insight(insight_data)

    async def _get_webhook_messages(self, lead_id: int) -> list[dict[str, Any]]:
        query = (
            select(AmoCRMWebhookEvent)
            .where(
                AmoCRMWebhookEvent.entity_id == lead_id,
                AmoCRMWebhookEvent.entity_type.in_(["message", "outgoing_message"]),
            )
            .order_by(AmoCRMWebhookEvent.received_at.desc())
            .limit(100)
        )
        async with self._session_factory() as session:
            events = (await session.scalars(query)).all()
        messages = []
        for event in events:
            text = next(
                (
                    value for key, value in event.payload.items()
                    if key.endswith("[text]")
                ),
                "",
            )
            timestamp = next(
                (
                    value for key, value in event.payload.items()
                    if key.endswith("[created_at]")
                ),
                None,
            )
            created_at = _from_timestamp(timestamp)
            messages.append(
                {
                    "channel": next(
                        (
                            value for key, value in event.payload.items()
                            if key.endswith("[origin]")
                        ),
                        "chat",
                    ),
                    "direction": (
                        "outgoing"
                        if event.entity_type == "outgoing_message"
                        else "incoming"
                    ),
                    "text": str(text)[:2000],
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        return messages

    async def scan_forgotten_deals(self) -> dict[str, Any]:
        cutoff = (datetime.now(timezone.utc) - timedelta(
            days=self._settings.forgotten_deal_days
        )).date().isoformat()
        leads = await self._amocrm.get_leads(
            date_to=cutoff,
            date_field="updated_at",
            max_items=self._settings.insight_scan_max_leads,
        )
        semaphore = asyncio.Semaphore(3)

        async def analyze(lead: dict[str, Any]) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    return await self.analyze_lead(int(lead["id"]))
                except Exception:
                    logger.exception("Failed to analyze lead %s", lead.get("id"))
                    return None

        results = await asyncio.gather(*(analyze(lead) for lead in leads))
        insights = [item for item in results if item]
        return {
            "scanned": len(leads),
            "found": len(insights),
            "high_risk": sum(1 for item in insights if item["risk_level"] == "high"),
            "cutoff": cutoff,
        }

    async def list_insights(
        self,
        *,
        risk_level: str | None = None,
        manager_id: int | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        query = select(DealInsight)
        if risk_level:
            query = query.where(DealInsight.risk_level == risk_level)
        if manager_id:
            query = query.where(DealInsight.manager_id == manager_id)
        query = query.order_by(
            DealInsight.risk_score.desc(), DealInsight.analyzed_at.desc()
        ).limit(min(max(limit, 1), 200))
        async with self._session_factory() as session:
            items = (await session.scalars(query)).all()
        return [self._serialize_insight(item) for item in items]

    @staticmethod
    def _serialize_insight(item: DealInsight | dict[str, Any]) -> dict[str, Any]:
        def get(name: str) -> Any:
            return item.get(name) if isinstance(item, dict) else getattr(item, name)

        return {
            "lead_id": get("lead_id"),
            "name": get("name"),
            "url": get("url"),
            "manager_id": get("manager_id"),
            "risk_score": get("risk_score"),
            "risk_level": get("risk_level"),
            "reasons": get("reasons"),
            "recommendations": get("recommendations"),
            "communication": get("communication"),
            "source": get("source"),
            "last_activity_at": get("last_activity_at").isoformat() if get("last_activity_at") else None,
            "analyzed_at": get("analyzed_at").isoformat() if get("analyzed_at") else None,
        }

    async def start(self) -> None:
        if self._task:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _scheduler_loop(self) -> None:
        interval = max(5, self._settings.insight_scan_interval_minutes) * 60
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                try:
                    await self.scan_forgotten_deals()
                except Exception:
                    logger.exception("Scheduled amoCRM insight scan failed")
