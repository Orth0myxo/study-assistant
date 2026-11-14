"""Timing output and the short chat-command timeout."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.config import get_settings
from app.schemas.chat_command import ChatCommandRequest
from app.services import chat_commands, timing


def test_server_timing_header_sums_calls_per_provider():
    header = timing.server_timing_header(
        812.34, [("groq", 100.0), ("gemini", 50.5), ("groq", 25.0)]
    )
    assert header == "total;dur=812.3, groq;dur=125.0, gemini;dur=50.5"


def test_server_timing_header_without_ai_calls():
    assert timing.server_timing_header(5.0, []) == "total;dur=5.0"


def test_provider_call_is_logged_without_user_content(caplog):
    caplog.set_level(logging.INFO, logger="app.timing")
    timing.begin_request()
    started = time.perf_counter() - 0.5
    seconds = timing.log_provider_call("groq", "some-model", "command", started, "http_200")
    assert seconds >= 0.5
    text = caplog.text
    assert "provider=groq" in text
    assert "model=some-model" in text
    assert "purpose=command" in text
    assert "outcome=http_200" in text


def test_logging_outside_a_request_does_not_fail():
    timing._request_calls.set(None)
    timing.log_provider_call("gemini", None, "summary", time.perf_counter(), "timeout")


# --- the middleware, exercised through a real Starlette app --------------------


def _app(slow_ms: float) -> Starlette:
    def fast(request):
        return PlainTextResponse("ok")

    def with_ai_call(request):
        # Sync endpoints run in a worker thread; the timing list must still be shared.
        timing.log_provider_call("groq", "m", "complete", time.perf_counter() - 0.2, "http_200")
        return PlainTextResponse("ok")

    async def slow(request):
        await asyncio.sleep(0.05)
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/fast", fast),
            Route("/ai", with_ai_call),
            Route("/slow", slow),
        ]
    )

    async def middleware(request, call_next):
        return await timing.measure_request(request, call_next, slow_ms=slow_ms)

    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    return app


def _get(app: Starlette, path: str, query: str = "") -> tuple[int, dict[str, str]]:
    async def run():
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [],
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
            "root_path": "",
        }
        await app(scope, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        headers = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        return start["status"], headers

    return asyncio.run(run())


def test_middleware_adds_server_timing_header():
    status, headers = _get(_app(slow_ms=10_000), "/fast")
    assert status == 200
    assert headers["server-timing"].startswith("total;dur=")


def test_ai_call_in_a_worker_thread_shows_up_in_the_header():
    _, headers = _get(_app(slow_ms=10_000), "/ai")
    assert "groq;dur=" in headers["server-timing"]
    groq_ms = float(headers["server-timing"].split("groq;dur=")[1].split(",")[0])
    assert groq_ms >= 200.0


def test_slow_request_is_logged_by_path_only(caplog):
    caplog.set_level(logging.WARNING, logger="app.timing")
    _get(_app(slow_ms=10), "/slow", query="token=SECRET123")
    assert "slow request" in caplog.text
    assert "path=/slow" in caplog.text
    assert "SECRET123" not in caplog.text


def test_fast_request_is_not_logged(caplog):
    caplog.set_level(logging.WARNING, logger="app.timing")
    _get(_app(slow_ms=10_000), "/fast")
    assert "slow request" not in caplog.text


# --- chat command timeout ------------------------------------------------------


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}]}


class _RecordingClient:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.timeouts: list[float] = []

    def post(self, url, *, json, headers, timeout):
        self.timeouts.append(timeout)
        if self.error:
            raise self.error
        return _FakeResponse()

    def close(self):
        pass


def _settings(**update):
    base = {"groq_api_key": "key", "groq_command_model": "small-model"}
    base.update(update)
    return get_settings().model_copy(update=base)


def _request() -> ChatCommandRequest:
    return ChatCommandRequest.model_validate(
        {
            "message": "Cervical smear",
            "context": {"document_names": ["a.pdf"], "page_count": 3, "doc_format": "pdf"},
        }
    )


def test_command_step_uses_its_own_short_timeout_not_the_60s_one():
    settings = _settings(groq_command_timeout_s=8.0, groq_request_timeout_s=60)
    client = _RecordingClient()
    with pytest.raises(Exception):
        # The fake returns "{}", which is not a valid command; only the timeout matters.
        chat_commands.groq_command(_request(), settings, http_client=client)
    assert client.timeouts == [8.0]


def test_command_timeout_falls_back_to_the_local_parser(monkeypatch):
    import httpx

    settings = _settings(groq_command_timeout_s=0.5)
    client = _RecordingClient(error=httpx.TimeoutException("slow"))

    real = chat_commands.groq_command
    monkeypatch.setattr(
        chat_commands,
        "groq_command",
        lambda payload, s, **kw: real(payload, s, http_client=client),
    )
    command = chat_commands.resolve_command(_request(), settings)
    assert client.timeouts == [0.5]
    assert command.action == "extract"
    assert command.selector.topic == "Cervical smear"


def test_default_command_timeout_is_below_the_apps_15s_wait():
    assert get_settings().groq_command_timeout_s < 15
