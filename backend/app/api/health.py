from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    settings = get_settings()
    session_store = getattr(request.app.state, "session_store", None)
    database = getattr(request.app.state, "database", None)
    redis_connected = await session_store.ping() if session_store else False
    database_connected = await database.ping() if database else False
    return HealthResponse(
        status="ok" if database_connected else "degraded",
        f5ai_configured=bool(settings.f5ai_api_key),
        amocrm_configured=settings.amocrm_configured,
        telegram_configured=bool(settings.telegram_bot_token),
        redis_connected=redis_connected,
        database_connected=database_connected,
    )


@router.get("/ready")
async def readiness(request: Request) -> JSONResponse:
    settings = get_settings()
    database = getattr(request.app.state, "database", None)
    session_store = getattr(request.app.state, "session_store", None)
    checks = {
        "database": bool(database and await database.ping()),
        "redis": bool(session_store and await session_store.ping()),
    }
    ready = checks["database"] and (
        checks["redis"] or not settings.session_store_require_redis
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
