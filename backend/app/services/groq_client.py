"""Groq (OpenAI-compatible chat completions) client.

``GroqClient.summarize`` implements the same ``AiClient`` protocol as
``GeminiClient`` and reuses the existing grounded prompts unchanged. The lower
level ``complete`` method is also used by the chat command step.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Union

import httpx

from app.services.ai_client import (
    BEYOND_SOURCE_PROMPT,
    BEYOND_SOURCE_SYSTEM_PROMPT,
    GROUNDED_SYSTEM_PROMPT,
    MAX_RETRY_DELAY_S,
    OVERVIEW_PROMPT,
    SECTION_PROMPT,
    TOPIC_PROMPT,
    AiError,
    AiInvalidResponse,
    AiNotConfigured,
    AiProviderError,
    AiRateLimited,
    AiTimeout,
    _parse_retry_delay,
    normalize_summary,
)
from app.services.provider_settings import ProviderSettings

logger = logging.getLogger(__name__)

_DAILY_MARKERS = ("per day", "(tpd)", "(rpd)", "daily", "per_day", "per-day")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def build_prompts(purpose: str, text: str, source_label: str) -> tuple[str, str]:
    """Return ``(user_prompt, system_prompt)`` using the existing prompt set."""
    if purpose == "overview":
        return OVERVIEW_PROMPT.format(source_text=text), GROUNDED_SYSTEM_PROMPT
    if purpose == "topic":
        return (
            TOPIC_PROMPT.format(source_label=source_label, source_text=text),
            GROUNDED_SYSTEM_PROMPT,
        )
    if purpose == "topic_beyond":
        return (
            BEYOND_SOURCE_PROMPT.format(source_label=source_label, source_text=text),
            BEYOND_SOURCE_SYSTEM_PROMPT,
        )
    return (
        SECTION_PROMPT.format(source_label=source_label, source_text=text),
        GROUNDED_SYSTEM_PROMPT,
    )


class GroqClient:
    """Small Groq client over httpx with retry/backoff and 429 classification."""

    def __init__(
        self,
        provider: ProviderSettings,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not provider.groq_api_key or not provider.groq_model:
            raise AiNotConfigured(
                "Groq is not configured on the server.",
                code="ai_not_configured",
                status_code=503,
            )
        self._provider = provider
        self._client = http_client or httpx.Client()
        self._owns_client = http_client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -- AiClient protocol -------------------------------------------------

    def summarize(
        self, *, source_text: str, source_label: str, purpose: str = "section"
    ) -> str:
        text = (source_text or "").strip()
        if not text:
            raise AiInvalidResponse(
                "There was no source text to summarise.",
                code="ai_invalid_response",
                status_code=502,
            )
        prompt, system_prompt = build_prompts(purpose, text, source_label)
        raw = self.complete(system_prompt, prompt)
        normalized = normalize_summary(raw)
        if not normalized:
            raise AiInvalidResponse(
                "The AI provider returned an empty response.",
                code="ai_invalid_response",
                status_code=502,
            )
        return normalized

    # -- generic completion ------------------------------------------------

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
        timeout_s: float | None = None,
        retries: int | None = None,
    ) -> str:
        provider = self._provider
        url = f"{provider.groq_base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": model or provider.groq_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens or provider.groq_max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if str(payload["model"]).startswith("openai/gpt-oss"):
            # Reasoning models spend completion tokens thinking; keep that small
            # so short structured answers are not cut off.
            payload["reasoning_effort"] = "low"
        headers = {
            "Authorization": f"Bearer {provider.groq_api_key}",
            "Content-Type": "application/json",
        }

        attempts = max(
            provider.groq_max_retries if retries is None else retries, 0
        ) + 1
        last_error: AiError | None = None
        for attempt in range(attempts):
            result = self._attempt(url, payload, headers, timeout_s)
            if isinstance(result, str):
                return result
            last_error = result
            if not result.retryable:
                raise result
            if attempt < attempts - 1:
                delay = provider.groq_retry_backoff_s * (2**attempt)
                if result.retry_after is not None:
                    delay = min(result.retry_after, MAX_RETRY_DELAY_S)
                if delay > 0:
                    self._sleep(delay)
        if last_error is not None:
            raise last_error
        raise AiProviderError("The AI provider could not be reached.")

    def _attempt(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_s: float | None = None,
    ) -> Union[str, AiError]:
        try:
            response = self._client.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout_s or self._provider.groq_request_timeout_s,
            )
        except httpx.TimeoutException:
            return AiTimeout(
                "The AI provider timed out. Please try again.",
                code="ai_timeout",
                status_code=504,
                retryable=True,
            )
        except httpx.HTTPError:
            return AiProviderError(
                "The AI provider could not be reached.",
                code="ai_provider_error",
                status_code=502,
                retryable=True,
            )

        status = response.status_code
        if status == 429:
            return self._rate_limited(response)
        if status >= 500:
            return AiProviderError(
                "The AI provider returned a server error. Please try again later.",
                code="ai_provider_error",
                status_code=502,
                retryable=True,
            )
        if status >= 400:
            return AiProviderError(
                _error_message(response),
                code="ai_provider_error",
                status_code=502,
                retryable=False,
            )
        return self._parse(response)

    def _rate_limited(self, response: httpx.Response) -> AiRateLimited:
        message = _error_text(response).lower()
        quota_exhausted = any(marker in message for marker in _DAILY_MARKERS)
        retry_after = _parse_retry_delay(response.headers.get("retry-after"))
        too_long = (
            retry_after is not None
            and retry_after > self._provider.groq_max_wait_s
        )
        retryable = not quota_exhausted and not too_long
        logger.info(
            "Groq 429 quota_exhausted=%s retry_after=%s retryable=%s",
            quota_exhausted,
            retry_after,
            retryable,
        )
        if quota_exhausted:
            text = "The AI provider's daily limit has been reached."
        else:
            text = "The AI provider is rate limiting requests. Please try again soon."
        return AiRateLimited(
            text,
            code="ai_rate_limited",
            status_code=429,
            retryable=retryable,
            retry_after=retry_after,
        )

    def _parse(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise AiInvalidResponse(
                "The AI provider returned a malformed response.",
                code="ai_invalid_response",
                status_code=502,
            ) from exc
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise AiInvalidResponse(
                "The AI provider returned no result.",
                code="ai_invalid_response",
                status_code=502,
            )
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        text = content if isinstance(content, str) else ""
        text = _THINK_BLOCK.sub("", text).strip()
        if not text:
            raise AiInvalidResponse(
                "The AI provider returned an empty response.",
                code="ai_invalid_response",
                status_code=502,
            )
        return text


def _error_text(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return ""


def _error_message(response: httpx.Response) -> str:
    detail = _error_text(response)
    if detail:
        return f"The AI provider rejected the request: {detail}"
    return "The AI provider rejected the request."
