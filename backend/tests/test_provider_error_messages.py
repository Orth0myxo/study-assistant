"""Errors from the provider chain name the providers that failed."""

from __future__ import annotations

from app.services.ai_chain import AiAllProvidersFailed
from app.services.ai_client import AiProviderError, AiRateLimited, AiTimeout


def _quota(message: str = "daily limit") -> AiRateLimited:
    error = AiRateLimited(message, code="ai_quota_exhausted", retryable=False)
    error.quota_exhausted = True
    return error


def test_quota_message_names_every_provider():
    failure = AiAllProvidersFailed([("groq", _quota()), ("gemini", _quota())])

    assert failure.code == "ai_quota_exhausted"
    assert failure.status_code == 429
    assert "groq, gemini" in failure.message
    assert "Original source text is still available" in failure.message


def test_rate_limit_message_names_the_provider_and_uses_singular_verb():
    failure = AiAllProvidersFailed([("groq", AiRateLimited("slow down"))])

    assert failure.code == "ai_rate_limited"
    assert failure.message.startswith("groq is rate limiting requests.")


def test_rate_limit_message_uses_plural_for_two_providers():
    failure = AiAllProvidersFailed(
        [("groq", AiRateLimited("slow down")), ("gemini", _quota())]
    )

    assert failure.code == "ai_rate_limited"
    assert failure.message.startswith("groq, gemini are rate limiting requests.")


def test_rate_limit_message_uses_the_plural_when_one_provider_fails_twice():
    failure = AiAllProvidersFailed(
        [("groq", AiRateLimited("slow down")), ("groq", AiRateLimited("slow down"))]
    )

    assert failure.message.startswith("groq is rate limiting requests.")


def test_same_error_from_every_provider_keeps_the_code_and_adds_provider_names():
    timeout = AiTimeout("The AI provider timed out. Please try again.", code="ai_timeout", status_code=504)
    failure = AiAllProvidersFailed([("groq", timeout), ("gemini", timeout)])

    assert failure.code == "ai_timeout"
    assert failure.status_code == 504
    assert failure.message == (
        "The AI provider timed out. Please try again. (provider: groq, gemini)"
    )


def test_mixed_failures_list_each_provider_and_its_code():
    timeout = AiTimeout("timed out", code="ai_timeout", status_code=504)
    down = AiProviderError("offline", code="ai_provider_error")
    failure = AiAllProvidersFailed([("groq", timeout), ("gemini", down)])

    assert failure.code == "ai_provider_error"
    assert failure.status_code == 502
    assert "groq: ai_timeout, gemini: ai_provider_error" in failure.message
    assert "Original source text is still available" in failure.message


def test_a_repeated_provider_is_named_once():
    failure = AiAllProvidersFailed([("groq", _quota()), ("groq", _quota())])

    assert failure.message.count("groq") == 1
