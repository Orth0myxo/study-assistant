import os
import uuid

from fastapi import APIRouter, Response

from app.config import get_settings
from app.schemas.health import AiStatus, HealthLimits, HealthResponse

router = APIRouter(tags=["health"])

_COMMIT_ENV_VARS = ("RENDER_GIT_COMMIT", "GIT_COMMIT")

# Generated once per process so the app can tell a restart from a new build.
_BOOT_ID = uuid.uuid4().hex


def _deployed_commit() -> str | None:
    """Short commit id of the running build, so the app and the server can be compared."""
    for name in _COMMIT_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value[:7]
    return None


# HEAD is allowed because uptime monitors (UptimeRobot's default) probe with HEAD;
# a GET-only route answers them 405 and they report the service as down. It has its
# own handler, hidden from the schema, so OpenAPI keeps a single operation id.
@router.head("/health", include_in_schema=False)
def health_head() -> Response:
    return Response(status_code=200)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    ai_configured = settings.ai_configured
    providers = settings.configured_ai_providers
    provider_models = {
        "groq": settings.groq_model,
        "gemini": settings.gemini_model,
    }
    return HealthResponse(
        status="ok" if ai_configured else "degraded",
        version=settings.version,
        commit=_deployed_commit(),
        boot_id=_BOOT_ID,
        ai=AiStatus(
            provider="configured" if ai_configured else "missing",
            model=(
                settings.groq_model
                if "groq" in settings.configured_ai_providers
                else settings.gemini_model if ai_configured else None
            ),
            reachable=None,
            providers=providers,
            models={name: provider_models[name] for name in providers},
        ),
        limits=HealthLimits(
            max_upload_mb=settings.max_upload_mb,
            retention_minutes=settings.retention_minutes,
        ),
    )
