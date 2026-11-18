"""Owner-scoped direct export jobs for stored agent results."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Literal

from app.errors import ApiError
from app.services.agent_service import AgentExportError, build_agent_export
from app.services.agent_store import AgentExportJob, agent_store

logger = logging.getLogger(__name__)

ExportFormat = Literal["docx", "pdf"]

_VALUE_ERROR_CODES = {
    "document not found": ("document_not_found", 404),
    "result not found": ("agent_result_not_found", 404),
    "no summary is available for that result": ("export_no_summary", 409),
    "the result has no readable content to export": ("export_empty", 422),
}


def start_agent_export(
    owner_id: str, result_id: str, file_format: ExportFormat
) -> AgentExportJob:
    result = agent_store.get_result(result_id, owner_id)
    if result is None:
        raise AgentExportError(
            "agent_result_not_found", "result not found", 404
        )
    job = agent_store.save_export_job(owner_id, result_id, file_format)
    thread = threading.Thread(
        target=_run_export_job,
        args=(job.job_id, owner_id, result_id, file_format),
        name=f"agent-export-{job.job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return job


def _run_export_job(
    job_id: str, owner_id: str, result_id: str, file_format: ExportFormat
) -> None:
    started = time.perf_counter()
    job = agent_store.get_export_job(job_id, owner_id)
    if job is None:
        return
    job.status = "running"
    agent_store.update_export_job(job)
    outcome = "failed"
    try:
        result = agent_store.get_result(result_id, owner_id)
        if result is None:
            raise AgentExportError(
                "agent_result_not_found", "result not found", 404
            )
        include = ["summary"] if result.kind == "summary" else ["verbatim"]
        built = build_agent_export(owner_id, result_id, file_format, include)
        job.status = "completed"
        job.file_id = str(built["file_id"])
        job.name = str(built["name"])
        outcome = "completed"
    except AgentExportError as exc:
        _fail_job(job, exc.code, exc.message, exc.details)
    except ApiError as exc:
        _fail_job(job, exc.code, exc.message, exc.details or {})
    except ValueError as exc:
        code, status = _VALUE_ERROR_CODES.get(
            str(exc), ("export_failed", 500)
        )
        _fail_job(
            job,
            code,
            "The export could not be prepared.",
            {"status_code": status},
        )
    except Exception as exc:  # never log document text or exception details
        logger.info(
            "agent export error_type=%s", type(exc).__name__
        )
        _fail_job(
            job,
            "export_failed",
            "The export could not be generated.",
            {"status_code": 500},
        )
    finally:
        agent_store.update_export_job(job)
        logger.info(
            "agent export job=%s format=%s seconds=%.2f outcome=%s",
            job_id[:8],
            file_format,
            time.perf_counter() - started,
            outcome,
        )


def _fail_job(
    job: AgentExportJob,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    job.status = "failed"
    job.error_code = code
    job.error_message = message
    job.details = details or {}
