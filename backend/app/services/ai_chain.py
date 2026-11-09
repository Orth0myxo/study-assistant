"""Provider fallback chain and summary cache wrapper.

``build_ai_client`` returns an object with the same ``summarize`` method as the
existing ``AiClient`` protocol, so it can be passed anywhere an ``AiClient`` is
accepted (for example ``build_study_output(..., ai_client=...)``).
"""

from __future__ import annotations

import hashlib
import logging
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
from app.services.summary_cache import SummaryCache, make_cache_key

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


class AiAllProvidersFailed(AiError):
    """Every configured provider failed. Verbatim output is still available."""

    code = "ai_limit_reached"
    status_code = 503

    def __init__(self, errors: list[tuple[str, AiError]]) -> None:
        super().__init__(LIMIT_REACHED_MESSAGE)
        self.errors = errors


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


class CachedAiClient:
    """Serve repeated requests from the summary cache without any provider call."""

    def __init__(
        self,
        inner: AiClient,
        cache: SummaryCache,
        *,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        self._inner = inner
        self._cache = cache
        self._prompt_version = prompt_version
        self.hits = 0
        self.misses = 0
        self.last_from_cache = False

    def summarize(
        self, *, source_text: str, source_label: str, purpose: str = "section"
    ) -> str:
        key = make_cache_key(
            purpose=purpose,
            source_label=source_label,
            source_text=source_text,
            prompt_version=self._prompt_version,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self.hits += 1
            self.last_from_cache = True
            return cached
        self.misses += 1
        self.last_from_cache = False
        result = self._inner.summarize(
            source_text=source_text, source_label=source_label, purpose=purpose
        )
        self._cache.put(key, result)
        return result


def _gemini_factory(settings: Settings) -> AiClient:
    return base.GeminiClient(settings)


def build_ai_client(
    settings: Settings,
    provider: ProviderSettings | None = None,
    *,
    cache: SummaryCache | None = None,
    groq_factory: Callable[[ProviderSettings], AiClient] = GroqClient,
    gemini_factory: Callable[[Settings], AiClient] = _gemini_factory,
) -> AiClient:
    """Build the configured provider chain, wrapped in the summary cache.

    Providers without credentials are skipped. When none is configured this
    raises ``AiNotConfigured``, exactly like the existing ``get_ai_client``.
    """
    provider = provider or load_provider_settings()
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
    if not provider.summary_cache_enabled:
        return chain
    cache = cache or SummaryCache(
        retention_minutes=provider.summary_cache_retention_minutes
    )
    return CachedAiClient(chain, cache)


def ai_available(settings: Settings, provider: ProviderSettings | None = None) -> bool:
    provider = provider or load_provider_settings()
    for name in provider.provider_order:
        if name == "groq" and provider.groq_configured:
            return True
        if name == "gemini" and settings.ai_configured:
            return True
    return False
