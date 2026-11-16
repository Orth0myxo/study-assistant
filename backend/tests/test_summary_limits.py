"""Summaries are held to a word budget even when a model ignores the prompt."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import ai_client
from app.services.ai_client import limit_words
from app.services.provider_settings import ProviderSettings
from app.services.study_processor import process_document


def _words(count: int, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def test_text_within_the_limit_is_returned_unchanged():
    text = "- one point\n- another point"
    assert limit_words(text, 50) == text


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_disables_the_cap(limit):
    text = _words(500)
    assert limit_words(text, limit) == text


def test_whole_bullets_are_kept_and_the_rest_dropped():
    bullets = [f"- {_words(10, f'b{n}_')}" for n in range(1, 8)]
    result = limit_words("\n".join(bullets), 35)
    lines = result.split("\n")
    # Each bullet is 11 "words" (dash included); three fit in 35.
    assert lines == bullets[:3]
    assert len(result.split()) <= 35


def test_a_long_paragraph_is_cut_at_a_sentence_end():
    sentences = [f"{_words(9, f's{n}_')}." for n in range(1, 11)]
    paragraph = " ".join(sentences)
    result = limit_words(paragraph, 35)
    assert result.endswith(".")
    assert not result.endswith("…")
    assert len(result.split()) <= 35
    # Only whole sentences survive.
    assert result.count(".") == len(result.split()) // 9


def test_a_single_run_on_line_is_cut_at_the_limit_with_an_ellipsis():
    result = limit_words(_words(300), 40)
    assert result.endswith("…")
    assert len(result.replace("…", "").split()) == 40


def test_a_short_remaining_budget_does_not_add_a_stub_line():
    first = _words(28)
    second = _words(30, "x")
    result = limit_words(f"{first}\n{second}", 30)
    # Only 2 words of budget are left, too few to be a useful partial line.
    assert result == first


def test_no_trailing_blank_lines_remain():
    text = f"{_words(20)}\n\n{_words(20, 'y')}"
    result = limit_words(text, 20)
    assert result == _words(20)


def test_prompts_ask_for_a_word_limit():
    assert "at most 120 words" in ai_client.SECTION_PROMPT
    assert "at most 150 words" in ai_client.TOPIC_PROMPT
    assert "at most 90 words" in ai_client.OVERVIEW_PROMPT
    assert "at most 100 words" in ai_client.BEYOND_SOURCE_PROMPT


# --- the caps are applied where summaries are produced ---------------------------


class _WordyClient:
    """Ignores every length instruction and returns a very long answer."""

    def __init__(self, words: int):
        self.words = words

    def summarize(self, *, source_text, source_label, purpose="section"):
        return " ".join(f"{purpose}{i}." for i in range(self.words))


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


def _provider() -> ProviderSettings:
    return ProviderSettings(
        summary_unit_delay_s=0.0,
        summary_rate_limit_retries=0,
        summary_rate_limit_wait_cap_s=0.0,
        summary_rate_limit_default_wait_s=0.0,
        summary_wait_budget_s=0.0,
    )


def test_section_and_overview_summaries_respect_the_caps():
    settings = get_settings().model_copy(
        update={
            "max_summary_chunk_chars": 50,
            "retention_minutes": 60,
            "summary_max_words": 25,
            "overview_max_words": 12,
        }
    )
    outcome = process_document(
        _record(3),
        "summaries_only",
        settings,
        _WordyClient(400),
        provider=_provider(),
        sleep=lambda _seconds: None,
    )

    assert outcome.status == "completed"
    assert outcome.items
    for item in outcome.items:
        assert len(item["summary"].replace("…", "").split()) <= 25
    assert outcome.document_summary
    # With a tiny chunk size the overview is built from several parts; the joined
    # overview is capped as a whole, not only each part.
    assert len(outcome.document_summary.replace("…", "").split()) <= 12


def test_beyond_cap_matches_its_prompt():
    settings = get_settings()
    assert settings.beyond_max_words == 100


def test_default_caps_are_sensible():
    settings = get_settings()
    assert 0 < settings.overview_max_words <= settings.summary_max_words <= 300
