from fastapi import APIRouter

from app.config import get_settings
from app.schemas.health import AiStatus, HealthLimits, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    ai_configured = settings.ai_configured
    return HealthResponse(
        status="ok" if ai_configured else "degraded",
        version=settings.version,
        ai=AiStatus(
            provider="configured" if ai_configured else "missing",
            model=(
                settings.groq_model
                if "groq" in settings.configured_ai_providers
                else settings.gemini_model if ai_configured else None
            ),
            reachable=None,
        ),
        limits=HealthLimits(
            max_upload_mb=settings.max_upload_mb,
            retention_minutes=settings.retention_minutes,
        ),
    )
