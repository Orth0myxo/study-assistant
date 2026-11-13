"""Whole-material summary jobs wait out short rate limits and stop on quota."""

from __future__ import annotations

from types import SimpleNamespace

from app.config import get_settings
from app.services.ai_chain import AiAllProvidersFailed
from app.services.ai_client import AiRateLimited
from app.services.provider_settings import ProviderSettings
from app.services.study_processor import process_document


class ScriptedClient:
    """Returns scripted outcomes in order, then a default summary."""

    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls = 0

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls += 1
        if self.script:
            outcome = self.script.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return f"Summary of {source_label}."


def _record(pages: int):
    sections = [
        {
            "index": number,
            "raw_text": f"Page {number} " + "x" * 40,
            "anchor": {"kind": "pdf_page", "page": number},
        }
        for number in range(1, pages + 1)
    ]
    return SimpleNamespace(sections=sections, owner_id=None)


def _settings():
    # Tiny chunk size so every page becomes its own part.
    return get_settings().model_copy(
        update={"max_summary_chunk_chars": 50, "retention_minutes": 60}
    )


def _provider(**overrides):
    values = {
        "summary_unit_delay_s": 0.0,
        "summary_rate_limit_retries": 2,
        "summary_rate_limit_wait_cap_s": 45.0,
        "summary_rate_limit_default_wait_s": 20.0,
        "summary_wait_budget_s": 150.0,
    }
    values.update(overrides)
    return ProviderSettings(**values)


def _rate_limited(retry_after=None):
    return AiAllProvidersFailed(
        [("groq", AiRateLimited("rate limiting", retry_after=retry_after))]
    )


def _out_of_quota():
    error = AiRateLimited("The AI provider's daily limit has been reached.", retryable=False)
    error.quota_exhausted = True
    return AiAllProvidersFailed([("groq", error)])


def _run(client, *, pages=1, provider=None):
    waits: list[float] = []
    outcome = process_document(
        _record(pages),
        "summaries_only",
        _settings(),
        client,
        provider=provider or _provider(),
        sleep=waits.append,
    )
    return outcome, waits


def test_short_rate_limit_is_waited_out_and_retried():
    client = ScriptedClient([_rate_limited(retry_after=25.0)])
    outcome, waits = _run(client)
    assert outcome.status == "completed"
    assert outcome.items[0]["summary"]
    assert waits == [25.0]
    assert client.calls == 2


def test_wait_is_capped_and_uses_default_when_provider_gave_none():
    client = ScriptedClient([_rate_limited(retry_after=999.0)])
    _, waits = _run(client)
    assert waits == [45.0]

    client = ScriptedClient([_rate_limited(retry_after=None)])
    _, waits = _run(client)
    assert waits == [20.0]


def test_waiting_stops_when_the_job_budget_is_used():
    client = ScriptedClient([_rate_limited(25.0), _rate_limited(25.0), _rate_limited(25.0)])
    outcome, waits = _run(client, provider=_provider(summary_wait_budget_s=30.0))
    assert waits == [25.0]  # a second 25s wait would exceed the 30s budget
    assert outcome.status == "failed"
    assert outcome.items[0]["summary"] is None


def test_retries_are_limited():
    client = ScriptedClient([_rate_limited(5.0)] * 5)
    outcome, waits = _run(client, provider=_provider(summary_rate_limit_retries=1))
    assert waits == [5.0]
    assert client.calls == 2
    assert outcome.status == "failed"


def test_out_of_quota_stops_calling_the_providers():
    client = ScriptedClient([_out_of_quota()])
    outcome, waits = _run(client, pages=4)
    assert client.calls == 1  # parts 2-4 never reach the provider
    assert waits == []
    assert len(outcome.items) == 4
    assert all(item["summary"] is None for item in outcome.items)
    assert all(item["warnings"] for item in outcome.items)
    assert outcome.status == "failed"


def test_quota_stop_keeps_earlier_summaries():
    client = ScriptedClient(["First part summary.", _out_of_quota()])
    outcome, _ = _run(client, pages=3)
    assert outcome.items[0]["summary"] == "First part summary."
    assert outcome.items[1]["summary"] is None
    assert outcome.items[2]["summary"] is None
    assert outcome.status == "partial"
    assert client.calls == 2


def test_fresh_summaries_are_paced_but_not_after_the_last_part():
    client = ScriptedClient()
    outcome, waits = _run(client, pages=3, provider=_provider(summary_unit_delay_s=1.5))
    assert outcome.status == "completed"
    assert waits == [1.5, 1.5]


def test_rate_limit_mixed_with_exhausted_quota_waits_for_the_other_provider():
    exhausted = AiRateLimited("daily limit", retryable=False)
    exhausted.quota_exhausted = True
    mixed = AiAllProvidersFailed(
        [("groq", exhausted), ("gemini", AiRateLimited("slow", retry_after=12.0))]
    )
    client = ScriptedClient([mixed])
    outcome, waits = _run(client)
    assert waits == [12.0]
    assert outcome.status == "completed"
