from __future__ import annotations

import asyncio
import logging
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
from app.api.topic_sources import router as topic_sources_router
from app.api.users import router as users_router
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
from app.services.topic_images import configure_ocr_concurrency
from app.services.topic_results import topic_results
from app.services.topic_sources import topic_sources
from app.services.users import users as user_store

settings = get_settings()
logger = logging.getLogger(__name__)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


async def _cleanup_loop() -> None:
    interval = max(settings.cleanup_interval_s, 5)
    while True:
        await asyncio.sleep(interval)
        for name, sweep in (
            ("storage", storage.sweep),
            ("jobs", jobs.sweep),
            ("topic_sources", topic_sources.sweep),
            ("topic_results", topic_results.sweep),
        ):
            try:
                sweep()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                logger.warning(
                    "cleanup sweep failed store=%s error_type=%s",
                    name,
                    type(exc).__name__,
                )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.configure(settings.database_path)
    storage.configure(settings.temp_dir, settings.retention_minutes)
    storage.load()
    storage.reconcile()
    storage.sweep()
    topic_sources.configure(
        settings.temp_dir,
        settings.retention_minutes,
        settings.topic_max_sources_per_user,
    )
    topic_sources.sweep()
    configure_ocr_concurrency(settings.topic_ocr_max_concurrency)
    topic_results.configure(settings.retention_minutes)
    topic_results.sweep()
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


def _requires_user_identity(path: str) -> bool:
    """Data endpoints require a per-user token.

    The application token is only an outer gate; it is shared by every install
    and must never be treated as proof of who the caller is. Registration is
    exempt so a device can obtain its own identity.
    """
    return (
        path.startswith("/api/v1/")
        and path.rstrip("/") != "/api/v1/users"
    )


@app.middleware("http")
async def require_access_token(request: Request, call_next):
    """Optional shared-secret gate plus mandatory per-user identity.

    A valid per-install bearer token is sufficient for API data requests. This
    lets the mobile app bootstrap and operate without embedding the shared
    application secret. If a client does send ``X-App-Token``, it must still be
    correct. Registration is exempt so a device can obtain its own identity.
    """
    path = request.url.path
    requires_user = _requires_user_identity(path)
    user_id: str | None = None

    if requires_user:
        authorization = request.headers.get("authorization") or ""
        provided_user = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else ""
        )
        user_id = user_store.resolve(provided_user)

    token = settings.app_access_token
    registration = (
        request.method == "POST" and path.rstrip("/") == "/api/v1/users"
    )
    if token and path != "/health" and not registration:
        provided = request.headers.get("X-App-Token") or request.query_params.get(
            "token"
        )
        if provided is not None:
            if provided != token:
                return JSONResponse(
                    status_code=401,
                    headers={"WWW-Authenticate": 'AppToken realm="app"'},
                    content=error_payload(
                        "unauthorized",
                        "A valid access token is required.",
                        getattr(request.state, "request_id", "req_unknown"),
                    ),
                )
        elif user_id is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": 'AppToken realm="app"'},
                content=error_payload(
                    "unauthorized",
                    "A valid access token is required.",
                    getattr(request.state, "request_id", "req_unknown"),
                ),
            )

    if requires_user:
        if user_id is None:
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="user"'},
                content=error_payload(
                    "invalid_user_token",
                    "A valid user token is required.",
                    getattr(request.state, "request_id", "req_unknown"),
                ),
            )
        request.state.user_id = user_id

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Server-Timing", "WWW-Authenticate"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health_router)
app.include_router(users_router)
app.include_router(documents_router)
app.include_router(study_router)
app.include_router(topic_sources_router)


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
