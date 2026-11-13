from __future__ import annotations

from app.services.ai_chain import AiAllProvidersFailed
from app.services.ai_client import AiRateLimited
from app.services.extraction_service import build_result, select, to_topic_payload
from app.services.provider_settings import ProviderSettings

SECTIONS = [
    {"index": i, "anchor": {"kind": "pdf_page", "page": i + 1},
     "raw_text": f"Original text of page {i + 1}.", "normalized_text": ""}
    for i in range(5)
]
PROVIDER = ProviderSettings()


class Counting:
    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls += 1
        if self.fail:
            error = AiRateLimited(
                "Daily quota exhausted.", code="ai_quota_exhausted", retryable=False
            )
            raise AiAllProvidersFailed([("groq", error)])
        return "Summary."


def _sel(spec="2-3"):
    return select(document_id="d", sections=SECTIONS, page_count=5, selector_type="pages", spec=spec)


def test_source_passages_never_calls_ai():
    client = Counting()
    result = build_result(selection=_sel(), mode="source_passages", client=client, provider=PROVIDER)
    assert client.calls == 0
    assert [p["text"] for p in result["passages"]] == [SECTIONS[1]["raw_text"], SECTIONS[2]["raw_text"]]


def test_both_keeps_text_and_adds_summary():
    result = build_result(selection=_sel(), mode="both", client=Counting(), provider=PROVIDER)
    assert result["passages"] and result["summaries"]


def test_summary_only_hides_passages_when_summary_exists():
    result = build_result(selection=_sel(), mode="ai_study_material", client=Counting(), provider=PROVIDER)
    assert result["passages"] == [] and result["summaries"]


def test_quota_failure_keeps_original_text_and_warns():
    client = Counting(fail=True)
    result = build_result(selection=_sel(), mode="ai_study_material", client=client, provider=PROVIDER)
    assert client.calls == 1  # stops at first failure
    assert result["passages"]
    assert result["warnings"][-1]["code"] == "ai_quota_exhausted"


def test_incomplete_summary_keeps_unsummarised_source_text():
    class FailSecond:
        def __init__(self):
            self.calls = 0

        def summarize(self, *, source_text, source_label, purpose="section"):
            self.calls += 1
            if self.calls == 2:
                error = AiRateLimited(
                    "Daily quota exhausted.", code="ai_quota_exhausted", retryable=False
                )
                raise AiAllProvidersFailed([("groq", error)])
            return "Summary."

    long_sections = [
        {
            "index": index,
            "anchor": {"kind": "pdf_page", "page": index + 1},
            "raw_text": f"Page {index + 1}. " + ("source wording " * 80),
        }
        for index in range(2)
    ]
    selection = select(
        document_id="d",
        sections=long_sections,
        page_count=2,
        selector_type="pages",
        spec="1-2",
    )
    result = build_result(
        selection=selection,
        mode="ai_study_material",
        client=FailSecond(),
        provider=ProviderSettings(summary_chunk_chars=500),
    )

    assert result["summaries"]
    assert result["passages"]
    assert result["warnings"][-1]["code"] == "ai_quota_exhausted"


def test_chunked_passage_is_not_repeated_in_export_payload():
    result = {
        "passages": [
            {
                "passage_id": "d:0",
                "text": "One long passage.",
                "anchor": {"kind": "pdf_page", "page": 1},
            }
        ],
        "summaries": [
            {"label": "Part 1", "text": "First.", "passage_ids": ["d:0"]},
            {"label": "Part 2", "text": "Second.", "passage_ids": ["d:0"]},
        ],
        "warnings": [],
    }

    payload = to_topic_payload(
        filename="a.pdf", mode="both", label="All", result=result
    )
    entries = [
        entry
        for topic in payload["topics"]
        for entry in topic["source_passages"]
    ]
    assert [entry["passage"] for entry in entries] == ["One long passage."]


def test_whole_and_payload_shape():
    sel = select(document_id="d", sections=SECTIONS, page_count=5, selector_type="whole", spec=None)
    result = build_result(selection=sel, mode="source_passages", client=None, provider=PROVIDER)
    payload = to_topic_payload(filename="a.pdf", mode="source_passages", label="All", result=result)
    assert payload["kind"] == "topic_study" and len(payload["topics"][0]["source_passages"]) == 5
