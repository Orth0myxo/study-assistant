"""Bounded table reads plus background exports for owner-scoped results."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Literal

from app.config import Settings
from app.errors import ApiError
from app.services.agent_store import AgentExportJob, agent_store
from app.services.table_exporter import build_table_file
from app.services.table_reader import (
    TableRead,
    merge_tables,
    parse_table_text,
    read_ai_table,
    read_local_table,
)
from app.services.table_results import TableResultRecord, table_results
from app.services.topic_sources import topic_sources

logger = logging.getLogger(__name__)

TableEngine = Literal["local", "ai"]
ExportFormat = Literal["csv", "xlsx", "docx"]


def read_table(
    owner_id: str,
    source_id: str,
    image_index: int,
    engine: TableEngine,
    settings: Settings,
    image_indices: list[int] | None = None,
) -> TableResultRecord:
    source = topic_sources.get_for_owner(source_id, owner_id)
    if source is None:
        raise ApiError(
            "table_source_not_found",
            "The table image was not found or has expired.",
            404,
        )
    indices = list(dict.fromkeys(image_indices or [image_index]))
    images = []
    for index in indices:
        image = next(
            (item for item in source.images if item.index == index), None
        )
        if image is None:
            raise ApiError(
                "table_image_not_found",
                "A selected image was not found in that source.",
                404,
            )
        images.append(image)
    tables: list[TableRead] = []
    for image in images:
        try:
            data = Path(image.path).read_bytes()
        except OSError as exc:
            raise ApiError(
                "table_image_not_found",
                "A selected image is no longer available.",
                404,
            ) from exc
        if engine == "ai":
            tables.append(read_ai_table(data, image.content_type, settings))
        else:
            tables.append(
                read_local_table(data, _suffix(image.content_type), settings)
            )
    table = merge_tables(tables) if len(tables) > 1 else tables[0]
    payload = table.as_payload()
    payload["name"] = images[0].filename
    payload["image_count"] = len(images)
    return table_results.save(
        owner_id,
        source_id=source.source_id,
        image_index=images[0].index,
        payload=payload,
    )


def read_text_table(
    owner_id: str,
    text: str,
    settings: Settings,
) -> TableResultRecord:
    table = parse_table_text(text, settings)
    payload = table.as_payload()
    payload["name"] = "typed-table"
    payload["image_count"] = 0
    return table_results.save(
        owner_id,
        source_id="",
        image_index=0,
        payload=payload,
    )


def start_table_export(
    owner_id: str,
    result_id: str,
    file_format: ExportFormat,
    settings: Settings,
    columns: list[str] | None = None,
    rows: list[list[str]] | None = None,
    title: str | None = None,
) -> AgentExportJob:
    record = table_results.get_for_owner(result_id, owner_id)
    if record is None:
        raise ApiError(
            "table_result_not_found",
            "The table result was not found or has expired.",
            404,
        )
    if (columns is None) != (rows is None):
        raise ApiError(
            "table_edit_invalid",
            "Send both columns and rows when exporting an edited table.",
            422,
        )
    if columns is not None and rows is not None:
        record = _edited_record(record, columns, rows, settings, title)
    elif title is not None:
        record = _edited_record(
            record,
            list(record.payload.get("columns") or []),
            [list(row) for row in record.payload.get("rows") or []],
            settings,
            title,
        )
    job = agent_store.save_export_job(owner_id, result_id, file_format)
    thread = threading.Thread(
        target=_run_export_job,
        args=(job.job_id, owner_id, record, file_format),
        name=f"table-export-{job.job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job


def _edited_record(
    record: TableResultRecord,
    columns: list[str],
    rows: list[list[str]],
    settings: Settings,
    title: str | None = None,
) -> TableResultRecord:
    if not columns:
        raise ApiError(
            "table_edit_invalid",
            "A table needs at least one column.",
            422,
        )
    if len(columns) > settings.table_max_columns:
        raise ApiError(
            "table_too_large",
            f"The table has more than {settings.table_max_columns} columns.",
            422,
        )
    if len(rows) > settings.table_max_rows:
        raise ApiError(
            "table_too_large",
            f"The table has more than {settings.table_max_rows} rows.",
            422,
        )
    limit = max(settings.table_max_cell_chars, 1)
    if any(len(cell) > limit for cell in columns):
        raise ApiError(
            "table_edit_invalid",
            "A column heading is longer than the supported limit.",
            422,
        )
    for index, row in enumerate(rows, start=1):
        if len(row) != len(columns):
            raise ApiError(
                "table_edit_invalid",
                f"Row {index} does not match the number of columns.",
                422,
                {"row": index},
            )
        if any(len(cell) > limit for cell in row):
            raise ApiError(
                "table_edit_invalid",
                f"A cell in row {index} is longer than the supported limit.",
                422,
                {"row": index},
            )
    payload = dict(record.payload)
    if title is not None:
        payload["title"] = title[:limit]
    payload["columns"] = list(columns)
    payload["rows"] = [list(row) for row in rows]
    payload["row_count"] = len(rows)
    payload["column_count"] = len(columns)
    return TableResultRecord(
        record.result_id,
        record.owner_id,
        record.source_id,
        record.image_index,
        payload,
        record.created_at,
        record.expires_at,
    )


def _run_export_job(
    job_id: str,
    owner_id: str,
    record: TableResultRecord,
    file_format: ExportFormat,
) -> None:
    started = time.perf_counter()
    job = agent_store.get_export_job(job_id, owner_id)
    if job is None:
        return
    job.status = "running"
    agent_store.update_export_job(job)
    outcome = "failed"
    try:
        content, name, suffix = build_table_file(record, file_format)
        saved = agent_store.save_file(owner_id, content, name, suffix)
        job.status = "completed"
        job.file_id = saved.file_id
        job.name = saved.name
        outcome = "completed"
    except ApiError as exc:
        job.status = "failed"
        job.error_code = exc.code
        job.error_message = exc.message
        job.details = exc.details or {}
    except ValueError as exc:
        job.status = "failed"
        job.error_code = "table_export_empty"
        job.error_message = "The table has no content to export."
        job.details = {"reason": str(exc)}
    except Exception as exc:  # never include table text in logs
        logger.info("table export error_type=%s", type(exc).__name__)
        job.status = "failed"
        job.error_code = "table_export_failed"
        job.error_message = "The table export could not be generated."
    finally:
        agent_store.update_export_job(job)
        logger.info(
            "table export job=%s format=%s seconds=%.2f outcome=%s",
            job_id[:8],
            file_format,
            time.perf_counter() - started,
            outcome,
        )


def _suffix(content_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }.get(content_type, ".img")
