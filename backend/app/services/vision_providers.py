"""Provider interface and bounded clients for image-reading tasks."""

from __future__ import annotations

import base64
import json
import logging
import time
import threading
from collections import deque
from typing import Any, Protocol

import httpx

from app.config import Settings
from app.services.ai_client import (
    AiError,
    AiInvalidResponse,
    AiNotConfigured,
    AiProviderError,
    AiRateLimited,
    AiTimeout,
    GeminiClient,
    _parse_retry_delay,
)
from app.services.timing import log_provider_call

logger = logging.getLogger(__name__)

_MAX_REQUEST_IMAGE_BYTES = 14 * 1024 * 1024
_GROQ_VISION_TPM_BUDGET = 7000
_GROQ_VISION_WINDOW_S = 60.0
_GROQ_VISION_RESERVATIONS: deque[tuple[float, int]] = deque()
_GROQ_VISION_LOCK = threading.Lock()


def _reserve_groq_vision_tokens(prompt: str) -> None:
    # Qwen 3.8 charges 2,048 input tokens per image. Reserve the maximum
    # completion cap plus a conservative prompt estimate under the published
    # 8K TPM Developer-plan baseline. Free-tier account limits may be lower.
    output_cap = 1024
    estimated = 2048 + len(prompt) // 3 + 128 + output_cap
    now = time.monotonic()
    with _GROQ_VISION_LOCK:
        while _GROQ_VISION_RESERVATIONS and (
            now - _GROQ_VISION_RESERVATIONS[0][0] >= _GROQ_VISION_WINDOW_S
        ):
            _GROQ_VISION_RESERVATIONS.popleft()
        used = sum(tokens for _, tokens in _GROQ_VISION_RESERVATIONS)
        if estimated + used > _GROQ_VISION_TPM_BUDGET:
            retry_after = max(
                1.0,
                _GROQ_VISION_WINDOW_S
                - (now - _GROQ_VISION_RESERVATIONS[0][0]),
            )
            logger.info(
                "ai image_read provider=groq outcome=rate_limited reason=local_tpm_budget "
                "estimated_tokens=%d reserved_tokens=%d retry_after_s=%.1f",
                estimated,
                used,
                retry_after,
            )
            raise AiRateLimited(
                "The image-reading token budget is temporarily full. Try again shortly.",
                code="vision_rate_limited",
                retry_after=retry_after,
            )
        _GROQ_VISION_RESERVATIONS.append((now, estimated))


class ImageReader(Protocol):
    provider: str

    def close(self) -> None: ...

    def read_table_image(self, image: bytes, mime_type: str) -> dict[str, Any]: ...

    def interpret_outline_image(self, image: bytes, mime_type: str) -> list[str]: ...

    def read_page_image(self, image: bytes, mime_type: str) -> str: ...

    def read_note_image(self, image: bytes, mime_type: str) -> str: ...


class GroqVisionProvider:
    """Groq chat-completions vision adapter using an env-configured model."""

    provider = "groq"

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        if not settings.groq_api_key:
            raise AiNotConfigured(
                "Groq image reading is not configured on the server.",
                code="vision_not_configured",
                status_code=503,
            )
        if not settings.groq_vision_model:
            raise AiNotConfigured(
                "Set GROQ_VISION_MODEL to enable Groq image reading.",
                code="vision_model_not_configured",
                status_code=503,
            )
        self.settings = settings
        self.client = http_client or httpx.Client()
        self.owns_client = http_client is None

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def read_table_image(self, image: bytes, mime_type: str) -> dict[str, Any]:
        prompt = (
            "Read this image of a table. Return one JSON object with keys title, "
            "columns, rows, and unreadable_cells. Put a heading above the table "
            "in title. Preserve visible rows, columns, order, leading zeros and "
            "plus signs. Do not summarize or reorder entries. Use an empty string "
            "for unreadable cells and list their zero-based [row, column] positions."
        )
        result = self._request(image, mime_type, prompt, json_mode=True)
        try:
            value = json.loads(result)
            if not isinstance(value, dict):
                raise ValueError("not an object")
            return value
        except (TypeError, ValueError) as exc:
            raise AiInvalidResponse(
                "The vision provider returned an unreadable table response.",
                code="vision_invalid_response",
            ) from exc

    def interpret_outline_image(self, image: bytes, mime_type: str) -> list[str]:
        prompt = (
            "Read only the lecturer-stated topic headings in this outline image. "
            "Return JSON with one key, topics, containing the headings in visual "
            "order. Do not add, explain, or infer topics."
        )
        result = self._request(image, mime_type, prompt, json_mode=True)
        try:
            value = json.loads(result)
            topics = value["topics"]
            if not isinstance(topics, list) or any(
                not isinstance(item, str) for item in topics
            ):
                raise ValueError("invalid topics")
            return topics
        except (ValueError, KeyError, TypeError) as exc:
            raise AiInvalidResponse(
                "The vision provider could not read outline topics.",
                code="vision_invalid_response",
            ) from exc

    def read_page_image(self, image: bytes, mime_type: str) -> str:
        prompt = (
            "Transcribe the visible text on this lecture page in reading order. "
            "Preserve wording, numbers, symbols, and line breaks where practical. "
            "Do not summarize, correct, or add text. If no text is readable, return "
            "an empty string. Return JSON with one string key named text."
        )
        return self._read_text_image(image, mime_type, prompt, "the page")

    def read_note_image(self, image: bytes, mime_type: str) -> str:
        prompt = (
            "Transcribe all visible text in this photo of notes or a page in reading "
            "order. Preserve wording, numbers, symbols, and line breaks where "
            "practical. Do not summarize, correct, or add text. If no text is "
            "readable, return an empty string. Return JSON with one string key named "
            "text."
        )
        return self._read_text_image(image, mime_type, prompt, "the note image")

    def _read_text_image(
        self,
        image: bytes,
        mime_type: str,
        prompt: str,
        subject: str,
    ) -> str:
        result = self._request(image, mime_type, prompt, json_mode=True)
        try:
            value = json.loads(result)
            text = value["text"]
            if not isinstance(text, str):
                raise ValueError("invalid text")
            return text.strip()
        except (ValueError, KeyError, TypeError) as exc:
            raise AiInvalidResponse(
                f"The vision provider could not transcribe {subject}.",
                code="vision_invalid_response",
            ) from exc

    def _request(
        self,
        image: bytes,
        mime_type: str,
        prompt: str,
        *,
        json_mode: bool,
    ) -> str:
        if not image or mime_type not in {"image/png", "image/jpeg"}:
            raise AiInvalidResponse(
                "The image format is not supported by the vision provider.",
                code="vision_image_invalid",
                status_code=415,
            )
        if len(image) > _MAX_REQUEST_IMAGE_BYTES:
            logger.warning(
                "ai image_read provider=groq model=%s outcome=image_too_large bytes=%d",
                self.settings.groq_vision_model,
                len(image),
            )
            raise AiInvalidResponse(
                "The image exceeds the safe request size for Groq vision.",
                code="vision_image_too_large",
                status_code=413,
            )

        # One image per call keeps Qwen's documented 3-image and per-image
        # token limits comfortably within the request budget.
        encoded = base64.b64encode(image).decode("ascii")
        content: list[dict[str, Any]] = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
            },
        ]
        payload: dict[str, Any] = {
            "model": self.settings.groq_vision_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            "max_completion_tokens": min(
                max(self.settings.table_vision_max_output_tokens, 1), 1024
            ),
        }
        _reserve_groq_vision_tokens(prompt)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        url = f"{self.settings.groq_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        try:
            response = self.client.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.groq_request_timeout_s,
            )
        except httpx.TimeoutException as exc:
            log_provider_call(
                "groq", self.settings.groq_vision_model, "vision", started, "timeout"
            )
            logger.warning(
                "ai image_read provider=groq model=%s outcome=timeout",
                self.settings.groq_vision_model,
            )
            raise AiTimeout(
                "Groq image reading timed out.", code="vision_timeout"
            ) from exc
        except httpx.HTTPError as exc:
            log_provider_call(
                "groq", self.settings.groq_vision_model, "vision", started, "unreachable"
            )
            logger.warning(
                "ai image_read provider=groq model=%s outcome=provider_error",
                self.settings.groq_vision_model,
            )
            raise AiProviderError(
                "Groq image reading could not reach the provider.",
                code="vision_provider_error",
                retryable=True,
            ) from exc

        status = response.status_code
        if status == 429:
            log_provider_call(
                "groq", self.settings.groq_vision_model, "vision", started, "http_429"
            )
            logger.info(
                "ai image_read provider=groq model=%s outcome=rate_limited",
                self.settings.groq_vision_model,
            )
            raise AiRateLimited(
                "Groq is rate limiting image reading. Try again later.",
                code="vision_rate_limited",
                retry_after=_parse_retry_delay(response.headers.get("retry-after")),
            )
        if status >= 400:
            try:
                body = response.json()
                error = body.get("error", {}) if isinstance(body, dict) else {}
                message = str(error.get("message", "")) if isinstance(error, dict) else ""
                error_type = str(error.get("type", "")) if isinstance(error, dict) else ""
            except ValueError:
                message, error_type = "", ""
            lowered = f"{message} {error_type}".lower()
            if status in {413, 422} or any(
                marker in lowered
                for marker in (
                    "image too large",
                    "payload too large",
                    "request too large",
                    "maximum image size",
                    "max image size",
                    "exceeds size",
                    "20mb",
                )
            ):
                code, outcome, user_message, error_class = (
                    "vision_image_too_large",
                    "image_too_large",
                    "The image exceeds Groq's request size limit.",
                    AiInvalidResponse,
                )
                status_code = 413
            elif status in {400, 404} and any(
                marker in lowered
                for marker in (
                    "does not support image",
                    "doesn't support image",
                    "does not support image inputs",
                    "image inputs are not supported",
                    "does not accept image",
                    "image_url is not supported",
                    "does not support vision input",
                    "image input is not supported",
                    "vision is not supported",
                    "not a vision model",
                    "model does not support vision",
                )
            ):
                code, outcome, user_message, error_class = (
                    "vision_model_unsupported",
                    "model_unsupported",
                    "The configured Groq model does not accept images.",
                    AiProviderError,
                )
                status_code = 503
            else:
                code, outcome, user_message, error_class = (
                    "vision_provider_error",
                    "provider_error",
                    "Groq could not read this image.",
                    AiProviderError,
                )
                status_code = 502
            log_provider_call(
                "groq", self.settings.groq_vision_model, "vision", started, f"http_{status}"
            )
            logger.warning(
                "ai image_read provider=groq model=%s outcome=%s status=%d",
                self.settings.groq_vision_model,
                outcome,
                status,
            )
            raise error_class(
                user_message, code=code, status_code=status_code, retryable=False
            )

        log_provider_call(
            "groq", self.settings.groq_vision_model, "vision", started, "ok"
        )
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "".join(
                    str(item.get("text", ""))
                    for item in text
                    if isinstance(item, dict)
                )
            if not isinstance(text, str) or not text.strip():
                raise ValueError("empty response")
            return text.strip()
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "ai image_read provider=groq model=%s outcome=invalid_response",
                self.settings.groq_vision_model,
            )
            raise AiInvalidResponse(
                "Groq returned an unreadable image response.",
                code="vision_invalid_response",
            ) from exc


class GeminiVisionProvider:
    """Adapter retaining the Gemini image-reading implementation."""

    provider = "gemini"

    def __init__(self, settings: Settings) -> None:
        self.client = GeminiClient(settings)

    def close(self) -> None:
        self.client.close()

    def read_table_image(self, image: bytes, mime_type: str) -> dict[str, Any]:
        return self.client.read_table_image(image, mime_type)

    def interpret_outline_image(self, image: bytes, mime_type: str) -> list[str]:
        return self.client.interpret_outline_image(image, mime_type)

    def read_page_image(self, image: bytes, mime_type: str) -> str:
        return self.client.transcribe_page_image(image, mime_type)

    def read_note_image(self, image: bytes, mime_type: str) -> str:
        return self.client.transcribe_page_image(image, mime_type)


def get_image_reader(settings: Settings) -> ImageReader:
    provider = settings.image_reading_provider.strip().lower()
    if provider == "groq":
        return GroqVisionProvider(settings)
    if provider == "gemini":
        return GeminiVisionProvider(settings)
    raise AiNotConfigured(
        "The configured image-reading provider is not supported.",
        code="vision_not_configured",
        status_code=503,
    )
