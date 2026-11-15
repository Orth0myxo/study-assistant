"""Request and AI-call timing, so slow steps show up in logs and response headers.

Two outputs, neither of which contains user content, document text or tokens:

* one log line per AI provider call (provider, model, purpose, seconds, outcome);
* a ``Server-Timing`` response header (``total;dur=812.3, groq;dur=640.1``) that the
  mobile app already prints in development builds.
"""

from __future__ import annotations

import logging
import time
from contextvars import ContextVar

logger = logging.getLogger("app.timing")

# Set per request by the timing middleware. The list is shared with the endpoint
# even when it runs in a worker thread, because the context copy points to the
# same list object.
_request_calls: ContextVar[list[tuple[str, float]] | None] = ContextVar(
    "request_ai_calls", default=None
)


def begin_request() -> list[tuple[str, float]]:
    calls: list[tuple[str, float]] = []
    _request_calls.set(calls)
    return calls


def log_provider_call(
    provider: str,
    model: str | None,
    purpose: str,
    started: float,
    outcome: str,
) -> float:
    """Log one provider call and add it to the current request's timings."""
    seconds = time.perf_counter() - started
    logger.info(
        "ai call provider=%s model=%s purpose=%s seconds=%.2f outcome=%s",
        provider,
        model or "-",
        purpose,
        seconds,
        outcome,
    )
    calls = _request_calls.get()
    if calls is not None:
        calls.append((provider, seconds * 1000.0))
    return seconds


def server_timing_header(
    total_ms: float,
    calls: list[tuple[str, float]],
    *,
    include_total: bool = True,
) -> str:
    """Format ``Server-Timing``: total plus summed time per provider."""
    per_provider: dict[str, float] = {}
    for name, duration_ms in calls:
        per_provider[name] = per_provider.get(name, 0.0) + duration_ms
    parts = [f"total;dur={total_ms:.1f}"] if include_total else []
    parts.extend(f"{name};dur={ms:.1f}" for name, ms in per_provider.items())
    return ", ".join(parts)


async def measure_request(request, call_next, *, slow_ms: float):
    """Middleware body: add ``Server-Timing`` and log slow requests.

    Only the path is logged, never the query string (it may carry an access token).
    """
    started = time.perf_counter()
    calls = begin_request()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    existing = response.headers.get("Server-Timing")
    if existing:
        # An endpoint (the upload route) already reports its own breakdown,
        # including its own total. Keep it and only add provider timings.
        extra = server_timing_header(elapsed_ms, calls, include_total=False)
        response.headers["Server-Timing"] = f"{existing}, {extra}" if extra else existing
    else:
        response.headers["Server-Timing"] = server_timing_header(elapsed_ms, calls)
    if elapsed_ms >= slow_ms:
        logger.warning(
            "slow request method=%s path=%s status=%s ms=%.0f",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    return response
