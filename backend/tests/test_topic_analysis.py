from __future__ import annotations

import pytest

from app.errors import ApiError
from app.services.storage import storage
from app.services.topic_matching import match_topics
from app.services.topic_segmentation import (
    prepare_provided_topics,
    segment_transcript,
)
from tests.helpers import build_docx, build_pdf, build_png

PDF_CT = "application/pdf"
PNG_CT = "image/png"

PAGE_ONE = "Photosynthesis converts light energy into chemical energy."
PAGE_TWO = "Cellular respiration releases energy from glucose."


def _upload_document(client, data: bytes, filename: str, headers=None) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, data, PDF_CT)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _create_source(client, document_id: str | None = None, headers=None):
    data = {"document_id": document_id} if document_id else None
    response = client.post(
        "/api/v1/topic-sources",
        files={"images": ("notes.png", build_png(64), PNG_CT)},
        data=data,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_transcript(client, source_id: str, text: str, headers=None) -> None:
    response = client.patch(
        f"/api/v1/topic-sources/{source_id}",
        json={"transcripts": [{"index": 0, "text": text}]},
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _review(client, source_id: str, topics=None, headers=None):
    kwargs = {"headers": headers}
    if topics is not None:
        kwargs["json"] = {"topics": topics}
    return client.post(
        f"/api/v1/topic-sources/{source_id}/topic-review", **kwargs
    )


def _section(sections: list[dict], index: int) -> dict:
    return next(section for section in sections if section["index"] == index)


def test_segments_numbered_and_multiple_topics_with_ocr_noise():
    text = (
        "| 1. Photosynthesis |\n"
        "~~ 2.\tCellular respiration ~~\n"
        "Photo-\n"
        "synthesis\n"
        "3. Calvin cycle\n"
        "Page 2\n"
        "---\n"
    )

    result = segment_transcript(text, max_topics=20, max_chars=200)

    assert result.topics == [
        "Photosynthesis",
        "Cellular respiration",
        "Calvin cycle",
    ]
    assert result.warnings == []


def test_multiple_topics_on_one_line_and_numbered_inline_lists():
    result = segment_transcript(
        "Photosynthesis; Cellular respiration | Calvin cycle\n"
        "1. Light reactions 2. Calvin cycle",
        max_topics=20,
        max_chars=200,
    )

    assert result.topics == [
        "Photosynthesis",
        "Cellular respiration",
        "Calvin cycle",
        "Light reactions",
    ]


def test_topic_label_and_unicode_bullet_separators():
    result = segment_transcript(
        "Study topics: Photosynthesis \u2022 Cellular respiration \u2014 "
        "Calvin cycle",
        max_topics=20,
        max_chars=200,
    )

    assert result.topics == [
        "Photosynthesis",
        "Cellular respiration",
        "Calvin cycle",
    ]

    compound = segment_transcript(
        "DNA\u2013protein interactions",
        max_topics=20,
        max_chars=200,
    )
    assert compound.topics == ["DNA\u2013protein interactions"]


def test_segmentation_reports_topic_limits_and_truncation():
    limited = segment_transcript(
        "1. Photosynthesis\n2. Cellular respiration\n3. Calvin cycle",
        max_topics=2,
        max_chars=200,
    )
    assert limited.topics == ["Photosynthesis", "Cellular respiration"]
    assert [warning["code"] for warning in limited.warnings] == [
        "topic_limit_reached"
    ]

    truncated = segment_transcript(
        "Photosynthesis and cellular respiration",
        max_topics=20,
        max_chars=10,
    )
    assert truncated.topics
    assert all(len(topic) <= 10 for topic in truncated.topics)
    assert "topic_truncated" in [
        warning["code"] for warning in truncated.warnings
    ]


def test_deduplicates_only_identical_topics_and_keeps_distinct_ones():
    result = segment_transcript(
        "Photosynthesis\n"
        "Photosynthesis.\n"
        "Photosynthesis regulation\n"
        "Cell cycle\n"
        "Cell-cycle",
        max_topics=20,
        max_chars=200,
    )

    assert result.topics == [
        "Photosynthesis",
        "Photosynthesis regulation",
        "Cell cycle",
    ]


def test_segmentation_handles_empty_and_topics_without_searchable_text():
    assert segment_transcript("", max_topics=20, max_chars=200).topics == []
    assert segment_transcript(
        "1. ...\n---\nPage 3", max_topics=20, max_chars=200
    ).topics == []
    assert segment_transcript(
        "Page 2 of 10\n2 / 10\n1. Photosynthesis",
        max_topics=20,
        max_chars=200,
    ).topics == ["Photosynthesis"]


def test_provided_topics_are_validated_and_deduplicated():
    assert prepare_provided_topics(
        [" Photosynthesis ", "photosynthesis.", "Cellular respiration"],
        max_topics=20,
        max_chars=200,
    ) == ["Photosynthesis", "Cellular respiration"]

    with pytest.raises(ApiError) as too_many:
        prepare_provided_topics(
            ["One", "Two"], max_topics=1, max_chars=200
        )
    assert too_many.value.code == "too_many_topics"

    with pytest.raises(ApiError) as no_terms:
        prepare_provided_topics(["..."], max_topics=20, max_chars=200)
    assert no_terms.value.code == "invalid_topic"


def test_matching_returns_exact_passage_and_anchor():
    sections = [
        {
            "index": 0,
            "anchor": {"kind": "pdf_page", "page": 1, "page_index": 0},
            "raw_text": PAGE_ONE,
        },
        {
            "index": 1,
            "anchor": {"kind": "pdf_page", "page": 2, "page_index": 1},
            "raw_text": PAGE_TWO,
        },
    ]

    result = match_topics(
        ["Cellular respiration"], sections, document_id="doc-1"
    )[0]

    assert result.status == "matched"
    assert result.confidence == 1.0
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.passage_id == "doc-1:1"
    assert match.section_index == 1
    assert match.anchor == sections[1]["anchor"]
    assert match.passage == PAGE_TWO
    assert match.passage_start == 0
    assert match.passage_end == len(PAGE_TWO)
    assert match.phrase_match is True
    assert match.matched_terms == ["cellular", "respiration"]


def test_matching_is_ambiguous_when_strong_candidates_are_close():
    sections = [
        {
            "index": 0,
            "anchor": {"kind": "pdf_page", "page": 1, "page_index": 0},
            "raw_text": "The Calvin cycle fixes carbon dioxide.",
        },
        {
            "index": 1,
            "anchor": {"kind": "pdf_page", "page": 2, "page_index": 1},
            "raw_text": "The Calvin cycle regenerates RuBP.",
        },
    ]

    result = match_topics(["Calvin cycle"], sections, document_id="doc-1")[0]

    assert result.status == "ambiguous"
    assert result.confidence == 1.0
    assert len(result.matches) == 2
    assert [match.section_index for match in result.matches] == [0, 1]
    assert "ambiguous_source_match" in [
        warning["code"] for warning in result.warnings
    ]


def test_matching_keeps_multiple_passages_with_a_clear_winner():
    sections = [
        {
            "index": 0,
            "anchor": {"kind": "pdf_page", "page": 1, "page_index": 0},
            "raw_text": "The Calvin cycle fixes carbon dioxide.",
        },
        {
            "index": 1,
            "anchor": {"kind": "pdf_page", "page": 2, "page_index": 1},
            "raw_text": "Calvin and cycle are described separately here.",
        },
    ]

    result = match_topics(["Calvin cycle"], sections, document_id="doc-1")[0]

    assert result.status == "matched"
    assert result.confidence == 1.0
    assert len(result.matches) == 2
    assert result.matches[0].score == 1.0
    assert result.matches[1].score == 0.8
    assert result.matches[1].phrase_match is False
    assert "multiple_source_matches" in [
        warning["code"] for warning in result.warnings
    ]


def test_matching_rejects_weak_term_overlap():
    sections = [
        {
            "index": 0,
            "anchor": {"kind": "pdf_page", "page": 1, "page_index": 0},
            "raw_text": PAGE_TWO,
        }
    ]

    result = match_topics(
        ["Cellular respiration glycolysis"], sections, document_id="doc-1"
    )[0]

    assert result.status == "unmatched"
    assert result.confidence == 0.0
    assert result.matches == []
    assert "no_strong_source_match" in [
        warning["code"] for warning in result.warnings
    ]


def test_matching_normalizes_singular_plural_and_ing_forms():
    sections = [
        {
            "index": 0,
            "anchor": {"kind": "pdf_page", "page": 1, "page_index": 0},
            "raw_text": "A parasite is reproducing in host cells.",
        }
    ]
    result = match_topics(["parasites reproduce in cell"], sections, document_id="doc")[0]
    assert result.status == "matched"
    assert result.matches[0].passage == sections[0]["raw_text"]


def test_adjacent_matching_pages_return_in_page_order_not_score_order():
    sections = [
        {"index": 0, "anchor": {"kind": "pdf_page", "page": 1}, "raw_text": "Parasites and malaria are discussed."},
        {"index": 1, "anchor": {"kind": "pdf_page", "page": 2}, "raw_text": "Malaria parasites cause disease."},
    ]
    result = match_topics(["malaria parasites"], sections, document_id="doc")[0]
    assert [match.anchor["page"] for match in result.matches] == [1, 2]
    assert result.matches[1].score > result.matches[0].score


def test_contiguous_run_is_capped_and_warned():
    from app.config import Settings

    sections = [
        {"index": page - 1, "anchor": {"kind": "pdf_page", "page": page}, "raw_text": f"Malaria parasite material page {page}."}
        for page in range(1, 5)
    ]
    settings = Settings(min_match_score=0.62, max_matches_per_topic=2)
    result = match_topics(["malaria parasites"], sections, document_id="doc", settings=settings)[0]
    assert [match.anchor["page"] for match in result.matches] == [1, 2]
    assert "matches_truncated" in [warning["code"] for warning in result.warnings]


def test_optional_synonym_map_expands_topic_terms(monkeypatch):
    import app.services.topic_matching as matching

    monkeypatch.setattr(matching, "_SYNONYMS", {"myocardial": {"cardiac"}})
    sections = [
        {"index": 0, "anchor": {"kind": "pdf_page", "page": 1}, "raw_text": "Cardiac tissue receives blood."}
    ]
    result = matching.match_topics(["myocardial tissue"], sections, document_id="doc")[0]
    assert result.status == "matched"
    assert result.matches[0].passage[ result.matches[0].excerpt_start : result.matches[0].excerpt_end ] == result.matches[0].excerpt


def test_topic_review_matches_pdf_with_verifiable_anchor(client, identity):
    document_id = _upload_document(
        client, build_pdf([PAGE_ONE, PAGE_TWO]), "lecture.pdf"
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "1. Photosynthesis\n2. Cellular respiration")

    response = _review(client, source["source_id"])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_available"] is True
    assert body["source_material"] == {
        "status": "available",
        "section_count": 2,
    }
    assert body["segmentation"]["input"] == "transcript"
    assert body["segmentation"]["topic_count"] == 2
    assert [topic["status"] for topic in body["topics"]] == [
        "matched",
        "matched",
    ]

    document = storage.get_for_owner(document_id, identity["user_id"])
    assert document is not None
    first_section = _section(document.sections, 0)
    second_section = _section(document.sections, 1)
    first_match = body["topics"][0]["matches"][0]
    second_match = body["topics"][1]["matches"][0]
    assert first_match["anchor"] == first_section["anchor"]
    assert first_match["passage"] == first_section["raw_text"]
    assert first_match["passage_start"] == 0
    assert first_match["passage_end"] == len(first_section["raw_text"])
    assert second_match["anchor"] == second_section["anchor"]
    assert second_match["passage"] == second_section["raw_text"]


def test_topic_review_returns_ambiguous_candidates(client):
    document_id = _upload_document(
        client,
        build_pdf(
            [
                "The Calvin cycle fixes carbon dioxide.",
                "The Calvin cycle regenerates RuBP.",
            ]
        ),
        "cycles.pdf",
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Calvin cycle")

    body = _review(client, source["source_id"]).json()

    topic = body["topics"][0]
    assert topic["status"] == "ambiguous"
    assert [match["anchor"]["page"] for match in topic["matches"]] == [1, 2]
    assert len({match["passage"] for match in topic["matches"]}) == 2


def test_topic_review_returns_unmatched_without_weak_passages(client):
    document_id = _upload_document(client, build_pdf([PAGE_ONE]), "one.pdf")
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Mitosis")

    body = _review(client, source["source_id"]).json()

    topic = body["topics"][0]
    assert topic["status"] == "unmatched"
    assert topic["matches"] == []
    assert topic["confidence"] == 0.0


def test_reviewed_topics_override_transcript_for_matching(client):
    document_id = _upload_document(
        client, build_pdf([PAGE_ONE, PAGE_TWO]), "lecture.pdf"
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Photosynthesis")

    body = _review(
        client, source["source_id"], topics=["Cellular respiration"]
    ).json()

    assert body["segmentation"]["input"] == "provided"
    assert body["topics"][0]["text"] == "Cellular respiration"
    assert body["topics"][0]["status"] == "matched"
    assert body["topics"][0]["matches"][0]["anchor"]["page"] == 2


def test_topic_review_handles_unlinked_and_unavailable_documents(client):
    unlinked = _create_source(client)
    _set_transcript(client, unlinked["source_id"], "Photosynthesis")
    unlinked_body = _review(client, unlinked["source_id"]).json()

    assert unlinked_body["document"] is None
    assert unlinked_body["document_available"] is False
    assert unlinked_body["source_material"]["status"] == "not_linked"
    assert unlinked_body["topics"][0]["status"] == "unmatched"
    assert "document_not_linked" in [
        warning["code"] for warning in unlinked_body["warnings"]
    ]

    document_id = _upload_document(client, build_pdf([PAGE_ONE]), "one.pdf")
    linked = _create_source(client, document_id)
    _set_transcript(client, linked["source_id"], "Photosynthesis")
    assert client.delete(f"/api/v1/documents/{document_id}").status_code == 204

    unavailable_body = _review(client, linked["source_id"]).json()

    assert unavailable_body["document"] is not None
    assert unavailable_body["document_available"] is False
    assert unavailable_body["source_material"]["status"] == "unavailable"
    assert unavailable_body["topics"][0]["status"] == "unmatched"
    assert unavailable_body["topics"][0]["matches"] == []
    assert "document_unavailable" in [
        warning["code"] for warning in unavailable_body["warnings"]
    ]


def test_topic_review_handles_empty_document_and_empty_transcript(client):
    empty_document_id = _upload_document(
        client, build_pdf([""]), "empty.pdf"
    )
    empty_source = _create_source(client, empty_document_id)
    _set_transcript(client, empty_source["source_id"], "Photosynthesis")

    empty_material = _review(client, empty_source["source_id"]).json()

    assert empty_material["document_available"] is True
    assert empty_material["source_material"]["status"] == "empty"
    assert empty_material["topics"][0]["status"] == "unmatched"
    assert "source_material_empty" in [
        warning["code"] for warning in empty_material["warnings"]
    ]

    document_id = _upload_document(client, build_pdf([PAGE_ONE]), "one.pdf")
    no_text_source = _create_source(client, document_id)
    empty_transcript = _review(client, no_text_source["source_id"]).json()

    assert empty_transcript["segmentation"]["topic_count"] == 0
    assert empty_transcript["topics"] == []
    assert "no_transcript" in [
        warning["code"] for warning in empty_transcript["warnings"]
    ]


def test_topic_review_is_owner_scoped(client, make_user):
    document_id = _upload_document(client, build_pdf([PAGE_ONE]), "one.pdf")
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Photosynthesis")
    other = make_user()

    response = _review(
        client, source["source_id"], headers=other["headers"]
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "topic_source_not_found"


def test_topic_review_preserves_docx_section_anchors(client, identity):
    document_id = _upload_document(
        client,
        build_docx(
            [
                ("heading", "Cellular Respiration"),
                ("paragraph", "Glycolysis breaks glucose into pyruvate."),
            ]
        ),
        "lecture.docx",
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Glycolysis")

    body = _review(client, source["source_id"]).json()

    topic = body["topics"][0]
    assert topic["status"] == "matched"
    match = topic["matches"][0]
    document = storage.get_for_owner(document_id, identity["user_id"])
    assert document is not None
    section = _section(document.sections, match["section_index"])
    assert match["anchor"] == section["anchor"]
    assert match["anchor"]["kind"] == "docx_section"
    assert match["anchor"]["section_label"] == "Cellular Respiration"
    assert match["anchor"]["paragraph_index"] == 1
    assert match["passage"] == section["raw_text"]
