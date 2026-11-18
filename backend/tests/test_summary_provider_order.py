from __future__ import annotations

from app.config import get_settings
from app.services.ai_chain import build_ai_client
from app.services.provider_settings import ProviderSettings, load_provider_settings


def test_summary_provider_order_defaults_to_gemini_then_groq():
    provider = load_provider_settings({}, dotenv_path=None)

    assert provider.summary_provider_order == ("gemini", "groq")


def test_summary_provider_order_can_be_overridden():
    provider = load_provider_settings(
        {"SUMMARY_PROVIDER_ORDER": "groq,gemini"}, dotenv_path=None
    )

    assert provider.summary_provider_order == ("groq", "gemini")


def test_build_ai_client_uses_the_summary_order(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-test")
    calls: list[str] = []

    class FakeClient:
        def summarize(self, **_kwargs):
            return "summary"

    def groq_factory(_provider):
        calls.append("groq")
        return FakeClient()

    def gemini_factory(_settings):
        calls.append("gemini")
        return FakeClient()

    build_ai_client(
        settings,
        ProviderSettings(
            summary_provider_order=("gemini", "groq"),
            groq_api_key="groq-key",
            summary_cache_enabled=False,
        ),
        groq_factory=groq_factory,
        gemini_factory=gemini_factory,
    )

    assert calls == ["gemini", "groq"]
