from functools import lru_cache

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
    max_section_chars: int = 6000

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
