from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.study import router as study_router
from app.config import get_settings
from app.errors import (
    ApiError,
    api_error_handler,
    error_payload,
    unhandled_error_handler,
    validation_error_handler,
)
from app.services.db import db
from app.services.job_store import jobs
from app.services.storage import storage

settings = get_settings()

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def _cleanup_loop() -> None:
    interval = max(settings.cleanup_interval_s, 5)
    while True:
        await asyncio.sleep(interval)
        storage.sweep()
        jobs.sweep()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.configure(settings.database_path)
    storage.configure(settings.temp_dir, settings.retention_minutes)
    storage.load()
    storage.reconcile()
    storage.sweep()
    jobs.configure(settings.retention_minutes)
    jobs.load()
    jobs.sweep()
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)


@app.middleware("http")
async def require_access_token(request: Request, call_next):
    """Optional shared-secret gate (enabled only when APP_ACCESS_TOKEN is set)."""
    token = settings.app_access_token
    if token and request.url.path != "/health":
        provided = request.headers.get("X-App-Token") or request.query_params.get(
            "token"
        )
        if provided != token:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    "unauthorized",
                    "A valid access token is required.",
                    getattr(request.state, "request_id", "req_unknown"),
                ),
            )
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Server-Timing"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health_router)
app.include_router(documents_router)
app.include_router(study_router)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    supplied = (request.headers.get("X-Request-ID") or "").strip()
    request.state.request_id = (
        supplied
        if _REQUEST_ID_RE.fullmatch(supplied)
        else f"req_{uuid.uuid4().hex[:12]}"
    )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
