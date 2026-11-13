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

SOURCE_REMAINS_MESSAGE = "Original source text is still available. Try again later."


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


class AiAllProvidersFailed(AiError):
    """Every configured provider failed. Verbatim output is still available."""

    code = "ai_provider_error"
    status_code = 502

    def __init__(self, errors: list[tuple[str, AiError]]) -> None:
        self.errors = errors
        codes = [error.code for _, error in errors]
        if codes and all(code == "ai_quota_exhausted" for code in codes):
            code = "ai_quota_exhausted"
            message = "The AI provider quota is exhausted. Original source text is still available. Try again after the quota resets."
            status_code = 429
        elif codes and all(code in {"ai_quota_exhausted", "ai_rate_limited"} for code in codes):
            code = "ai_rate_limited"
            message = "AI providers are rate limiting requests. Original source text is still available. Please try again shortly."
            status_code = 429
        elif codes and len(set(codes)) == 1:
            first = errors[0][1]
            code, message, status_code = first.code, first.message, first.status_code
        else:
            code = "ai_provider_error"
            message = f"All configured AI providers failed. {SOURCE_REMAINS_MESSAGE}"
            status_code = 502
        super().__init__(message, code=code, status_code=status_code)


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
