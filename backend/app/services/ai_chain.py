"""Provider fallback chain and summary cache wrapper.

``build_ai_client`` returns an object with the same ``summarize`` method as the
existing ``AiClient`` protocol, so it can be passed anywhere an ``AiClient`` is
accepted (for example ``build_study_output(..., ai_client=...)``).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Callable

from app.config import Settings
from app.services import ai_client as base
from app.services.ai_client import (
    AiClient,
    AiError,
    AiInvalidResponse,
    AiNotConfigured,
    AiProviderError,
    AiRateLimited,
    AiTimeout,
)
from app.services.groq_client import GroqClient
from app.services.provider_settings import ProviderSettings, load_provider_settings
from app.services.summary_cache import CachedAiClient as SummaryCachedAiClient

logger = logging.getLogger(__name__)

LIMIT_REACHED_MESSAGE = "Summary limit reached. Original text is still available."


def _prompt_version() -> str:
    """Hash of the grounded prompts: editing a prompt invalidates cached summaries."""
    parts = (
        base.GROUNDED_SYSTEM_PROMPT,
        base.SECTION_PROMPT,
        base.OVERVIEW_PROMPT,
        base.TOPIC_PROMPT,
        base.BEYOND_SOURCE_PROMPT,
        base.BEYOND_SOURCE_SYSTEM_PROMPT,
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


PROMPT_VERSION = _prompt_version()


def is_quota_exhausted(exc: AiError) -> bool:
    """True when a provider says its quota is used up, so waiting will not help."""
    flag = getattr(exc, "quota_exhausted", None)
    if flag is not None:
        return bool(flag)
    message = exc.message.lower()
    return (
        exc.code == "ai_quota_exhausted"
        or "quota" in message
        or "daily limit" in message
    )


class AiAllProvidersFailed(AiError):
    """Every configured provider failed. Verbatim output is still available."""

    code = "ai_limit_reached"
    status_code = 503

    def __init__(self, errors: list[tuple[str, AiError]]) -> None:
        super().__init__(LIMIT_REACHED_MESSAGE)
        self.errors = errors

    @property
    def quota_only(self) -> bool:
        """Every provider is out of quota; retrying now cannot succeed."""
        return bool(self.errors) and all(
            isinstance(error, AiRateLimited) and is_quota_exhausted(error)
            for _, error in self.errors
        )

    def wait_hint_s(self, default: float) -> float | None:
        """Seconds worth waiting before retrying, or None when waiting will not help.

        Only short rate limits qualify. Timeouts and provider errors were already
        retried inside the client, and quota exhaustion lasts until tomorrow.
        """
        waits = [
            error.retry_after if error.retry_after is not None else default
            for _, error in self.errors
            if isinstance(error, AiRateLimited) and not is_quota_exhausted(error)
        ]
        return max(waits) if waits else None


class FallbackAiClient:
    """Try providers in order; move on when one is limited, down or unusable."""

    def __init__(self, providers: list[tuple[str, AiClient]]) -> None:
        if not providers:
            raise AiNotConfigured(
                "AI summarization is not configured on the server.",
                code="ai_not_configured",
                status_code=503,
            )
        self._providers = providers
        self.last_provider: str | None = None

    def summarize(
        self, *, source_text: str, source_label: str, purpose: str = "section"
    ) -> str:
        errors: list[tuple[str, AiError]] = []
        for name, client in self._providers:
            try:
                result = client.summarize(
                    source_text=source_text,
                    source_label=source_label,
                    purpose=purpose,
                )
            except (AiRateLimited, AiTimeout, AiProviderError, AiInvalidResponse) as exc:
                logger.info("ai provider failed provider=%s code=%s", name, exc.code)
                errors.append((name, exc))
                continue
            self.last_provider = name
            return result
        raise AiAllProvidersFailed(errors)


def _gemini_factory(settings: Settings) -> AiClient:
    return base.GeminiClient(settings)


def build_ai_client(
    settings: Settings,
    provider: ProviderSettings | None = None,
    *,
    owner_id: str | None = None,
    groq_factory: Callable[[ProviderSettings], AiClient] = GroqClient,
    gemini_factory: Callable[[Settings], AiClient] = _gemini_factory,
) -> AiClient:
    """Build the configured provider chain, optionally wrapped in the cache.

    Providers without credentials are skipped. When none is configured this
    raises ``AiNotConfigured``, exactly like the existing ``get_ai_client``.
    The cache is owner-scoped, so callers that have a user id should pass it.
    """
    explicit_provider = provider is not None
    provider = provider or load_provider_settings()
    if not explicit_provider and settings.groq_api_key is None:
        provider = replace(provider, groq_api_key=None)
    providers: list[tuple[str, AiClient]] = []
    for name in provider.provider_order:
        try:
            if name == "groq":
                providers.append((name, groq_factory(provider)))
            elif name == "gemini":
                providers.append((name, gemini_factory(settings)))
        except AiNotConfigured:
            continue
    chain = FallbackAiClient(providers)
    if not owner_id or not provider.summary_cache_enabled:
        return chain
    return SummaryCachedAiClient(
        chain,
        owner_id=owner_id,
        retention_minutes=provider.summary_cache_retention_minutes,
    )


def ai_available(settings: Settings, provider: ProviderSettings | None = None) -> bool:
    provider = provider or load_provider_settings()
    for name in provider.provider_order:
        if name == "groq" and provider.groq_configured:
            return True
        if name == "gemini" and settings.ai_configured:
            return True
    return False
