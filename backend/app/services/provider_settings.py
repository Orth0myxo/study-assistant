"""Settings for the optional Groq provider, summary cache and chat command step.

These are read straight from the process environment (and, as a fallback, from
a local ``.env`` file) so that the existing ``Settings`` class does not have to
change. Real environment variables always win over ``.env`` values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DEFAULT_PROVIDER_ORDER = ("groq", "gemini")
KNOWN_PROVIDERS = frozenset({"groq", "gemini"})


def read_dotenv(path: str | os.PathLike[str] = ".env") -> dict[str, str]:
    """Parse a minimal ``KEY=VALUE`` file. Missing or unreadable files give ``{}``."""
    values: dict[str, str] = {}
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _as_int(value: str | None, default: int, *, minimum: int = 0) -> int:
    if value is None or not value.strip():
        return default
    try:
        parsed = int(value.strip())
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


def _as_float(value: str | None, default: float, *, minimum: float = 0.0) -> float:
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value.strip())
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_order(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return DEFAULT_PROVIDER_ORDER
    order: list[str] = []
    for part in value.split(","):
        name = part.strip().lower()
        if name in KNOWN_PROVIDERS and name not in order:
            order.append(name)
    return tuple(order) or DEFAULT_PROVIDER_ORDER


@dataclass(frozen=True)
class ProviderSettings:
    provider_order: tuple[str, ...] = DEFAULT_PROVIDER_ORDER

    groq_api_key: str | None = None
    # Summaries. Verify current model ids and limits in the Groq console.
    groq_model: str = "llama-3.3-70b-versatile"
    # Request -> command step (JSON mode, temperature 0). A non-reasoning model
    # is the safe default. To save daily token budget, set GROQ_COMMAND_MODEL to
    # a smaller model after confirming it is available in your Groq console.
    groq_command_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_request_timeout_s: float = 60.0
    groq_max_output_tokens: int = 1024
    groq_max_retries: int = 2
    groq_retry_backoff_s: float = 1.0
    # Longest Retry-After we are willing to sleep through. Anything longer is
    # reported as retryable=False so the fallback chain moves on immediately.
    groq_max_wait_s: float = 8.0

    summary_cache_enabled: bool = True
    summary_cache_retention_minutes: int = 10080
    # Characters of source text per summary request (free-tier friendly).
    summary_chunk_chars: int = 4000
    # Hard cap on summary requests per extraction call.
    summary_max_chunks: int = 12

    command_max_message_chars: int = 500

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key and self.groq_model)


def load_provider_settings(
    environ: Mapping[str, str] | None = None,
    dotenv_path: str | os.PathLike[str] | None = ".env",
) -> ProviderSettings:
    merged: dict[str, str] = {}
    if dotenv_path is not None:
        merged.update(read_dotenv(dotenv_path))
    merged.update(os.environ if environ is None else environ)

    def get(name: str) -> str | None:
        value = merged.get(name)
        return value if value is not None and value.strip() else None

    defaults = ProviderSettings()
    key = get("GROQ_API_KEY")
    return ProviderSettings(
        provider_order=_as_order(get("AI_PROVIDER_ORDER")),
        groq_api_key=key.strip() if key else None,
        groq_model=(get("GROQ_MODEL") or defaults.groq_model).strip(),
        groq_command_model=(
            get("GROQ_COMMAND_MODEL") or defaults.groq_command_model
        ).strip(),
        groq_base_url=(get("GROQ_BASE_URL") or defaults.groq_base_url).strip(),
        groq_request_timeout_s=_as_float(
            get("GROQ_REQUEST_TIMEOUT_S"), defaults.groq_request_timeout_s, minimum=1
        ),
        groq_max_output_tokens=_as_int(
            get("GROQ_MAX_OUTPUT_TOKENS"), defaults.groq_max_output_tokens, minimum=16
        ),
        groq_max_retries=_as_int(
            get("GROQ_MAX_RETRIES"), defaults.groq_max_retries, minimum=0
        ),
        groq_retry_backoff_s=_as_float(
            get("GROQ_RETRY_BACKOFF_S"), defaults.groq_retry_backoff_s, minimum=0
        ),
        groq_max_wait_s=_as_float(
            get("GROQ_MAX_WAIT_S"), defaults.groq_max_wait_s, minimum=0
        ),
        summary_cache_enabled=_as_bool(
            get("SUMMARY_CACHE_ENABLED"), defaults.summary_cache_enabled
        ),
        summary_cache_retention_minutes=_as_int(
            get("SUMMARY_CACHE_RETENTION_MINUTES") or get("RETENTION_MINUTES"),
            defaults.summary_cache_retention_minutes,
            minimum=1,
        ),
        summary_chunk_chars=_as_int(
            get("SUMMARY_CHUNK_CHARS"), defaults.summary_chunk_chars, minimum=500
        ),
        summary_max_chunks=_as_int(
            get("SUMMARY_MAX_CHUNKS"), defaults.summary_max_chunks, minimum=1
        ),
        command_max_message_chars=_as_int(
            get("COMMAND_MAX_MESSAGE_CHARS"),
            defaults.command_max_message_chars,
            minimum=50,
        ),
    )
