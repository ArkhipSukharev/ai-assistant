from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.auth import get_auth_service, require_admin
from app.database import User
from app.models.schemas import (
    AdminSettingsResponse,
    AuditLogResponse,
    CreateUserRequest,
    ReportRunResponse,
    ReportTemplateResponse,
    TelegramProxySettingsResponse,
    TelegramProxyProfileResponse,
    TelegramProxyProfilesResponse,
    TelegramProxyTestResponse,
    UpdateTelegramProxyRequest,
    UpsertTelegramProxyProfileRequest,
    UpdateAdminSettingsRequest,
    UpdateUserRequest,
    UpsertReportTemplateRequest,
    UserResponse,
)
from app.services.app_settings import AppSettingsService
from app.services.auth_service import AuthService
from app.services.telegram_proxy import TelegramProxyService
from app.services.report_schedule import ReportScheduleService
from app.bot.telegram import TelegramBotManager

router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_app_settings_service(request: Request) -> AppSettingsService:
    service = getattr(request.app.state, "app_settings_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Settings service is not ready")
    return service


def get_telegram_proxy_service(request: Request) -> TelegramProxyService:
    service = getattr(request.app.state, "telegram_proxy_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Proxy settings are not ready")
    return service


def get_telegram_manager(request: Request) -> TelegramBotManager | None:
    return getattr(request.app.state, "telegram_manager", None)


def get_report_schedule_service(request: Request) -> ReportScheduleService:
    service = getattr(request.app.state, "report_schedule_service", None)
    if not service:
        raise HTTPException(status_code=503, detail="Report service is not ready")
    return service


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    _: User = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
) -> list[User]:
    return await auth.list_users()


@router.post("/users", response_model=UserResponse, status_code=201)
async def create_user(
    payload: CreateUserRequest,
    admin: User = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    try:
        return await auth.create_user(actor_id=admin.id, **payload.model_dump())
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email или Telegram ID уже используется",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: User = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
) -> User:
    values = payload.model_dump(exclude_unset=True)
    if user_id == admin.id and values.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Нельзя отключить собственный аккаунт")
    if user_id == admin.id and values.get("role") not in {None, "admin"}:
        raise HTTPException(status_code=400, detail="Нельзя снять с себя роль администратора")
    try:
        user = await auth.update_user(user_id, admin.id, values)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email или Telegram ID уже используется",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    return user


@router.get("/audit", response_model=list[AuditLogResponse])
async def audit_logs(
    _: User = Depends(require_admin),
    auth: AuthService = Depends(get_auth_service),
) -> list:
    return await auth.recent_audit_logs()


@router.get("/settings", response_model=AdminSettingsResponse)
async def get_settings(
    _: User = Depends(require_admin),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> AdminSettingsResponse:
    return AdminSettingsResponse(
        selected_model=await service.get_model(),
        available_models=await service.available_models(),
        f5ai_configured=bool(
            service.settings.f5ai_api_key
            and service.settings.f5ai_api_key != "sk-f5ai-..."
        ),
    )


@router.patch("/settings", response_model=AdminSettingsResponse)
async def update_settings(
    payload: UpdateAdminSettingsRequest,
    admin: User = Depends(require_admin),
    service: AppSettingsService = Depends(get_app_settings_service),
) -> AdminSettingsResponse:
    try:
        selected_model = await service.set_model(payload.selected_model, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AdminSettingsResponse(
        selected_model=selected_model,
        available_models=await service.available_models(),
        f5ai_configured=bool(
            service.settings.f5ai_api_key
            and service.settings.f5ai_api_key != "sk-f5ai-..."
        ),
    )


@router.get(
    "/telegram-proxy",
    response_model=TelegramProxySettingsResponse,
)
async def get_telegram_proxy(
    _: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
) -> TelegramProxySettingsResponse:
    return TelegramProxySettingsResponse(
        **service.public_config(await service.get_config())
    )


@router.patch(
    "/telegram-proxy",
    response_model=TelegramProxySettingsResponse,
)
async def update_telegram_proxy(
    payload: UpdateTelegramProxyRequest,
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
    manager: TelegramBotManager | None = Depends(get_telegram_manager),
) -> TelegramProxySettingsResponse:
    try:
        config = await service.set_config(payload.model_dump(), admin.id)
        if manager:
            await manager.restart()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TelegramProxySettingsResponse(**service.public_config(config))


@router.post(
    "/telegram-proxy/test",
    response_model=TelegramProxyTestResponse,
)
async def test_telegram_proxy(
    payload: UpdateTelegramProxyRequest,
    _: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
) -> TelegramProxyTestResponse:
    try:
        message, username = await service.test_config(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TelegramProxyTestResponse(
        success=True,
        message=message,
        bot_username=username,
    )


@router.get(
    "/telegram-proxies",
    response_model=TelegramProxyProfilesResponse,
)
async def list_telegram_proxies(
    _: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
) -> TelegramProxyProfilesResponse:
    return TelegramProxyProfilesResponse(**await service.list_profiles())


@router.post(
    "/telegram-proxies",
    response_model=TelegramProxyProfileResponse,
    status_code=201,
)
async def create_telegram_proxy_profile(
    payload: UpsertTelegramProxyProfileRequest,
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
) -> TelegramProxyProfileResponse:
    try:
        profile = await service.upsert_profile(payload.model_dump(), admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TelegramProxyProfileResponse(**profile)


@router.put(
    "/telegram-proxies/{profile_id}",
    response_model=TelegramProxyProfileResponse,
)
async def update_telegram_proxy_profile(
    profile_id: str,
    payload: UpsertTelegramProxyProfileRequest,
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
    manager: TelegramBotManager | None = Depends(get_telegram_manager),
) -> TelegramProxyProfileResponse:
    try:
        profile = await service.upsert_profile(
            payload.model_dump(), admin.id, profile_id
        )
        current = await service.list_profiles()
        if manager and current["enabled"] and current["active_id"] == profile_id:
            await manager.restart()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TelegramProxyProfileResponse(**profile)


@router.delete("/telegram-proxies/{profile_id}", status_code=204)
async def delete_telegram_proxy_profile(
    profile_id: str,
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
    manager: TelegramBotManager | None = Depends(get_telegram_manager),
) -> None:
    if not await service.delete_profile(profile_id, admin.id):
        raise HTTPException(status_code=404, detail="Прокси не найден")
    if manager:
        await manager.restart()


@router.post(
    "/telegram-proxies/{profile_id}/activate",
    response_model=TelegramProxyProfilesResponse,
)
async def activate_telegram_proxy_profile(
    profile_id: str,
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
    manager: TelegramBotManager | None = Depends(get_telegram_manager),
) -> TelegramProxyProfilesResponse:
    try:
        await service.activate_profile(profile_id, admin.id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    if manager:
        await manager.restart()
    return TelegramProxyProfilesResponse(**await service.list_profiles())


@router.post(
    "/telegram-proxies/disable",
    response_model=TelegramProxyProfilesResponse,
)
async def disable_telegram_proxy(
    admin: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
    manager: TelegramBotManager | None = Depends(get_telegram_manager),
) -> TelegramProxyProfilesResponse:
    await service.disable_proxy(admin.id)
    if manager:
        await manager.restart()
    return TelegramProxyProfilesResponse(**await service.list_profiles())


@router.post(
    "/telegram-proxies/{profile_id}/test",
    response_model=TelegramProxyTestResponse,
)
async def test_telegram_proxy_profile(
    profile_id: str,
    _: User = Depends(require_admin),
    service: TelegramProxyService = Depends(get_telegram_proxy_service),
) -> TelegramProxyTestResponse:
    try:
        message, username = await service.test_profile(profile_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return TelegramProxyTestResponse(
        success=True,
        message=message,
        bot_username=username,
    )


@router.get("/report-templates", response_model=list[ReportTemplateResponse])
async def list_report_templates(
    _: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
) -> list:
    return await service.list_templates()


@router.post(
    "/report-templates",
    response_model=ReportTemplateResponse,
    status_code=201,
)
async def create_report_template(
    payload: UpsertReportTemplateRequest,
    admin: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
):
    try:
        return await service.create_template(payload.model_dump(), admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/report-templates/{template_id}",
    response_model=ReportTemplateResponse,
)
async def update_report_template(
    template_id: int,
    payload: UpsertReportTemplateRequest,
    admin: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
):
    try:
        template = await service.update_template(
            template_id, payload.model_dump(), admin.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not template:
        raise HTTPException(status_code=404, detail="Шаблон отчёта не найден")
    return template


@router.delete("/report-templates/{template_id}", status_code=204)
async def delete_report_template(
    template_id: int,
    admin: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
) -> None:
    if not await service.delete_template(template_id, admin.id):
        raise HTTPException(status_code=404, detail="Шаблон отчёта не найден")


@router.post(
    "/report-templates/{template_id}/run",
    response_model=ReportRunResponse,
)
async def run_report_template(
    template_id: int,
    deliver: bool = False,
    _: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
) -> ReportRunResponse:
    try:
        presentation, sent = await service.run_template(
            template_id, deliver=deliver
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ReportRunResponse(presentation=presentation, telegram_sent=sent)


@router.get("/report-templates/{template_id}/export/{export_format}")
async def export_report_template(
    template_id: int,
    export_format: str,
    _: User = Depends(require_admin),
    service: ReportScheduleService = Depends(get_report_schedule_service),
) -> Response:
    try:
        presentation, _ = await service.run_template(template_id, deliver=False)
        content, filename, content_type = service.export_bytes(
            presentation, export_format
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
