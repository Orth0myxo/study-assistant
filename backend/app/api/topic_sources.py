from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import Settings, get_settings
from app.errors import ApiError
from app.schemas.topic_sources import (
    TopicBeyondSourceMaterial,
    TopicGeneratedStudyMaterial,
    TopicPassageMatch,
    TopicReviewRequest,
    TopicReviewResponse,
    TopicReviewSegmentation,
    TopicReviewTopic,
    TopicSourceDocument,
    TopicSourceGroundedMaterial,
    TopicSourceImage,
    TopicSourceMaterial,
    TopicSourceResponse,
    TopicSourceSummary,
    TopicSourceUpdate,
    TopicStudyAiInfo,
    TopicStudyOutputResponse,
    TopicStudyOutputTopic,
    TopicStudyRequest,
    TopicStudyResultSummary,
    TopicWarning,
)
from app.services.ai_client import AiNotConfigured
from app.services.exporter import EXPORT_MEDIA_TYPES, export_filename, render
from app.services.storage import require_ready_document, storage
from app.services.topic_results import topic_results
from app.services.topic_images import OcrResult, ocr_image, validate_image
from app.services.topic_matching import match_topics, normalize_for_match
from app.services.topic_segmentation import (
    prepare_provided_topics,
    segment_transcript,
)
from app.services.topic_sources import (
    TopicImageInput,
    TopicSourceRecord,
    topic_sources,
)
from app.services.topic_study import build_study_output

router = APIRouter(prefix="/api/v1", tags=["topic-sources"])
logger = logging.getLogger(__name__)


def _iso_timestamp(value: float) -> str:
    return (
        datetime.fromtimestamp(value, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _response(record: TopicSourceRecord) -> TopicSourceResponse:
    document: TopicSourceDocument | None = None
    document_available = False
    if record.document_id:
        available_record = storage.get_for_owner(
            record.document_id, record.owner_id
        )
        document_available = bool(
            available_record is not None and available_record.available
        )
        document = TopicSourceDocument(
            document_id=record.document_id,
            filename=record.document_filename or "document",
            format=record.document_format or "pdf",  # type: ignore[arg-type]
            page_count=record.document_page_count,
        )
    return TopicSourceResponse(
        source_id=record.source_id,
        status=record.status,  # type: ignore[arg-type]
        document=document,
        document_available=document_available,
        image_count=record.image_count,
        jottings_text=record.jottings_text,
        total_chars=record.total_chars,
        topics=record.topics,
        images=[
            TopicSourceImage(
                index=image.index,
                filename=image.filename,
                content_type=image.content_type,  # type: ignore[arg-type]
                size_bytes=image.size_bytes,
                width=image.width,
                height=image.height,
                ocr_status=image.ocr_status,  # type: ignore[arg-type]
                transcript_source=image.transcript_source,  # type: ignore[arg-type]
                text=image.text,
                image_url=(
                    f"/api/v1/topic-sources/{record.source_id}/images/{image.index}"
                ),
                warnings=[TopicWarning(**item) for item in image.warnings],
            )
            for image in record.images
        ],
        warnings=[TopicWarning(**item) for item in record.warnings],
        created_at=_iso_timestamp(record.created_at),
        updated_at=_iso_timestamp(record.updated_at),
    )


def _document_metadata(record: Any) -> dict[str, Any]:
    return {
        "document_id": record.document_id,
        "filename": record.filename,
        "format": record.format,
        "page_count": record.page_count,
    }


def _validated_topics(raw_topics: list[str], settings: Settings) -> list[str]:
    return prepare_provided_topics(
        raw_topics,
        max_topics=settings.topic_max_topics,
        max_chars=settings.topic_max_topic_chars,
    )


@router.post(
    "/topic-sources",
    response_model=TopicSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_source(
    request: Request,
    document_id: str | None = Form(default=None, max_length=128),
    jottings_text: str | None = Form(default=None),
    images: list[UploadFile] | None = File(default=None),
) -> TopicSourceResponse:
    settings = get_settings()
    user_id = request.state.user_id
    request_id = getattr(request.state, "request_id", "req_unknown")

    typed_jottings = (jottings_text or "").strip()
    if len(typed_jottings) > settings.topic_max_jottings_chars:
        raise ApiError(
            "jottings_too_long",
            f"Typed jottings must be at most "
            f"{settings.topic_max_jottings_chars} characters.",
            422,
            {"max_chars": settings.topic_max_jottings_chars},
        )

    uploads = images or []
    if not typed_jottings and not uploads:
        raise ApiError(
            "jottings_required",
            "Add typed jottings or at least one note photo.",
            400,
        )
    if len(uploads) > settings.topic_max_images:
        raise ApiError(
            "too_many_images",
            f"At most {settings.topic_max_images} note images are allowed.",
            413,
            {"max_images": settings.topic_max_images},
        )

    per_image_max = settings.topic_max_image_mb * 1024 * 1024
    total_max = settings.max_upload_mb * 1024 * 1024
    raw_images: list[tuple[str | None, bytes]] = []
    total_bytes = 0
    for upload in uploads:
        data = await upload.read(per_image_max + 1)
        if len(data) > per_image_max:
            raise ApiError(
                "image_too_large",
                f"A note image exceeds the {settings.topic_max_image_mb} MB limit.",
                413,
                {"limit_mb": settings.topic_max_image_mb},
            )
        total_bytes += len(data)
        if total_bytes > total_max:
            raise ApiError(
                "images_too_large",
                f"Note images exceed the {settings.max_upload_mb} MB upload limit.",
                413,
                {"limit_mb": settings.max_upload_mb},
            )
        raw_images.append((upload.filename, data))

    document: dict[str, Any] | None = None
    cleaned_document_id = (document_id or "").strip()
    if cleaned_document_id:
        record = storage.get_for_owner(cleaned_document_id, user_id)
        if record is None:
            raise ApiError(
                "document_not_found",
                "The document was not found or has expired.",
                404,
            )
        record = require_ready_document(record)
        if not record.available:
            raise ApiError(
                "document_unavailable",
                "The source document is no longer available.",
                409,
            )
        document = _document_metadata(record)

    ocr_limit = settings.ocr_page_limit
    processed: list[TopicImageInput] = []
    for index, (filename, data) in enumerate(raw_images):
        image = await run_in_threadpool(validate_image, filename, data, settings)
        if ocr_limit <= 0:
            result = OcrResult(
                status="unavailable",
                text="",
                warnings=[
                    _warning(
                        "ocr_disabled",
                        "OCR is disabled on the server; enter the note text manually.",
                    )
                ],
            )
        elif index >= ocr_limit:
            result = OcrResult(
                status="skipped",
                text="",
                warnings=[
                    _warning(
                        "ocr_limit_reached",
                        "OCR was skipped for this image because the OCR limit was "
                        "reached. Enter the text manually.",
                    )
                ],
            )
        else:
            result = await run_in_threadpool(ocr_image, image, settings)

        processed.append(
            TopicImageInput(
                filename=image.filename,
                content_type=image.content_type,
                data=image.data,
                width=image.width,
                height=image.height,
                ocr_status=result.status,
                text=result.text,
                warnings=result.warnings,
            )
        )

    saved = await run_in_threadpool(
        topic_sources.save,
        user_id,
        document=document,
        images=processed,
        jottings_text=typed_jottings,
    )
    logger.info(
        "topic source created request_id=%s source_id=%s images=%d bytes=%d "
        "jottings_chars=%d status=%s ocr_ok=%d",
        request_id,
        saved.source_id,
        saved.image_count,
        total_bytes,
        len(saved.jottings_text),
        saved.status,
        sum(1 for image in saved.images if image.ocr_status == "ok"),
    )
    return _response(saved)


@router.get(
    "/topic-sources/{source_id}",
    response_model=TopicSourceResponse,
)
def get_topic_source(source_id: str, request: Request) -> TopicSourceResponse:
    record = topic_sources.get_for_owner(source_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )
    return _response(record)


@router.delete(
    "/topic-sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_topic_source(source_id: str, request: Request) -> None:
    deleted = topic_sources.delete_for_owner(
        source_id, request.state.user_id
    )
    if not deleted:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )


@router.post(
    "/topic-sources/{source_id}/topic-review",
    response_model=TopicReviewResponse,
)
def review_topic_source(
    source_id: str,
    request: Request,
    payload: TopicReviewRequest | None = None,
) -> TopicReviewResponse:
    record = topic_sources.get_for_owner(source_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )
    raw_topics = (
        payload.topics
        if payload is not None and payload.topics is not None
        else None
    )
    return _build_topic_review(record, request.state.user_id, raw_topics)


def _input_sources(record: TopicSourceRecord) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    if record.jottings_text.strip():
        sources.append(("Typed jottings", record.jottings_text))
    for image in record.images:
        if image.text.strip():
            sources.append((f"Photo {image.index + 1}", image.text))
    return sources


def _labels_for_topic(
    topic: str, sources: list[tuple[str, str]]
) -> list[str]:
    normalized_topic = normalize_for_match(topic)
    if not normalized_topic:
        return []
    needle = f" {normalized_topic} "
    return [
        label
        for label, text in sources
        if needle in f" {normalize_for_match(text)} "
    ]


def _segment_sources(
    sources: list[tuple[str, str]], settings: Settings
) -> tuple[list[tuple[str, list[str]]], list[dict[str, str]]]:
    topics_with_labels: list[tuple[str, list[str]]] = []
    index_by_key: dict[str, int] = {}
    warnings: list[dict[str, str]] = []

    for label, text in sources:
        remaining = settings.topic_max_topics - len(topics_with_labels)
        if remaining <= 0:
            warnings.append(
                _warning(
                    "topic_limit_reached",
                    f"Only the first {settings.topic_max_topics} topics were "
                    "returned.",
                )
            )
            break
        segmented = segment_transcript(
            text,
            max_topics=remaining,
            max_chars=settings.topic_max_topic_chars,
        )
        warnings.extend(segmented.warnings)
        for topic in segmented.topics:
            key = normalize_for_match(topic)
            existing = index_by_key.get(key)
            if existing is not None:
                labels = topics_with_labels[existing][1]
                if label not in labels:
                    labels.append(label)
                continue
            index_by_key[key] = len(topics_with_labels)
            topics_with_labels.append((topic, [label]))

    unique_warnings: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for warning in warnings:
        key = (warning.get("code", ""), warning.get("message", ""))
        if key not in seen:
            seen.add(key)
            unique_warnings.append(warning)
    return topics_with_labels, unique_warnings


def _build_topic_review(
    record: TopicSourceRecord,
    user_id: str,
    raw_topics: list[str] | None,
) -> TopicReviewResponse:
    settings = get_settings()
    warnings: list[dict[str, str]] = []
    document: TopicSourceDocument | None = None
    document_available = False
    material_status = "not_linked"
    section_count = 0
    sections: list[dict[str, Any]] = []

    if record.document_id:
        document = TopicSourceDocument(
            document_id=record.document_id,
            filename=record.document_filename or "document",
            format=record.document_format or "pdf",  # type: ignore[arg-type]
            page_count=record.document_page_count,
        )
        source_document = storage.get_for_owner(record.document_id, user_id)
        if source_document is not None:
            source_document = require_ready_document(source_document)
        document_available = bool(
            source_document is not None and source_document.available
        )
        if source_document is None or not source_document.available:
            material_status = "unavailable"
            warnings.append(
                _warning(
                    "document_unavailable",
                    "The linked source document is no longer available, so topics "
                    "cannot be matched to source passages.",
                )
            )
        else:
            usable_sections = [
                section
                for section in source_document.sections
                if str(section.get("raw_text") or "").strip()
            ]
            if not source_document.text_extractable or not usable_sections:
                material_status = "empty"
                warnings.append(
                    _warning(
                        "source_material_empty",
                        "The linked document contains no extractable text to match "
                        "against.",
                    )
                )
            else:
                material_status = "available"
                sections = usable_sections
                section_count = len(sections)
    else:
        warnings.append(
            _warning(
                "document_not_linked",
                "This topic source is not linked to a source document, so topics "
                "cannot be matched to source passages.",
            )
        )

    sources = _input_sources(record)
    segmentation_warnings: list[dict[str, str]] = []
    if raw_topics is not None:
        topics = _validated_topics(raw_topics, settings)
        topics_with_labels = [
            (topic, _labels_for_topic(topic, sources)) for topic in topics
        ]
        mode = "provided"
    elif not sources:
        topics = []
        topics_with_labels = []
        mode = (
            "jottings"
            if record.jottings_text.strip()
            else "transcript"
        )
        warnings.append(
            _warning(
                "no_transcript",
                "There is no typed jottings text or corrected photo transcript "
                "to segment into topics.",
            )
        )
    else:
        topics_with_labels, segmentation_warnings = _segment_sources(
            sources, settings
        )
        topics = [topic for topic, _ in topics_with_labels]
        has_transcript = any(image.text.strip() for image in record.images)
        if record.jottings_text.strip() and has_transcript:
            mode = "combined"
        elif record.jottings_text.strip():
            mode = "jottings"
        else:
            mode = "transcript"

    if not topics and not any(
        warning.get("code") == "no_transcript" for warning in warnings
    ):
        warnings.append(
            _warning(
                "no_topics",
                "No usable study topics were found in the supplied input.",
            )
        )

    results = match_topics(
        topics,
        sections,
        document_id=record.document_id or "",
        settings=settings,
    )
    reviewed_topics = [
        TopicReviewTopic(
            topic_id=f"topic_{position + 1}",
            text=result.text,
            status=result.status,  # type: ignore[arg-type]
            confidence=result.confidence,
            source_labels=topics_with_labels[position][1],
            matches=[
                TopicPassageMatch(
                    passage_id=match.passage_id,
                    section_index=match.section_index,
                    anchor=match.anchor,
                    passage=match.passage,
                    passage_start=match.passage_start,
                    passage_end=match.passage_end,
                    excerpt=match.excerpt,
                    excerpt_start=match.excerpt_start,
                    excerpt_end=match.excerpt_end,
                    score=match.score,
                    coverage=match.coverage,
                    phrase_match=match.phrase_match,
                    matched_terms=match.matched_terms,
                )
                for match in result.matches
            ],
            warnings=[TopicWarning(**warning) for warning in result.warnings],
        )
        for position, result in enumerate(results)
    ]

    return TopicReviewResponse(
        source_id=record.source_id,
        document=document,
        document_available=document_available,
        source_material=TopicSourceMaterial(
            status=material_status,  # type: ignore[arg-type]
            section_count=section_count,
        ),
        segmentation=TopicReviewSegmentation(
            input=mode,  # type: ignore[arg-type]
            topic_count=len(topics),
            warnings=[
                TopicWarning(**warning) for warning in segmentation_warnings
            ],
        ),
        topics=reviewed_topics,
        warnings=[TopicWarning(**warning) for warning in warnings],
    )


@router.post(
    "/topic-sources/{source_id}/study-output",
    response_model=TopicStudyOutputResponse,
)
def create_topic_study_output(
    source_id: str,
    request: Request,
    payload: TopicStudyRequest,
) -> TopicStudyOutputResponse:
    settings = get_settings()
    record = topic_sources.get_for_owner(source_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )

    if payload.topics is not None:
        raw_topics: list[str] | None = payload.topics
    elif record.topics:
        raw_topics = list(record.topics)
    else:
        raw_topics = None

    review = _build_topic_review(record, request.state.user_id, raw_topics)
    try:
        outcome = build_study_output(
            review.topics,
            mode=payload.mode,
            ai_scope=payload.ai_scope,
            settings=settings,
            user_id=request.state.user_id,
        )
    except AiNotConfigured as exc:
        raise ApiError(exc.code, exc.message, exc.status_code) from exc

    reviewed_topics: list[TopicStudyOutputTopic] = []
    for topic in outcome.topics:
        material: TopicGeneratedStudyMaterial | None = None
        if topic.study_material is not None:
            generated = topic.study_material
            material = TopicGeneratedStudyMaterial(
                source_grounded=TopicSourceGroundedMaterial(
                    text=generated.source_grounded.text,
                    basis_passage_ids=generated.source_grounded.basis_passage_ids,
                    basis_refs=generated.source_grounded.basis_refs,
                ),
                beyond_source=(
                    TopicBeyondSourceMaterial(
                        text=generated.beyond_source.text
                    )
                    if generated.beyond_source is not None
                    else None
                ),
            )
        reviewed_topics.append(
            TopicStudyOutputTopic(
                topic_id=topic.topic_id,
                text=topic.text,
                match_status=topic.match_status,  # type: ignore[arg-type]
                material_status=topic.material_status,  # type: ignore[arg-type]
                source_passages=topic.source_passages,
                study_material=material,
                warnings=[TopicWarning(**warning) for warning in topic.warnings],
            )
        )

    response = TopicStudyOutputResponse(
        source_id=review.source_id,
        document=review.document,
        document_available=review.document_available,
        source_material=review.source_material,
        mode=payload.mode,
        ai_scope=payload.ai_scope,
        status=outcome.status,  # type: ignore[arg-type]
        topic_count=len(reviewed_topics),
        topics=reviewed_topics,
        ai=TopicStudyAiInfo(
            requested=outcome.ai_requested,
            scope=outcome.ai_scope,  # type: ignore[arg-type]
            configured=outcome.ai_configured,
            generated_count=outcome.generated_count,
            failed_count=outcome.failed_count,
            skipped_count=outcome.skipped_count,
            beyond_generated_count=outcome.beyond_generated_count,
            beyond_failed_count=outcome.beyond_failed_count,
        ),
        warnings=[
            *review.warnings,
            *review.segmentation.warnings,
            *[TopicWarning(**warning) for warning in outcome.warnings],
        ],
        error=(
            TopicWarning(**outcome.error)
            if outcome.error is not None
            else None
        ),
        diagnostics=outcome.diagnostics,
    )

    stored = response.model_dump(mode="json")
    document = review.document
    stored["kind"] = "topic_study"
    stored["filename"] = (
        document.filename if document is not None else "topic-study"
    )
    saved = topic_results.save(
        request.state.user_id,
        source_id=review.source_id,
        document_id=document.document_id if document is not None else None,
        document_filename=document.filename if document is not None else None,
        document_format=document.format if document is not None else None,
        mode=payload.mode,
        ai_scope=payload.ai_scope,
        status=outcome.status,
        topic_count=len(reviewed_topics),
        payload=stored,
    )
    response.result_id = saved.result_id
    response.created_at = saved.payload["created_at"]
    response.expires_at = saved.payload["expires_at"]
    return response


@router.patch(
    "/topic-sources/{source_id}",
    response_model=TopicSourceResponse,
)
def update_topic_source(
    source_id: str,
    payload: TopicSourceUpdate,
    request: Request,
) -> TopicSourceResponse:
    settings = get_settings()

    topics: list[str] | None = None
    if payload.topics is not None:
        topics = _validated_topics(payload.topics, settings)

    jottings_text: str | None = None
    if payload.jottings_text is not None:
        jottings_text = payload.jottings_text.strip()
        if len(jottings_text) > settings.topic_max_jottings_chars:
            raise ApiError(
                "jottings_too_long",
                f"Typed jottings must be at most "
                f"{settings.topic_max_jottings_chars} characters.",
                422,
                {"max_chars": settings.topic_max_jottings_chars},
            )

    transcripts: dict[int, str] | None = None
    if payload.transcripts is not None:
        transcripts = {}
        for item in payload.transcripts:
            if item.index in transcripts:
                raise ApiError(
                    "duplicate_transcript_index",
                    "Each transcript index may appear only once.",
                    400,
                )
            if len(item.text) > settings.topic_max_transcript_chars:
                raise ApiError(
                    "transcript_too_long",
                    f"Each transcript must be at most "
                    f"{settings.topic_max_transcript_chars} characters.",
                    422,
                    {"max_chars": settings.topic_max_transcript_chars},
                )
            transcripts[item.index] = item.text

    if topics is None and not transcripts and jottings_text is None:
        raise ApiError(
            "no_update",
            "Provide topics, jottings text, or transcripts to update.",
            400,
        )

    record = topic_sources.update_for_owner(
        source_id,
        request.state.user_id,
        topics=topics,
        transcripts=transcripts,
        jottings_text=jottings_text,
    )
    if record is None:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )
    return _response(record)


def _document_available(document_id: str | None, user_id: str) -> bool:
    if not document_id:
        return False
    record = storage.get_for_owner(document_id, user_id)
    return bool(record is not None and record.available)


@router.get(
    "/topic-sources",
    response_model=list[TopicSourceSummary],
)
def list_topic_sources(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TopicSourceSummary]:
    user_id = request.state.user_id
    summaries: list[TopicSourceSummary] = []
    for record in topic_sources.list_for_owner(user_id, limit=limit, offset=offset):
        summaries.append(
            TopicSourceSummary(
                source_id=record.source_id,
                status=record.status,  # type: ignore[arg-type]
                document_id=record.document_id,
                filename=record.document_filename,
                format=record.document_format,  # type: ignore[arg-type]
                image_count=record.image_count,
                total_chars=record.total_chars,
                topics=record.topics,
                created_at=_iso_timestamp(record.created_at),
                updated_at=_iso_timestamp(record.updated_at),
                document_available=_document_available(
                    record.document_id, user_id
                ),
            )
        )
    return summaries


@router.get("/topic-sources/{source_id}/images/{image_index}")
def get_topic_source_image(
    source_id: str,
    image_index: int,
    request: Request,
) -> FileResponse:
    record = topic_sources.get_for_owner(source_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_source_not_found",
            "The topic source was not found or has expired.",
            404,
        )
    image = next(
        (item for item in record.images if item.index == image_index), None
    )
    if image is None:
        raise ApiError(
            "image_not_found",
            "The requested note image does not exist on this topic source.",
            404,
        )
    path = image.path.resolve()
    try:
        inside_root = path.is_relative_to(topic_sources.root.resolve())
    except (OSError, ValueError):
        inside_root = False
    if not inside_root or not path.is_file():
        raise ApiError(
            "topic_image_unavailable",
            "The stored note image is no longer available.",
            404,
        )
    return FileResponse(
        path,
        media_type=image.content_type,
        filename=image.filename,
        content_disposition_type="inline",
    )


@router.get(
    "/topic-results",
    response_model=list[TopicStudyResultSummary],
)
def list_topic_results(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[TopicStudyResultSummary]:
    user_id = request.state.user_id
    summaries: list[TopicStudyResultSummary] = []
    for record in topic_results.list_for_owner(
        user_id, limit=limit, offset=offset
    ):
        summaries.append(
            TopicStudyResultSummary(
                result_id=record.result_id,
                source_id=record.source_id,
                document_id=record.document_id,
                filename=record.document_filename or "Topic study",
                format=record.document_format,  # type: ignore[arg-type]
                mode=record.mode,  # type: ignore[arg-type]
                ai_scope=record.ai_scope,  # type: ignore[arg-type]
                status=record.status,  # type: ignore[arg-type]
                topic_count=record.topic_count,
                created_at=_iso_timestamp(record.created_at),
                updated_at=_iso_timestamp(record.updated_at),
                expires_at=_iso_timestamp(record.expires_at),
                document_available=_document_available(
                    record.document_id, user_id
                ),
            )
        )
    return summaries


@router.get(
    "/topic-results/{result_id}",
    response_model=TopicStudyOutputResponse,
)
def get_topic_result(
    result_id: str, request: Request
) -> TopicStudyOutputResponse:
    record = topic_results.get_for_owner(result_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_result_not_found",
            "The topic study result was not found or has expired.",
            404,
        )
    payload = dict(record.payload)
    payload["document_available"] = _document_available(
        record.document_id, request.state.user_id
    )
    return TopicStudyOutputResponse.model_validate(payload)


@router.delete(
    "/topic-results/{result_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_topic_result(result_id: str, request: Request) -> None:
    if not topic_results.delete_for_owner(result_id, request.state.user_id):
        raise ApiError(
            "topic_result_not_found",
            "The topic study result was not found or has expired.",
            404,
        )


@router.get("/topic-results/{result_id}/export")
async def export_topic_result(
    result_id: str,
    request: Request,
    format: str = Query(..., pattern="^(pdf|docx)$"),
) -> Response:
    record = topic_results.get_for_owner(result_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "topic_result_not_found",
            "The topic study result was not found or has expired.",
            404,
        )
    payload = dict(record.payload)
    payload["document_available"] = _document_available(
        record.document_id, request.state.user_id
    )
    try:
        data = await run_in_threadpool(render, payload, format)
    except Exception as exc:  # noqa: BLE001 - never leak internals to the client
        logger.warning(
            "topic export failed result_id=%s format=%s error_type=%s",
            result_id,
            format,
            type(exc).__name__,
        )
        raise ApiError(
            "export_failed",
            "The export could not be generated.",
            500,
        ) from exc
    filename = export_filename(payload, format)
    return Response(
        content=data,
        media_type=EXPORT_MEDIA_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
