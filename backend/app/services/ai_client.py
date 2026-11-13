from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Iterator, NamedTuple, Protocol, Union

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-08-v1"

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
    "Return plain text only. Use short paragraphs separated by blank lines. "
    "Prefix every bullet with '- '. "
    "Do not use markdown headings, bold, italics, code fences, or tables. "
    "Do not start with a title, a preamble, or a line such as 'Summary:'."
)

SECTION_PROMPT = (
    "Summarise the following study material for a student.\n"
    "Source reference: {source_label}\n\n"
    "SOURCE TEXT:\n"
    "{source_text}\n\n"
    "Write a concise, faithful summary using only the source text above. "
    "Use one short paragraph or 3-6 bullet points. Do not repeat the source text."
)

OVERVIEW_PROMPT = (
    "The following are summaries of the sections of a study document. "
    "Combine them into one coherent overview for the student.\n\n"
    "SECTION SUMMARIES:\n"
    "{source_text}\n\n"
    "Use only the information in these summaries. Do not add anything new. "
    "Write one short paragraph of at most 5 sentences."
)

TOPIC_PROMPT = (
    "Create study material for the following topic using ONLY the source passages "
    "below.\n"
    "Topic and source references: {source_label}\n\n"
    "SOURCE PASSAGES:\n"
    "{source_text}\n\n"
    "Explain the key idea clearly for a student, using facts only from these "
    "passages. Use one short paragraph or 3-6 bullet points. Do not copy long "
    "passages verbatim. Do not add examples, facts, page numbers, or citations "
    "that are not in the passages. If the passages do not fully explain the "
    "topic, say what is missing."
)

BEYOND_SOURCE_PROMPT = (
    "The student has explicitly opted in to additional background beyond the "
    "uploaded material.\n"
    "Topic: {source_label}\n\n"
    "SOURCE-GROUNDED STUDY NOTES:\n"
    "{source_text}\n\n"
    "Write a short additional study note that helps the student understand the "
    "topic. You may use general knowledge beyond the uploaded material, but do "
    "not claim this background came from the uploaded material. Do not invent "
    "page numbers, citations, references, or specific source claims. Use one "
    "short paragraph or 3-6 bullet points. Return plain text only."
)

BEYOND_SOURCE_SYSTEM_PROMPT = (
    "You are a study aid. The user has explicitly requested optional AI "
    "background beyond their uploaded material. You may use general knowledge "
    "for that optional section. Never claim the extra background is from the "
    "uploaded material. Never invent page numbers, citations, references, or "
    "document quotes. Do not fabricate specific names, numbers, or study "
    "results. Return plain text only. Use short paragraphs separated by blank "
    "lines. Prefix every bullet with '- '. Do not use markdown headings, bold, "
    "italics, code fences, or tables. Do not start with a title, a preamble, or "
    "a line such as 'Summary:'."
)

_MARKDOWN_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+")
_MARKDOWN_BULLET = re.compile(r"^\s*[-*+\u2022\u2023\u2013\u2014]\s+")
_MARKDOWN_QUOTE = re.compile(r"^\s*>\s?")
_MARKDOWN_RULE = re.compile(r"^\s*[-*_]{3,}\s*$")
_MARKDOWN_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_MARKDOWN_ITALIC = re.compile(r"(?<![\w*])([*_])([^*_\n]*\S)\1(?![\w*])")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LEADING_LABEL = re.compile(r"(?i)^(summary|overview)\s*:?\s*$")
_PREAMBLE = re.compile(
    r"(?i)^(here is|below is)\b.*\b(summary|overview)\b.*:\s*$"
)


def normalize_summary(text: str) -> str:
    """Convert common markdown-ish model output into readable plain text."""
    source = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not source:
        return ""

    lines = source.split("\n")
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    normalized: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            normalized.append("")
            continue
        if _MARKDOWN_RULE.match(line):
            continue
        line = _MARKDOWN_HEADING.sub("", line)
        line = _MARKDOWN_QUOTE.sub("", line)
        line = _MARKDOWN_BOLD.sub(r"\2", line)
        line = _MARKDOWN_ITALIC.sub(r"\2", line)
        line = _INLINE_CODE.sub(r"\1", line)
        line = _MARKDOWN_BULLET.sub("- ", line)
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line in {"-", "*", "+"}:
            continue
        if line:
            normalized.append(line)

    while normalized and (
        not normalized[0]
        or _LEADING_LABEL.match(normalized[0])
        or _PREAMBLE.match(normalized[0])
    ):
        normalized.pop(0)

    collapsed: list[str] = []
    for line in normalized:
        if line == "" and (not collapsed or collapsed[-1] == ""):
            continue
        collapsed.append(line)
    while collapsed and collapsed[-1] == "":
        collapsed.pop()
    return "\n".join(collapsed).strip()


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
            system_prompt = GROUNDED_SYSTEM_PROMPT
        elif purpose == "topic":
            prompt = TOPIC_PROMPT.format(
                source_label=source_label, source_text=text
            )
            system_prompt = GROUNDED_SYSTEM_PROMPT
        elif purpose == "topic_beyond":
            prompt = BEYOND_SOURCE_PROMPT.format(
                source_label=source_label, source_text=text
            )
            system_prompt = BEYOND_SOURCE_SYSTEM_PROMPT
        else:
            prompt = SECTION_PROMPT.format(source_label=source_label, source_text=text)
            system_prompt = GROUNDED_SYSTEM_PROMPT
        return self._generate(prompt, system_prompt=system_prompt)

    def _generate(self, prompt: str, *, system_prompt: str) -> str:
        settings = self._settings
        url = (
            f"{settings.gemini_api_base_url.rstrip('/')}/models/"
            f"{settings.gemini_model}:generateContent"
        )
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
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
                code="ai_quota_exhausted" if not info.retryable else "ai_rate_limited",
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

        normalized = normalize_summary(text)
        if not normalized:
            raise AiInvalidResponse(
                "The AI provider returned an empty response.",
                code="ai_invalid_response",
                status_code=502,
            )
        return normalized


class GroqClient:
    """Groq Chat Completions client using the same grounded purpose prompts."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not settings.groq_api_key or not settings.groq_model:
            raise AiNotConfigured("Groq is not configured.", code="ai_not_configured", status_code=503)
        self._settings = settings
        self._client = http_client or httpx.Client()
        self._owns_client = http_client is None
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def summarize(self, *, source_text: str, source_label: str, purpose: str = "section") -> str:
        text = (source_text or "").strip()
        if not text:
            raise AiInvalidResponse("There was no source text to summarise.", code="ai_invalid_response", status_code=502)
        if purpose == "overview":
            prompt, system = OVERVIEW_PROMPT.format(source_text=text), GROUNDED_SYSTEM_PROMPT
        elif purpose == "topic":
            prompt, system = TOPIC_PROMPT.format(source_label=source_label, source_text=text), GROUNDED_SYSTEM_PROMPT
        elif purpose == "topic_beyond":
            prompt, system = BEYOND_SOURCE_PROMPT.format(source_label=source_label, source_text=text), BEYOND_SOURCE_SYSTEM_PROMPT
        else:
            prompt, system = SECTION_PROMPT.format(source_label=source_label, source_text=text), GROUNDED_SYSTEM_PROMPT
        settings = self._settings
        payload = {
            "model": settings.groq_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": settings.groq_max_output_tokens,
        }
        url = f"{settings.groq_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"}
        last_error: AiError | None = None
        for attempt in range(max(settings.gemini_max_retries, 0) + 1):
            result = self._attempt(url, payload, headers, len(text))
            if isinstance(result, str):
                return result
            last_error = result
            if not result.retryable:
                raise result
            if attempt < settings.gemini_max_retries:
                delay = result.retry_after if result.retry_after is not None else settings.gemini_retry_backoff_s * (2 ** attempt)
                self._sleep(min(delay, MAX_RETRY_DELAY_S))
        if last_error is not None:
            raise last_error
        raise AiProviderError("The AI provider could not be reached.")

    def _attempt(self, url: str, payload: dict[str, Any], headers: dict[str, str], source_chars: int) -> str | AiError:
        try:
            response = self._client.post(url, json=payload, headers=headers, timeout=self._settings.groq_request_timeout_s)
        except httpx.TimeoutException:
            return AiTimeout("The AI provider timed out. Please try again.", code="ai_timeout", status_code=504, retryable=True)
        except httpx.HTTPError:
            return AiProviderError("The AI provider could not be reached.", code="ai_provider_error", status_code=502, retryable=True)
        if response.status_code == 429:
            body = response.text.lower()
            retry_after = _parse_retry_delay(response.headers.get("retry-after"))
            daily = any(marker in body for marker in ("per_day", "per-day", "daily", "requests per day", "tokens per day"))
            logger.info("Groq rate limit source_chars=%d daily_quota=%s retry_after=%s", source_chars, daily, retry_after)
            return AiRateLimited(
                QUOTA_EXHAUSTED_MESSAGE if daily else RATE_LIMITED_MESSAGE,
                code="ai_quota_exhausted" if daily else "ai_rate_limited",
                status_code=429,
                retryable=not daily,
                retry_after=retry_after,
            )
        if response.status_code >= 500:
            return AiProviderError("The AI provider returned a server error. Please try again later.", code="ai_provider_error", status_code=502, retryable=True)
        if response.status_code >= 400:
            return AiProviderError(_provider_message(response), code="ai_provider_error", status_code=502, retryable=False)
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            return AiInvalidResponse("The AI provider returned a malformed response.", code="ai_invalid_response", status_code=502)
        if not isinstance(text, str) or not normalize_summary(text):
            return AiInvalidResponse("The AI provider returned an empty response.", code="ai_invalid_response", status_code=502)
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict):
            logger.info("Groq request completed source_chars=%d prompt_tokens=%s completion_tokens=%s", source_chars, usage.get("prompt_tokens"), usage.get("completion_tokens"))
        else:
            logger.info("Groq request completed source_chars=%d", source_chars)
        return normalize_summary(text)


class FallbackAiClient:
    def __init__(self, clients: list[AiClient]) -> None:
        self._clients = clients

    def summarize(self, *, source_text: str, source_label: str, purpose: str = "section") -> str:
        if not self._clients:
            raise AiNotConfigured("AI summarization is not configured on the server.", code="ai_not_configured", status_code=503)
        errors: list[AiError] = []
        for client in self._clients:
            try:
                return client.summarize(source_text=source_text, source_label=source_label, purpose=purpose)
            except (AiRateLimited, AiTimeout, AiProviderError, AiInvalidResponse) as exc:
                errors.append(exc)
        last = errors[-1]
        codes = [error.code for error in errors]
        if codes and all(code == "ai_quota_exhausted" for code in codes):
            raise AiRateLimited(
                "The AI provider quota is exhausted. Original source text is still available. Try again after the quota resets.",
                code="ai_quota_exhausted",
                status_code=429,
                retryable=False,
            ) from last
        if codes and all(code in {"ai_quota_exhausted", "ai_rate_limited"} for code in codes):
            raise AiRateLimited(
                "AI providers are rate limiting requests. Original source text is still available. Please try again shortly.",
                code="ai_rate_limited",
                status_code=429,
                retryable=True,
            ) from last
        if codes and len(set(codes)) == 1:
            raise last
        raise AiProviderError(
            "All configured AI providers failed. Original source text is still available. Try again later.",
            code="ai_provider_error",
            status_code=502,
        ) from last


def get_ai_client(settings: Settings) -> AiClient:
    clients: list[AiClient] = []
    for provider in settings.ai_provider_order.split(","):
        name = provider.strip().lower()
        try:
            if name == "groq" and settings.groq_api_key and settings.groq_model:
                clients.append(GroqClient(settings))
            elif name == "gemini" and settings.gemini_api_key and settings.gemini_model:
                clients.append(GeminiClient(settings))
        except AiNotConfigured:
            continue
    return FallbackAiClient(clients)
