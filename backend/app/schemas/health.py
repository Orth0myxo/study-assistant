from typing import Literal

from pydantic import BaseModel


class AiStatus(BaseModel):
    provider: Literal["configured", "missing"]
    model: str | None = None
    reachable: bool | None = None


class HealthLimits(BaseModel):
    max_upload_mb: int
    retention_minutes: int


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    ai: AiStatus
    limits: HealthLimits
