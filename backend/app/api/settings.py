from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import get_current_user
from app.database import User
from app.models.schemas import (
    AdminSettingsResponse,
    QuickActionsResponse,
    UpdateAdminSettingsRequest,
    UpdateQuickActionsRequest,
)
from app.services.app_settings import AppSettingsService

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_app_settings_service(request: Request) -> AppSettingsService:
    service = getattr(request.app.state, "app_settings_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Settings service is not ready")
    return service


def _llm_configured(service: AppSettingsService) -> bool:
    return bool(
        service.settings.llm_api_key
        and service.settings.llm_api_key != "sk-llm-..."
    )


@router.get("/model", response_model=AdminSettingsResponse)
async def get_model_settings(
    user: User = Depends(get_current_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> AdminSettingsResponse:
    return AdminSettingsResponse(
        selected_model=await service.get_model_for_user(user.id, user.role),
        available_models=await service.available_models(role=user.role),
        llm_configured=_llm_configured(service),
    )


@router.patch("/model", response_model=AdminSettingsResponse)
async def update_model_settings(
    payload: UpdateAdminSettingsRequest,
    user: User = Depends(get_current_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> AdminSettingsResponse:
    try:
        selected_model = await service.set_user_model(
            user.id, user.role, payload.selected_model
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AdminSettingsResponse(
        selected_model=selected_model,
        available_models=await service.available_models(role=user.role),
        llm_configured=_llm_configured(service),
    )


@router.get("/quick-actions", response_model=QuickActionsResponse)
async def get_quick_actions(
    user: User = Depends(get_current_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> QuickActionsResponse:
    return QuickActionsResponse(actions=await service.get_quick_actions(user.id))


@router.put("/quick-actions", response_model=QuickActionsResponse)
async def update_quick_actions(
    payload: UpdateQuickActionsRequest,
    user: User = Depends(get_current_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> QuickActionsResponse:
    try:
        actions = await service.set_quick_actions(
            user.id, [item.model_dump() for item in payload.actions]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QuickActionsResponse(actions=actions)


@router.delete("/quick-actions", response_model=QuickActionsResponse)
async def reset_quick_actions(
    user: User = Depends(get_current_user),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> QuickActionsResponse:
    return QuickActionsResponse(actions=await service.reset_quick_actions(user.id))
