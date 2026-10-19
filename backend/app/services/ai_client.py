from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator, NamedTuple, Protocol, Union

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

RATE_LIMITED_MESSAGE = (
    "The AI provider is rate limiting requests. Please try again later."
)
QUOTA_EXHAUSTED_MESSAGE = (
    "The AI provider's request quota appears to be exhausted. Please try again "
    "later, or use a key or plan with more capacity."
)
MAX_RETRY_DELAY_S = 60.0
_QUOTA_MARKERS = ("per_day", "per-day", "perday", "daily")

GROUNDED_SYSTEM_PROMPT = (
    "You are a study aid that summarises lecture material. "
    "Use ONLY the source text provided by the user. "
    "Do not add facts, examples, citations, numbers, or advice that are not present "
    "in the source text. "
    "If the source text does not contain the requested information, say so plainly. "
    "Preserve important numbers, units, names, and definitions exactly as written. "
    "This is not a clinical tool: do not give diagnoses, dosages, treatments, or "
    "medical advice. "
    "Return plain text only, with no markdown headings and no preamble."
)

SECTION_PROMPT = (
    "Summarise the following study material for a student.\n"
    "Source reference: {source_label}\n\n"
    "SOURCE TEXT:\n"
    "{source_text}\n\n"
    "Write a concise, faithful summary (a short paragraph or a few bullet points) "
    "using only the source text above."
)

OVERVIEW_PROMPT = (
    "The following are summaries of the sections of a study document. "
    "Combine them into one coherent overview for the student.\n\n"
    "SECTION SUMMARIES:\n"
    "{source_text}\n\n"
    "Use only the information in these summaries. Do not add anything new."
)


class AiError(Exception):
    code = "ai_error"
    status_code = 502
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable


class AiNotConfigured(AiError):
    code = "ai_not_configured"
    status_code = 503


class AiRateLimited(AiError):
    code = "ai_rate_limited"
    status_code = 429
    retryable = True


class AiTimeout(AiError):
    code = "ai_timeout"
    status_code = 504
    retryable = True


class AiProviderError(AiError):
    code = "ai_provider_error"
    status_code = 502


class AiInvalidResponse(AiError):
    code = "ai_invalid_response"
    status_code = 502


class AiClient(Protocol):
    def summarize(
        self, *, source_text: str, source_label: str, purpose: str = "section"
    ) -> str: ...


def _provider_message(response: httpx.Response) -> str:
    try:
        payload: Any = response.json()
    except ValueError:
        return "The AI provider rejected the request."
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return f"The AI provider rejected the request: {error['message']}"
    return "The AI provider rejected the request."


class _RateLimitInfo(NamedTuple):
    message: str
    retry_after: float | None
    retryable: bool
    diagnostic: str


def _parse_retry_delay(value: Any) -> float | None:
    """Parse a provider retry delay (seconds or a ``"12.5s"`` duration) safely."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        text = value.strip().lower()
        try:
            if text.endswith("ms"):
                seconds = float(text[:-2]) / 1000.0
            elif text.endswith("s"):
                seconds = float(text[:-1])
            else:
                seconds = float(text)
        except ValueError:
            return None
    else:
        return None
    return seconds if seconds >= 0 else None


def _iter_leaf_strings(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_leaf_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_leaf_strings(item)
    elif isinstance(value, str):
        yield value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield str(value)


def _describe_rate_limit(response: httpx.Response) -> _RateLimitInfo:
    """Classify a Gemini 429 without exposing secrets or document content.

    Only provider diagnostic metadata (error status, detail reasons, quota
    metrics, and retry delay) is inspected. The request body, headers, API key,
    and study text are never read or logged.
    """
    status: str | None = None
    reasons: list[str] = []
    retry_after = _parse_retry_delay(response.headers.get("retry-after"))
    quota_exhausted = False

    try:
        payload: Any = response.json()
    except ValueError:
        payload = None

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        raw_status = error.get("status")
        if isinstance(raw_status, str):
            status = raw_status
        details = error.get("details")
        if isinstance(details, list):
            blob = " ".join(_iter_leaf_strings(details)).lower()
            quota_exhausted = any(marker in blob for marker in _QUOTA_MARKERS)
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                reason = detail.get("reason")
                if isinstance(reason, str) and reason:
                    reasons.append(reason)
                if retry_after is None:
                    retry_after = _parse_retry_delay(detail.get("retryDelay"))

    diagnostic = (
        f"status={status or 'unknown'} reasons={','.join(reasons) or 'none'} "
        f"quota_exhausted={quota_exhausted} retry_after={retry_after}"
    )
    if quota_exhausted:
        return _RateLimitInfo(QUOTA_EXHAUSTED_MESSAGE, retry_after, False, diagnostic)
    return _RateLimitInfo(RATE_LIMITED_MESSAGE, retry_after, True, diagnostic)


class GeminiClient:
    """Small Gemini Developer API (generateContent) client over the existing httpx dep."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.gemini_api_key or not settings.gemini_model:
            raise AiNotConfigured(
                "AI summarization is not configured on the server.",
                code="ai_not_configured",
                status_code=503,
            )
        self._settings = settings
        self._client = http_client or httpx.Client()
        self._owns_client = http_client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

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
        if purpose == "overview":
            prompt = OVERVIEW_PROMPT.format(source_text=text)
        else:
            prompt = SECTION_PROMPT.format(source_label=source_label, source_text=text)
        return self._generate(prompt)

    def _generate(self, prompt: str) -> str:
        settings = self._settings
        url = (
            f"{settings.gemini_api_base_url.rstrip('/')}/models/"
            f"{settings.gemini_model}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": GROUNDED_SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": settings.gemini_max_output_tokens,
            },
        }
        headers = {
            "x-goog-api-key": settings.gemini_api_key or "",
            "Content-Type": "application/json",
        }

        attempts = max(settings.gemini_max_retries, 0) + 1
        last_error: AiError | None = None
        for attempt in range(attempts):
            result = self._attempt(url, payload, headers)
            if isinstance(result, str):
                return result

            last_error = result
            if not result.retryable:
                raise result
            if attempt < attempts - 1:
                delay = settings.gemini_retry_backoff_s * (2**attempt)
                if result.retry_after is not None:
                    delay = min(result.retry_after, MAX_RETRY_DELAY_S)
                if delay > 0:
                    self._sleep(delay)
        if last_error is not None:
            raise last_error
        raise AiProviderError("The AI provider could not be reached.")

    def _attempt(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> Union[str, AiError]:
        try:
            response = self._client.post(
                url,
                json=payload,
                headers=headers,
                timeout=self._settings.gemini_request_timeout_s,
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

        if response.status_code == 429:
            info = _describe_rate_limit(response)
            logger.info("Gemini 429 response: %s", info.diagnostic)
            return AiRateLimited(
                info.message,
                code="ai_rate_limited",
                status_code=429,
                retryable=info.retryable,
                retry_after=info.retry_after,
            )
        if response.status_code >= 500:
            return AiProviderError(
                "The AI provider returned a server error. Please try again later.",
                code="ai_provider_error",
                status_code=502,
                retryable=True,
            )
        if response.status_code >= 400:
            return AiProviderError(
                _provider_message(response),
                code="ai_provider_error",
                status_code=502,
                retryable=False,
            )
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> str:
        try:
            data = response.json()
        except ValueError as exc:
            raise AiInvalidResponse(
                "The AI provider returned a malformed response.",
                code="ai_invalid_response",
                status_code=502,
            ) from exc
        if not isinstance(data, dict):
            raise AiInvalidResponse(
                "The AI provider returned a malformed response.",
                code="ai_invalid_response",
                status_code=502,
            )

        feedback = data.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise AiInvalidResponse(
                "The AI provider blocked this request.",
                code="ai_invalid_response",
                status_code=502,
            )

        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise AiInvalidResponse(
                "The AI provider returned no summary.",
                code="ai_invalid_response",
                status_code=502,
            )

        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        finish_reason = candidate.get("finishReason")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        text = ""
        if isinstance(parts, list):
            text = "".join(
                part.get("text", "")
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ).strip()

        if not text:
            if finish_reason in {
                "SAFETY",
                "RECITATION",
                "BLOCKLIST",
                "PROHIBITED_CONTENT",
            }:
                raise AiInvalidResponse(
                    "The AI provider blocked this response.",
                    code="ai_invalid_response",
                    status_code=502,
                )
            raise AiInvalidResponse(
                "The AI provider returned an empty response.",
                code="ai_invalid_response",
                status_code=502,
            )

        return text


def get_ai_client(settings: Settings) -> AiClient:
    return GeminiClient(settings)
