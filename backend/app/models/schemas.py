from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    session_id: str | None = None
    conversation_id: int | None = None
    message: str = Field(..., min_length=1, max_length=8000)


class ChatAttachment(BaseModel):
    filename: str
    content_type: str
    content: str


class ChatResponse(BaseModel):
    session_id: str
    conversation_id: int
    title: str
    reply: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    user_message_created_at: datetime
    assistant_message_created_at: datetime


class HealthResponse(BaseModel):
    status: str
    f5ai_configured: bool
    amocrm_configured: bool
    telegram_configured: bool
    redis_connected: bool
    database_connected: bool


class BalanceResponse(BaseModel):
    balance: Any


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=10, max_length=256)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    role: str
    telegram_id: int | None
    is_active: bool
    request_limit_per_day: int
    requests_today: int
    created_at: datetime


class CreateUserRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=10, max_length=256)
    role: Literal["admin", "user"] = "user"
    telegram_id: int | None = None
    request_limit_per_day: int = Field(default=50, ge=1, le=10000)


class UpdateUserRequest(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    email: str | None = Field(default=None, min_length=3, max_length=255)
    password: str | None = Field(default=None, min_length=10, max_length=256)
    role: Literal["admin", "user"] | None = None
    telegram_id: int | None = None
    is_active: bool | None = None
    request_limit_per_day: int | None = Field(default=None, ge=1, le=10000)


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    target_user_id: int | None
    action: str
    details: str
    created_at: datetime


class ModelOption(BaseModel):
    code: str
    name: str
    vendor: str
    description: str
    is_light: bool = False


class AdminSettingsResponse(BaseModel):
    selected_model: str
    available_models: list[ModelOption]
    f5ai_configured: bool


class UpdateAdminSettingsRequest(BaseModel):
    selected_model: str = Field(..., min_length=1, max_length=100)


class QuickActionItem(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    prompt: str = Field(..., min_length=1, max_length=1000)


class QuickActionsResponse(BaseModel):
    actions: list[QuickActionItem]


class UpdateQuickActionsRequest(BaseModel):
    actions: list[QuickActionItem] = Field(..., max_length=8)


class CreateConversationRequest(BaseModel):
    title: str = Field(default="Новый чат", min_length=1, max_length=500)


class RenameConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)


class PinConversationRequest(BaseModel):
    is_pinned: bool


class ConversationResponse(BaseModel):
    id: int
    title: str
    last_message: str | None
    messages_count: int
    is_pinned: bool = False
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: int
    role: str
    content: str
    attachments: list[ChatAttachment] = Field(default_factory=list)
    created_at: datetime


class TelegramProxySettingsResponse(BaseModel):
    enabled: bool = False
    scheme: Literal["http", "socks5"] = "socks5"
    host: str = ""
    port: int = 1080
    username: str = ""
    password_configured: bool = False


class UpdateTelegramProxyRequest(BaseModel):
    enabled: bool = False
    scheme: Literal["http", "socks5"] = "socks5"
    host: str = Field(default="", max_length=255)
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=500)
    clear_password: bool = False


class TelegramProxyTestResponse(BaseModel):
    success: bool
    message: str
    bot_username: str | None = None


class TelegramProxyProfileResponse(BaseModel):
    id: str
    name: str
    scheme: Literal["http", "socks5"]
    host: str
    port: int
    username: str
    password_configured: bool


class TelegramProxyProfilesResponse(BaseModel):
    enabled: bool
    active_id: str | None
    profiles: list[TelegramProxyProfileResponse]


class UpsertTelegramProxyProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    scheme: Literal["http", "socks5"] = "socks5"
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(default=1080, ge=1, le=65535)
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=500)
    clear_password: bool = False


class UpsertReportTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    report_type: Literal["sales", "funnel", "managers"]
    period_mode: Literal[
        "current_month", "previous_month", "last_7_days", "last_30_days", "fixed"
    ] = "current_month"
    fixed_date_from: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    fixed_date_to: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    comparison_mode: Literal["none", "previous_period"] = "none"
    plan_value: int | None = Field(default=None, ge=0)
    pipeline_id: int | None = Field(default=None, ge=1)
    manager_id: int | None = Field(default=None, ge=1)
    group_by: Literal["status", "manager", "day", "week"] = "status"
    top_n: int = Field(default=10, ge=1, le=50)
    schedule_frequency: Literal["manual", "daily", "weekly", "monthly"] = "manual"
    schedule_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_weekday: int = Field(default=0, ge=0, le=6)
    schedule_month_day: int = Field(default=1, ge=1, le=28)
    telegram_chat_id: int | None = None
    export_formats: list[Literal["xlsx", "pdf", "png"]] = Field(default_factory=list)
    is_enabled: bool = True


class ReportTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    owner_user_id: int
    name: str
    report_type: str
    arguments: dict[str, Any]
    period_mode: str
    fixed_date_from: str | None
    fixed_date_to: str | None
    comparison_mode: str
    plan_value: int | None
    schedule_frequency: str
    schedule_time: str
    schedule_weekday: int
    schedule_month_day: int
    telegram_chat_id: int | None
    export_formats: list[str]
    is_enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class ReportRunResponse(BaseModel):
    presentation: dict[str, Any]
    telegram_sent: bool = False
