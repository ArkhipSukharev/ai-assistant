from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.api.admin import router as admin_router
from app.api.amocrm_integration import router as amocrm_integration_router
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.settings import router as settings_router
from app.bot.telegram import TelegramBotManager
from app.config import get_settings
from app.database import Database
from app.services.agent import AgentService
from app.services.amocrm_client import AmoCRMClient
from app.services.amocrm_insights import AmoCRMInsightService
from app.services.amocrm_snapshot import AmoCRMSnapshotStore
from app.services.app_settings import AppSettingsService
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService
from app.services.llm_client import LLMClient
from app.services.report_schedule import ReportScheduleService
from app.services.session_store import SessionStore
from app.services.telegram_proxy import TelegramProxyService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

session_store: SessionStore | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_store

    settings = get_settings()
    session_store = SessionStore(settings)
    await session_store.connect()
    app.state.session_store = session_store

    database = Database(settings.database_url)
    if settings.database_auto_create:
        await database.create_tables()
    elif not await database.ping():
        raise RuntimeError("Database is unavailable")
    auth_service = AuthService(database, settings)
    conversation_service = ConversationService(database)
    await auth_service.bootstrap_admin()
    app.state.database = database
    app.state.auth_service = auth_service
    app.state.conversation_service = conversation_service

    llm = LLMClient(settings)
    amocrm = AmoCRMClient(
        settings,
        AmoCRMSnapshotStore(database.session_factory),
    )
    await amocrm.start()
    app_settings_service = AppSettingsService(database, settings, llm)
    telegram_proxy_service = TelegramProxyService(database, settings)
    amocrm_insight_service = AmoCRMInsightService(
        settings, database.session_factory, amocrm
    )
    app.state.app_settings_service = app_settings_service
    app.state.telegram_proxy_service = telegram_proxy_service
    app.state.amocrm_insight_service = amocrm_insight_service
    agent = AgentService(
        settings,
        llm,
        amocrm,
        session_store,
        app_settings_service,
        amocrm_insight_service,
    )
    app.state.agent = agent

    telegram_manager = TelegramBotManager(
        settings,
        agent,
        auth_service,
        telegram_proxy_service,
    )
    app.state.telegram_manager = telegram_manager
    await telegram_manager.start()
    report_schedule_service = ReportScheduleService(
        database,
        settings,
        amocrm,
        telegram_manager,
    )
    app.state.report_schedule_service = report_schedule_service
    await report_schedule_service.start()
    if settings.amocrm_configured:
        await amocrm_insight_service.start()

    yield

    await amocrm_insight_service.stop()
    await report_schedule_service.stop()
    await telegram_manager.stop()
    await amocrm.close()

    if session_store:
        await session_store.close()
    await database.close()


limiter = Limiter(key_func=get_remote_address)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Assistant", lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(settings_router)
    app.include_router(conversations_router)
    app.include_router(admin_router)
    app.include_router(amocrm_integration_router)
    app.include_router(chat_router)
    return app


app = create_app()
