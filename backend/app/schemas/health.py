from typing import Literal

from pydantic import BaseModel


class AiStatus(BaseModel):
    provider: Literal["configured", "missing"]
    model: str | None = None
    reachable: bool | None = None
    # Providers that will actually be tried, in order, and the model each uses.
    # A provider missing here is switched off (no key, or left out of AI_PROVIDER_ORDER).
    providers: list[str] = []
    models: dict[str, str] = {}


class HealthLimits(BaseModel):
    max_upload_mb: int
    retention_minutes: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    # Short git commit the running service was built from (Render sets it); None if unknown.
    commit: str | None = None
    # Random id generated when the service process starts; changes on restart.
    boot_id: str
    ai: AiStatus
    limits: HealthLimits
