from typing import Any

from pydantic import BaseModel, Field

from app.schemas.topic_sources import StudyOutputMode


class RangeExtractionRequest(BaseModel):
    selector: str = Field(min_length=1, max_length=300)


class RangeExtractionResponse(BaseModel):
    document_id: str
    format: str
    page_count: int | None = None
    selector: str
    sections: list[dict[str, Any]]
    warnings: list[dict[str, str]] = Field(default_factory=list)


class RangeStudyRequest(RangeExtractionRequest):
    mode: StudyOutputMode = "source_passages"
