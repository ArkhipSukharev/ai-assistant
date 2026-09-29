from __future__ import annotations

from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app.api.auth import require_admin
from app.database import User
from app.services.amocrm_insights import AmoCRMInsightService

router = APIRouter(tags=["amoCRM integration"])


def get_insight_service(request: Request) -> AmoCRMInsightService:
    service = getattr(request.app.state, "amocrm_insight_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="amoCRM integration is not ready")
    return service


@router.post("/api/webhooks/amocrm")
async def receive_amocrm_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    secret: str = Query(default=""),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> dict[str, int | str]:
    if not service.validate_webhook_secret(secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    raw_body = await request.body()
    fields = parse_qs(raw_body.decode("utf-8", errors="replace"), keep_blank_values=True)
    event_ids = await service.ingest_webhook(raw_body, fields)
    for event_id in event_ids:
        background_tasks.add_task(service.process_event, event_id)
    return {"status": "accepted", "events": len(event_ids)}


@router.get("/api/admin/amocrm/status")
async def get_amocrm_status(
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> dict:
    try:
        return await service.webhook_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"amoCRM API error: {exc}") from exc


@router.get("/api/admin/amocrm/metrics")
async def get_amocrm_metrics(
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> dict:
    return await service.api_metrics()


@router.get("/api/admin/amocrm/managers")
async def list_amocrm_managers(
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> list[dict]:
    try:
        return await service.list_managers()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"amoCRM API error: {exc}") from exc


@router.post("/api/admin/amocrm/webhook/register")
async def register_amocrm_webhook(
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> dict:
    try:
        return await service.register_webhook()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        detail = exc.response.text[:1000] if isinstance(exc, httpx.HTTPStatusError) else str(exc)
        raise HTTPException(status_code=502, detail=f"amoCRM API error: {detail}") from exc


@router.post("/api/admin/amocrm/scan")
async def scan_forgotten_deals(
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> dict:
    return await service.scan_forgotten_deals()


@router.get("/api/admin/amocrm/insights")
async def list_deal_insights(
    risk_level: str | None = None,
    manager_id: int | None = None,
    limit: int = 50,
    _: User = Depends(require_admin),
    service: AmoCRMInsightService = Depends(get_insight_service),
) -> list[dict]:
    if risk_level and risk_level not in {"low", "medium", "high"}:
        raise HTTPException(status_code=422, detail="Invalid risk_level")
    return await service.list_insights(
        risk_level=risk_level, manager_id=manager_id, limit=limit
    )
