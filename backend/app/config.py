from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    f5ai_api_key: str = ""
    f5ai_model: str = "gpt-4.1-mini"
    f5ai_model_complex: str = "gpt-4o"
    f5ai_base_url: str = "https://api.f5ai.ru"

    amocrm_domain: str = ""
    amocrm_access_token: str = ""
    amocrm_refresh_token: str = ""
    amocrm_client_id: str = ""
    amocrm_client_secret: str = ""
    amocrm_redirect_uri: str = ""
    public_base_url: str = ""
    amocrm_webhook_secret: str = ""
    forgotten_deal_days: int = 7
    insight_scan_interval_minutes: int = 60
    insight_scan_max_leads: int = 100

    telegram_bot_token: str = ""
    telegram_allowed_users: str = ""
    telegram_bot_enabled: bool = True
    settings_encryption_key: str = ""

    web_api_key: str = ""
    redis_url: str = "redis://localhost:6379"
    database_url: str = "sqlite+aiosqlite:///./amocrm_assistant.db"
    database_auto_create: bool = True
    session_store_require_redis: bool = False

    admin_email: str = ""
    admin_password: str = ""
    auth_session_days: int = 7
    auth_cookie_secure: bool = False
    telegram_require_account: bool = True

    cors_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def telegram_allowed_user_ids(self) -> set[int]:
        if not self.telegram_allowed_users.strip():
            return set()
        return {int(uid.strip()) for uid in self.telegram_allowed_users.split(",") if uid.strip()}

    @property
    def amocrm_configured(self) -> bool:
        return bool(self.amocrm_domain and self.amocrm_access_token)


@lru_cache
def get_settings() -> Settings:
    return Settings()
