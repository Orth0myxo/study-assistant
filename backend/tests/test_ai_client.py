from __future__ import annotations

import logging

import httpx
import pytest

from app.config import get_settings
from app.services.ai_client import (
    AiInvalidResponse,
    AiNotConfigured,
    AiProviderError,
    AiRateLimited,
    AiTimeout,
    GeminiClient,
    normalize_summary,
)


def _settings(monkeypatch, **overrides):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")
    monkeypatch.setattr(settings, "gemini_max_retries", 0)
    monkeypatch.setattr(settings, "gemini_retry_backoff_s", 0.0)
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)
    return settings


def _client(monkeypatch, handler, **overrides):
    settings = _settings(monkeypatch, **overrides)
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return GeminiClient(settings, http_client=http_client), settings


def _success(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


def _client_with_sleep(monkeypatch, handler, sleeps, **overrides):
    settings = _settings(monkeypatch, **overrides)
    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = GeminiClient(settings, http_client=http_client, sleep=sleeps.append)
    return client, settings


def test_success_returns_text_and_sends_key_header(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success("Grounded summary.")

    client, _ = _client(monkeypatch, handler)

    result = client.summarize(
        source_text="Source page text.", source_label="Page 1"
    )

    assert result == "Grounded summary."
    assert seen[0].headers["x-goog-api-key"] == "test-key"
    assert ":generateContent" in str(seen[0].url)
    body = seen[0].content.decode()
    assert "SOURCE TEXT" in body
    assert "Source page text." in body


def test_topic_prompt_uses_source_only_grounding(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success("Study material.")

    client, _ = _client(monkeypatch, handler)

    client.summarize(
        source_text="[Page 1]\nOnly uploaded source text.",
        source_label="Topic: Calvin cycle; Page 1",
        purpose="topic",
    )

    body = seen[0].content.decode()
    assert "SOURCE PASSAGES" in body
    assert "Only uploaded source text." in body
    assert "Source-grounded study notes" not in body
    assert "Use ONLY the source text provided by the user." in body


def test_beyond_source_prompt_is_explicit_and_separate(monkeypatch):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _success("Optional background.")

    client, _ = _client(monkeypatch, handler)

    client.summarize(
        source_text="Source-grounded study notes.",
        source_label="Calvin cycle",
        purpose="topic_beyond",
    )

    body = seen[0].content.decode()
    assert "explicitly opted in" in body
    assert "SOURCE-GROUNDED STUDY NOTES" in body
    assert "You may use general knowledge" in body
    assert "Never claim the extra background is from the" in body
    assert "Use ONLY the source text provided by the user." not in body


def test_missing_key_raises_not_configured():
    settings = get_settings()
    settings = settings.model_copy(update={"gemini_api_key": None})
    with pytest.raises(AiNotConfigured):
        GeminiClient(settings)


def test_rate_limit_is_reported(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "quota"}})

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiRateLimited):
        client.summarize(source_text="text", source_label="Page 1")


def test_server_error_is_reported(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiProviderError):
        client.summarize(source_text="text", source_label="Page 1")


def test_timeout_is_reported(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow")

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiTimeout):
        client.summarize(source_text="text", source_label="Page 1")


def test_malformed_json_is_reported(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiInvalidResponse):
        client.summarize(source_text="text", source_label="Page 1")


def test_empty_candidate_is_invalid(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": []})

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiInvalidResponse):
        client.summarize(source_text="text", source_label="Page 1")


def test_blocked_response_is_invalid(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "promptFeedback": {"blockReason": "SAFETY"},
                "candidates": [],
            },
        )

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiInvalidResponse):
        client.summarize(source_text="text", source_label="Page 1")


def test_retry_then_success(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return _success("Recovered summary.")

    client, _ = _client(monkeypatch, handler, gemini_max_retries=1)

    assert client.summarize(source_text="text", source_label="Page 1") == (
        "Recovered summary."
    )
    assert attempts["count"] == 2


def test_client_error_is_not_retried(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    client, _ = _client(monkeypatch, handler, gemini_max_retries=3)

    with pytest.raises(AiProviderError):
        client.summarize(source_text="text", source_label="Page 1")
    assert attempts["count"] == 1


def test_empty_source_raises_invalid(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _success("unused")

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiInvalidResponse):
        client.summarize(source_text="   ", source_label="Page 1")


def test_rate_limit_uses_provider_retry_delay(monkeypatch):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": "7s",
                        },
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "reason": "RATE_LIMIT_EXCEEDED",
                            "violations": [
                                {
                                    "quotaMetric": "generativelanguage.googleapis.com/"
                                    "generate_content_free_tier_requests"
                                }
                            ],
                        },
                    ],
                }
            },
        )

    client, _ = _client_with_sleep(
        monkeypatch,
        handler,
        sleeps,
        gemini_max_retries=2,
        gemini_retry_backoff_s=0.0,
    )

    with pytest.raises(AiRateLimited) as excinfo:
        client.summarize(source_text="text", source_label="Page 1")

    assert excinfo.value.retryable is True
    assert excinfo.value.retry_after == 7.0
    assert sleeps == [7.0, 7.0]


def test_rate_limit_uses_retry_after_header(monkeypatch):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"Retry-After": "3"},
            json={"error": {"status": "RESOURCE_EXHAUSTED"}},
        )

    client, _ = _client_with_sleep(
        monkeypatch,
        handler,
        sleeps,
        gemini_max_retries=1,
        gemini_retry_backoff_s=0.0,
    )

    with pytest.raises(AiRateLimited) as excinfo:
        client.summarize(source_text="text", source_label="Page 1")

    assert excinfo.value.retry_after == 3.0
    assert sleeps == [3.0]


def test_rate_limit_retry_delay_is_capped(monkeypatch):
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"details": [{"retryDelay": "9999s"}]}},
        )

    client, _ = _client_with_sleep(
        monkeypatch,
        handler,
        sleeps,
        gemini_max_retries=1,
        gemini_retry_backoff_s=0.0,
    )

    with pytest.raises(AiRateLimited):
        client.summarize(source_text="text", source_label="Page 1")

    assert sleeps == [60.0]


def test_quota_exhaustion_is_not_retried(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaId": "GenerateRequestsPerDayPerProjectPer"
                                    "Model-FreeTier",
                                    "quotaMetric": "generativelanguage.googleapis."
                                    "com/generate_content_free_tier_requests",
                                }
                            ],
                        }
                    ],
                }
            },
        )

    client, _ = _client(monkeypatch, handler, gemini_max_retries=3)

    with pytest.raises(AiRateLimited) as excinfo:
        client.summarize(source_text="text", source_label="Page 1")

    assert attempts["count"] == 1
    assert excinfo.value.retryable is False
    assert "quota" in excinfo.value.message.lower()


def test_rate_limit_is_retried_when_cause_is_ambiguous(monkeypatch):
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}
        )

    client, _ = _client(monkeypatch, handler, gemini_max_retries=2)

    with pytest.raises(AiRateLimited) as excinfo:
        client.summarize(source_text="text", source_label="Page 1")

    assert attempts["count"] == 3
    assert excinfo.value.retryable is True


def test_rate_limit_logs_diagnostic_without_secrets(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [{"reason": "RATE_LIMIT_EXCEEDED"}],
                }
            },
        )

    client, _ = _client(monkeypatch, handler)

    with caplog.at_level(logging.INFO, logger="app.services.ai_client"):
        with pytest.raises(AiRateLimited):
            client.summarize(
                source_text="secret lecture text", source_label="Page 1"
            )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "RESOURCE_EXHAUSTED" in logged
    assert "RATE_LIMIT_EXCEEDED" in logged
    assert "test-key" not in logged
    assert "secret lecture text" not in logged



def test_markdown_summary_is_normalized(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _success(
            "## Summary\n\n"
            "**Key points**\n"
            "- Mitochondria produce ATP.\n"
            "* Prokaryotes lack a nucleus.\n"
            "Use `snake_case_names` and a *key* term."
        )

    client, _ = _client(monkeypatch, handler)

    assert client.summarize(source_text="text", source_label="Page 1") == (
        "Key points\n"
        "- Mitochondria produce ATP.\n"
        "- Prokaryotes lack a nucleus.\n"
        "Use snake_case_names and a key term."
    )


def test_normalize_summary_keeps_paragraphs_and_numbered_lists():
    assert normalize_summary(
        "\n\nOverview:\n\n"
        "First paragraph with *emphasis* and 2 * 3 = 6.\n\n\n"
        "1. Step one\n"
        "2. Step two\n"
    ) == (
        "First paragraph with emphasis and 2 * 3 = 6.\n\n"
        "1. Step one\n2. Step two"
    )


def test_markdown_only_response_is_invalid(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return _success("```\n\n```")

    client, _ = _client(monkeypatch, handler)

    with pytest.raises(AiInvalidResponse):
        client.summarize(source_text="text", source_label="Page 1")
