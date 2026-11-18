from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import get_settings
from app.errors import ApiError
from app.schemas.table import (
    TableExportErrorData,
    TableExportJobResponse,
    TableExportRequest,
    TableReadRequest,
    TableResultResponse,
)
from app.services.agent_store import AgentExportJob, agent_store
from app.services.table_results import TableResultRecord, table_results
from app.services.table_service import read_table, start_table_export

router = APIRouter(prefix="/api/v1/tables", tags=["tables"])

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _result_response(record: TableResultRecord) -> TableResultResponse:
    payload = record.payload
    return TableResultResponse(
        result_id=record.result_id,
        source_id=record.source_id,
        image_index=record.image_index,
        engine=payload.get("engine", "local"),
        columns=payload.get("columns") or [],
        rows=payload.get("rows") or [],
        confidence=payload.get("confidence") or [],
        low_confidence=payload.get("low_confidence") or [],
        row_count=payload.get("row_count") or len(payload.get("rows") or []),
        column_count=payload.get("column_count") or len(payload.get("columns") or []),
        warnings=payload.get("warnings") or [],
        created_at=record.created_at,
        expires_at=record.expires_at,
    )


def _export_response(job: AgentExportJob) -> TableExportJobResponse:
    error = None
    if job.error_code:
        error = TableExportErrorData(
            code=job.error_code,
            message=job.error_message or "The export failed.",
            details=job.details or {},
        )
    return TableExportJobResponse(
        job_id=job.job_id,
        result_id=job.result_id,
        format=job.format,
        status=job.status,
        file_id=job.file_id,
        name=job.name,
        error=error,
    )


@router.post("", response_model=TableResultResponse, status_code=201)
async def read_table_image(payload: TableReadRequest, request: Request) -> TableResultResponse:
    record = await run_in_threadpool(
        read_table,
        request.state.user_id,
        payload.source_id,
        payload.image_index,
        payload.engine,
        get_settings(),
    )
    return _result_response(record)


@router.get("/results/{result_id}", response_model=TableResultResponse)
def get_table_result(result_id: str, request: Request) -> TableResultResponse:
    record = table_results.get_for_owner(result_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "table_result_not_found",
            "The table result was not found or has expired.",
            404,
        )
    return _result_response(record)


@router.delete("/results/{result_id}", status_code=204)
def delete_table_result(result_id: str, request: Request) -> None:
    deleted = table_results.delete_for_owner(result_id, request.state.user_id)
    if not deleted:
        raise ApiError(
            "table_result_not_found",
            "The table result was not found or has expired.",
            404,
        )


@router.post(
    "/results/{result_id}/exports",
    response_model=TableExportJobResponse,
    status_code=202,
)
def export_table_result(
    result_id: str, payload: TableExportRequest, request: Request
) -> TableExportJobResponse:
    job = start_table_export(
        request.state.user_id,
        result_id,
        payload.format,
        get_settings(),
        payload.columns,
        payload.rows,
    )
    return _export_response(job)


@router.get("/exports/{job_id}", response_model=TableExportJobResponse)
def get_table_export(job_id: str, request: Request) -> TableExportJobResponse:
    job = agent_store.get_export_job(job_id, request.state.user_id)
    if job is None:
        raise ApiError(
            "table_export_not_found",
            "The export was not found or has expired.",
            404,
        )
    return _export_response(job)


@router.get("/files/{file_id}")
def download_table_file(file_id: str, request: Request) -> FileResponse:
    record = agent_store.get_file(file_id, request.state.user_id)
    if (
        record is None
        or record.format not in {"csv", "xlsx", "docx"}
        or not record.path.is_file()
    ):
        raise ApiError(
            "table_file_not_found",
            "The file was not found or has expired.",
            404,
        )
    media_type = _MEDIA_TYPES.get(record.format, "application/octet-stream")
    return FileResponse(
        path=record.path,
        media_type=media_type,
        filename=Path(record.name).name,
    )
