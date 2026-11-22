from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Study Assistant API"
    version: str = "0.1.0"
    log_level: str = "info"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.8-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_request_timeout_s: int = 60
    gemini_max_output_tokens: int = 2048
    gemini_max_retries: int = 2
    gemini_retry_backoff_s: float = 1.0
    ai_provider_order: str = "groq,gemini"
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.8-27b"
    groq_vision_model: str = ""
    groq_command_model: str = "qwen/qwen3.8-27b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_request_timeout_s: int = 60
    # The chat command step only classifies a short message and the app waits
    # 15s for it, so give up early and let the local fallback answer instead.
    groq_command_timeout_s: float = 8.0
    # Requests slower than this are logged (path and status only).
    slow_request_log_ms: int = 2000
    groq_max_output_tokens: int = 2048
    max_summary_chunk_chars: int = 4000
    # Ordinary lecture documents fit in one summary call. A prompt this large
    # can exceed Groq's free-tier limit; the provider chain then falls back to
    # Gemini. Above it, the per-part path is kept.
    summary_single_call_chars: int = 40000
    # Hard backstops applied after the model answers. The prompts ask for shorter
    # text; these guarantee a summary never runs long when a model ignores that.
    summary_max_words: int = 150
    overview_max_words: int = 100
    beyond_max_words: int = 100
    max_section_chars: int = 6000

    # The tool-calling chat is opt-in until its scripted evaluation is green.
    # The agent endpoint is generic OpenAI-compatible configuration; Groq/Gemini
    # remain summary/command providers unless explicitly added to its fallback order.
    agent_enabled: bool = False
    agent_provider_order: str = "agent"
    agent_api_key: str | None = None
    agent_model: str = ""
    agent_base_url: str = ""
    agent_reasoning_effort: Literal[
        "default", "low", "medium", "high", "none"
    ] | None = "low"
    agent_max_output_tokens: int = 500
    agent_tool_max_output_tokens: int = 800
    agent_output_tokens_per_minute: int | None = None

    agent_max_model_calls: int = 4
    agent_max_tool_calls: int = 6
    agent_token_budget: int = 7000
    agent_request_timeout_s: float = 45.0
    # A provider 429 may be waited out and the same call retried once when the
    # honest wait (retry-after or the token-reset header, whichever is larger)
    # fits both this limit and the remaining request budget. Completed tools are
    # never re-run. Set to 0 to disable automatic waiting.
    agent_rate_limit_wait_s: float = 20.0
    agent_tool_result_chars: int = 4000
    agent_pending_ttl_s: int = 120
    # Recent user/assistant turns kept for follow-ups, bounded by a token budget.
    agent_memory_turns: int = 4
    agent_memory_tokens: int = 600
    # Whole-document summaries sample evenly spaced sections, not every section.
    agent_document_sample_chars: int = 24000
    # Bounded page range accepted by the agent's page tool.
    agent_max_page_range: int = 50

    max_upload_mb: int = 25
    max_pages: int = 50
    max_docx_words: int = 30000
    retention_minutes: int = 10080
    database_path: str = ".tmp/study.db"
    job_timeout_s: int = 300
    rate_limit_per_minute: int = 30
    sync_processing_max_sections: int = 20
    background_ingest: bool = True
    ingest_workers: int = 2

    temp_dir: str = ".tmp/uploads"
    min_pdf_chars_per_page: int = 15
    max_docx_uncompressed_mb: int = 100
    cleanup_interval_s: int = 60

    ocr_tesseract_path: str = "tesseract"
    ocr_page_limit: int = 30
    ocr_dpi: int = 200
    ocr_timeout_s: int = 30
    ocr_language: str = "eng"
    ocr_request_page_limit: int = 5
    ocr_request_time_budget_s: float = 20.0

    # Topic-note images are bounded independently from document OCR. The total
    # upload limit still uses max_upload_mb.
    topic_max_images: int = 10
    topic_max_image_mb: int = 10
    topic_max_image_pixels: int = 40_000_000
    topic_max_topics: int = 20
    topic_max_topic_chars: int = 200
    topic_max_transcript_chars: int = 50_000
    topic_max_jottings_chars: int = 50_000
    topic_max_sources_per_user: int = 20
    topic_ocr_max_concurrency: int = 4
    min_match_score: float = 0.62
    max_matches_per_topic: int = 10

    # Photo-to-table results are temporary and owner-scoped. The default local
    # reader uses Tesseract word boxes; AI reading is an explicit per-request
    # choice and is disclosed in the result.
    image_reading_provider: str = "groq"
    table_vision_max_output_tokens: int = 4096
    table_max_rows: int = 200
    table_max_columns: int = 30
    table_max_cell_chars: int = 500

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:19006,http://localhost:8081"

    # Optional shared-secret gate for temporary public hosting. When set,
    # unauthenticated API requests and non-API routes must present it as the
    # `X-App-Token` header or a `?token=` query parameter (the query form is
    # needed for native file downloads, which cannot set headers). Registration
    # and /health are exempt, and a valid per-user bearer token is sufficient
    # for API data requests. Unset keeps local dev open.
    app_access_token: str | None = None

    @field_validator(
        "agent_reasoning_effort", "agent_output_tokens_per_minute", mode="before"
    )
    @classmethod
    def _blank_optional_agent_value(cls, value):
        return None if value == "" else value

    @property
    def ai_configured(self) -> bool:
        configured = {
            "groq": bool(self.groq_api_key and self.groq_model),
            "gemini": bool(self.gemini_api_key and self.gemini_model),
        }
        return any(configured.get(name.strip().lower(), False) for name in self.ai_provider_order.split(","))

    @property
    def configured_ai_providers(self) -> list[str]:
        configured = {
            "groq": bool(self.groq_api_key and self.groq_model),
            "gemini": bool(self.gemini_api_key and self.gemini_model),
        }
        return [
            name.strip().lower()
            for name in self.ai_provider_order.split(",")
            if configured.get(name.strip().lower(), False)
        ]

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
