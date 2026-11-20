from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import get_settings
from app.errors import ApiError
from app.schemas.convert import (
    ConversionErrorData,
    ConversionFileInfo,
    ConversionFormatsResponse,
    ConversionJobResponse,
    ConversionLimits,
    ConversionTable,
    ConversionTableExportRequest,
)
from app.services.convert_store import ConversionJob, conversion_jobs
from app.services.converter import (
    CONVERT_MAX_CELL_CHARS,
    CONVERT_MAX_COLUMNS,
    CONVERT_MAX_ROWS,
    ConversionError,
    conversion_formats,
    export_summary_table,
    get_conversion,
    start_conversion_job,
    validate_input,
)

router = APIRouter(prefix="/api/v1/convert", tags=["convert"])

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _job_response(job: ConversionJob) -> ConversionJobResponse:
    error = None
    if job.error_code:
        error = ConversionErrorData(
            code=job.error_code,
            message=job.error_message or "The conversion failed.",
            details=job.error_details,
        )
    file_info = None
    if (
        job.status == "completed"
        and job.file_name
        and job.file_format
        and job.file_path
        and job.file_path.is_file()
    ):
        file_info = ConversionFileInfo(
            name=job.file_name,
            format=job.file_format,
            size_bytes=job.size_bytes,
        )
    table = None
    if job.table and job.table.get("columns"):
        table = ConversionTable(
            title=str(job.table.get("title") or ""),
            columns=[str(value) for value in job.table["columns"]],
            rows=[
                [str(value) for value in row]
                for row in job.table.get("rows") or []
            ],
        )
    return ConversionJobResponse(
        job_id=job.job_id,
        conversion_id=job.conversion_id,
        input_kind=job.input_kind,
        output_kind=job.output_kind,
        status=job.status,
        needs_ai=job.needs_ai,
        editable=bool(
            table is not None
            and job.status == "completed"
            and job.conversion_id.startswith("summary-")
        ),
        file=file_info,
        table=table,
        warnings=job.warnings,
        error=error,
        created_at=job.created_at,
        expires_at=job.expires_at,
    )


def _raise_api_error(exc: ConversionError) -> None:
    if exc.code in {"conversion_not_found", "conversion_input_missing"}:
        status = 404
    elif exc.code in {"file_too_large", "document_too_long"}:
        status = 413
    elif exc.code in {"file_format_mismatch", "macro_file_unsupported"}:
        status = 415
    else:
        status = 422
    raise ApiError(exc.code, exc.message, status, exc.details)


@router.get("/formats", response_model=ConversionFormatsResponse)
def get_conversion_formats() -> ConversionFormatsResponse:
    settings = get_settings()
    payload = conversion_formats()
    return ConversionFormatsResponse(
        inputs=payload["inputs"],
        limits=ConversionLimits(
            max_file_mb=settings.max_upload_mb,
            max_rows=CONVERT_MAX_ROWS,
            max_columns=CONVERT_MAX_COLUMNS,
            max_cell_chars=CONVERT_MAX_CELL_CHARS,
            max_pages=settings.max_pages,
        ),
    )


@router.post("/jobs", response_model=ConversionJobResponse, status_code=202)
async def start_conversion(
    request: Request,
    conversion_id: str = Form(..., min_length=1, max_length=80),
    text: str | None = Form(default=None, max_length=200_000),
    sheet: str | None = Form(default=None, max_length=120),
    use_ai: bool = Form(default=False),
    file: UploadFile | None = File(default=None),
) -> ConversionJobResponse:
    settings = get_settings()
    definition = get_conversion(conversion_id)
    if definition is None:
        raise ApiError(
            "conversion_not_found",
            "That conversion is not available.",
            404,
        )
    if definition.needs_ai and not use_ai:
        raise ApiError(
            "ai_consent_required",
            "Confirm that AI may read this source before creating the summary table.",
            422,
        )
    max_bytes = max(settings.max_upload_mb, 1) * 1024 * 1024
    if definition.input_mode == "text":
        if file is not None:
            raise ApiError(
                "conversion_input_invalid",
                "This conversion uses pasted text, not a file.",
                422,
            )
        if text is None or not text.strip():
            raise ApiError(
                "empty_text",
                "Paste some text before converting.",
                422,
            )
        data = text.encode("utf-8")
        if len(data) > max_bytes:
            raise ApiError(
                "file_too_large",
                f"The text is larger than {settings.max_upload_mb} MB.",
                413,
            )
        filename = "pasted-text.txt"
        suffix = ".txt"
    else:
        if text is not None:
            raise ApiError(
                "conversion_input_invalid",
                "This conversion uses a file, not pasted text.",
                422,
            )
        if file is None:
            raise ApiError(
                "conversion_input_invalid",
                "Choose a file to convert.",
                422,
            )
        filename = Path(
            (file.filename or "").replace("\\", "/")
        ).name.replace("\x00", "")[:255]
        filename = filename or "upload"
        suffix = Path(filename).suffix or ".bin"
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ApiError(
                "file_too_large",
                f"The file is larger than {settings.max_upload_mb} MB.",
                413,
            )
        if not data:
            raise ApiError(
                "empty_file",
                "That file is empty.",
                422,
            )
        try:
            await run_in_threadpool(
                validate_input, definition, filename, data, settings
            )
        except ConversionError as exc:
            _raise_api_error(exc)

    job_id = conversion_jobs.new_id()
    path = conversion_jobs.input_path(job_id, suffix)
    await run_in_threadpool(path.write_bytes, data)
    job = conversion_jobs.create(
        request.state.user_id,
        job_id=job_id,
        conversion_id=definition.conversion_id,
        input_kind=definition.input_kind,
        output_kind=definition.output_kind,
        needs_ai=definition.needs_ai,
        input_path=path,
        input_name=filename,
        sheet=(sheet or "").strip() or None,
    )
    start_conversion_job(job.job_id, job.owner_id, settings)
    return _job_response(job)


@router.get("/jobs/{job_id}", response_model=ConversionJobResponse)
def get_conversion_job(job_id: str, request: Request) -> ConversionJobResponse:
    job = conversion_jobs.get_for_owner(job_id, request.state.user_id)
    if job is None:
        raise ApiError(
            "conversion_not_found",
            "The conversion was not found or has expired.",
            404,
        )
    return _job_response(job)


@router.post(
    "/jobs/{job_id}/export",
    response_model=ConversionJobResponse,
    status_code=200,
)
def export_conversion_table(
    job_id: str,
    payload: ConversionTableExportRequest,
    request: Request,
) -> ConversionJobResponse:
    try:
        job = export_summary_table(
            request.state.user_id,
            job_id,
            payload.format,
            payload.columns,
            payload.rows,
            payload.title,
            get_settings(),
        )
    except ConversionError as exc:
        _raise_api_error(exc)
    return _job_response(job)


@router.get("/jobs/{job_id}/file")
def download_conversion_file(job_id: str, request: Request) -> FileResponse:
    job = conversion_jobs.get_for_owner(job_id, request.state.user_id)
    if (
        job is None
        or job.status != "completed"
        or not job.file_path
        or not job.file_path.is_file()
        or not job.file_format
    ):
        raise ApiError(
            "conversion_file_not_found",
            "The converted file was not found or has expired.",
            404,
        )
    return FileResponse(
        path=job.file_path,
        media_type=_MEDIA_TYPES.get(job.file_format, "application/octet-stream"),
        filename=Path(job.file_name or f"converted.{job.file_format}").name,
    )
