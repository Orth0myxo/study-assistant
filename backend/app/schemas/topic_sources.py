from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

TopicText = Annotated[str, StringConstraints(max_length=10_000)]
TopicJottingsText = str


class TopicWarning(BaseModel):
    code: str
    message: str


class TopicSourceDocument(BaseModel):
    document_id: str
    filename: str
    format: Literal["pdf", "docx"]
    page_count: int | None = None


class TopicSourceImage(BaseModel):
    index: int = Field(ge=0)
    filename: str
    content_type: Literal["image/png", "image/jpeg"]
    size_bytes: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    ocr_status: Literal[
        "ok", "empty", "timeout", "unavailable", "failed", "skipped", "manual"
    ]
    transcript_source: Literal["ocr", "manual"]
    text: str
    image_url: str | None = None
    warnings: list[TopicWarning] = Field(default_factory=list)


class TopicSourceResponse(BaseModel):
    source_id: str
    status: Literal["ok", "partial", "no_text"]
    document: TopicSourceDocument | None = None
    document_available: bool = False
    image_count: int = Field(ge=0)
    jottings_text: str = ""
    total_chars: int = Field(ge=0)
    topics: list[str] = Field(default_factory=list)
    images: list[TopicSourceImage] = Field(default_factory=list)
    warnings: list[TopicWarning] = Field(default_factory=list)
    created_at: str
    updated_at: str


class TopicTranscriptUpdate(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(max_length=200_000)


class TopicSourceUpdate(BaseModel):
    topics: list[TopicText] | None = Field(default=None, max_length=1000)
    transcripts: list[TopicTranscriptUpdate] | None = Field(
        default=None, max_length=100
    )
    jottings_text: TopicJottingsText | None = None


class TopicReviewRequest(BaseModel):
    topics: list[TopicText] | None = Field(default=None, max_length=1000)


class OutlineInterpretationResponse(BaseModel):
    topics: list[str] = Field(default_factory=list)
    warnings: list[TopicWarning] = Field(default_factory=list)


class TopicSourceMaterial(BaseModel):
    status: Literal["available", "not_linked", "unavailable", "empty"]
    section_count: int = Field(ge=0)


class TopicReviewSegmentation(BaseModel):
    input: Literal["transcript", "jottings", "combined", "provided"]
    topic_count: int = Field(ge=0)
    warnings: list[TopicWarning] = Field(default_factory=list)


class TopicPassageMatch(BaseModel):
    passage_id: str
    section_index: int = Field(ge=0)
    anchor: dict[str, Any]
    passage: str = Field(min_length=1)
    passage_start: int = Field(ge=0)
    passage_end: int = Field(ge=0)
    excerpt: str = ""
    excerpt_start: int = Field(default=0, ge=0)
    excerpt_end: int = Field(default=0, ge=0)
    score: float = Field(ge=0, le=1)
    coverage: float = Field(ge=0, le=1)
    phrase_match: bool
    matched_terms: list[str] = Field(default_factory=list)


class TopicReviewTopic(BaseModel):
    topic_id: str
    text: str
    status: Literal["matched", "ambiguous", "unmatched"]
    confidence: float = Field(ge=0, le=1)
    source_labels: list[str] = Field(default_factory=list)
    matches: list[TopicPassageMatch] = Field(default_factory=list)
    warnings: list[TopicWarning] = Field(default_factory=list)


class TopicReviewResponse(BaseModel):
    source_id: str
    document: TopicSourceDocument | None = None
    document_available: bool = False
    source_material: TopicSourceMaterial
    segmentation: TopicReviewSegmentation
    topics: list[TopicReviewTopic] = Field(default_factory=list)
    warnings: list[TopicWarning] = Field(default_factory=list)


StudyOutputMode = Literal[
    "source_passages", "ai_study_material", "both"
]
StudyAiScope = Literal["source_only", "source_and_beyond"]
StudyOutputStatus = Literal["completed", "partial", "failed"]
StudyMaterialStatus = Literal[
    "not_requested",
    "generated",
    "skipped_ambiguous",
    "skipped_unmatched",
    "skipped_no_evidence",
    "ai_failed",
]


class TopicStudyRequest(BaseModel):
    mode: StudyOutputMode
    topics: list[TopicText] | None = Field(default=None, max_length=1000)
    ai_scope: StudyAiScope = "source_only"


class TopicSourceGroundedMaterial(BaseModel):
    text: str = Field(min_length=1)
    basis_passage_ids: list[str] = Field(min_length=1)
    basis_refs: list[dict[str, Any]] = Field(min_length=1)


class TopicBeyondSourceMaterial(BaseModel):
    text: str = Field(min_length=1)
    grounded_in_uploaded_material: Literal[False] = False


class TopicGeneratedStudyMaterial(BaseModel):
    generated: Literal[True] = True
    source_grounded: TopicSourceGroundedMaterial
    beyond_source: TopicBeyondSourceMaterial | None = None


class TopicStudyOutputTopic(BaseModel):
    topic_id: str
    text: str
    match_status: Literal["matched", "ambiguous", "unmatched"]
    material_status: StudyMaterialStatus
    source_passages: list[TopicPassageMatch] = Field(default_factory=list)
    study_material: TopicGeneratedStudyMaterial | None = None
    warnings: list[TopicWarning] = Field(default_factory=list)


class TopicStudyAiInfo(BaseModel):
    requested: bool
    scope: StudyAiScope
    configured: bool
    generated_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    beyond_generated_count: int = Field(ge=0)
    beyond_failed_count: int = Field(ge=0)


class TopicStudyOutputResponse(BaseModel):
    result_id: str | None = None
    source_id: str
    document: TopicSourceDocument | None = None
    document_available: bool = False
    source_material: TopicSourceMaterial
    mode: StudyOutputMode
    ai_scope: StudyAiScope
    status: StudyOutputStatus
    topic_count: int = Field(ge=0)
    topics: list[TopicStudyOutputTopic] = Field(default_factory=list)
    ai: TopicStudyAiInfo
    warnings: list[TopicWarning] = Field(default_factory=list)
    error: TopicWarning | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    expires_at: str | None = None


class TopicStudyResultSummary(BaseModel):
    result_id: str
    source_id: str
    document_id: str | None = None
    filename: str
    format: Literal["pdf", "docx"] | None = None
    mode: StudyOutputMode
    ai_scope: StudyAiScope
    status: StudyOutputStatus
    topic_count: int = Field(ge=0)
    created_at: str
    updated_at: str
    expires_at: str
    document_available: bool = False


class TopicSourceSummary(BaseModel):
    source_id: str
    status: Literal["ok", "partial", "no_text"]
    document_id: str | None = None
    filename: str | None = None
    format: Literal["pdf", "docx"] | None = None
    image_count: int = Field(ge=0)
    total_chars: int = Field(ge=0)
    topics: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    document_available: bool = False
