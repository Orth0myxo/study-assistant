from __future__ import annotations

from app.services.heading_sections import (
    resolve_heading_section,
    resolve_heading_section_match,
)
from app.services.storage import storage
from tests.helpers import build_docx

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _document(client, identity):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "notes.docx",
                build_docx(
                    [
                        ("heading", "8.4 Prevalence"),
                        ("para", "Prevalence text for the section."),
                        ("heading", "8.5 Incidence"),
                        ("para", "Incidence text for the section."),
                    ]
                ),
                DOCX_CT,
            )
        },
    )
    assert response.status_code == 201, response.text
    document = storage.get_for_owner(
        response.json()["document_id"], identity["user_id"]
    )
    assert document is not None
    return document


def test_resolver_matches_an_exact_heading_and_keeps_the_whole_section(client, identity):
    document = _document(client, identity)

    index, section, kind, nearest = resolve_heading_section(
        document, [], "8.4 Prevalence"
    )

    assert kind == "exact"
    assert nearest == []
    assert section is not None
    assert section.text == "8.4 Prevalence"
    assert "Prevalence text for the section." in section.raw_text
    assert "Incidence text" not in section.raw_text
    assert len(index.sections) == 2


def test_resolver_matches_a_typed_section_number(client, identity):
    document = _document(client, identity)

    _index, section, kind, _nearest = resolve_heading_section(document, [], "8.5")

    assert kind == "number"
    assert section is not None
    assert section.text == "8.5 Incidence"


def test_resolver_matches_a_close_heading_and_reports_it(client, identity):
    document = _document(client, identity)

    _index, section, kind, _nearest = resolve_heading_section(
        document, [], "Prevalance"
    )

    assert kind == "closest"
    assert section is not None
    assert section.text == "8.4 Prevalence"


def test_resolver_returns_nearest_headings_when_nothing_matches(client, identity):
    document = _document(client, identity)

    _index, section, kind, nearest = resolve_heading_section(
        document, [], "Quantum chromodynamics"
    )

    assert kind == "none"
    assert section is None
    assert nearest
    assert "8.4 Prevalence" in nearest


def _document_from_blocks(client, identity, blocks):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("headings.docx", build_docx(blocks), DOCX_CT)},
    )
    assert response.status_code == 201, response.text
    document = storage.get_for_owner(
        response.json()["document_id"], identity["user_id"]
    )
    assert document is not None
    return document


def test_resolver_reports_duplicate_exact_headings_as_ambiguous(client, identity):
    document = _document_from_blocks(
        client,
        identity,
        [
            ("heading", "Introduction"),
            ("para", "First introduction."),
            ("heading", "Introduction"),
            ("para", "Second introduction."),
        ],
    )

    match = resolve_heading_section_match(document, [], "Introduction")

    assert match.match_kind == "ambiguous"
    assert match.section is None
    assert len(match.candidates) == 2
    assert {item["heading"] for item in match.candidates} == {"Introduction"}
    assert all(item["path"] == ["Introduction"] for item in match.candidates)


def test_resolver_reports_duplicate_numbers_as_ambiguous(client, identity):
    document = _document_from_blocks(
        client,
        identity,
        [
            ("heading", "1.1 Overview"),
            ("para", "First overview."),
            ("heading", "1.1 Details"),
            ("para", "Second overview."),
        ],
    )

    match = resolve_heading_section_match(document, [], "1.1")

    assert match.match_kind == "ambiguous"
    assert match.section is None
    assert {item["heading"] for item in match.candidates} == {
        "1.1 Overview",
        "1.1 Details",
    }


def test_resolver_prefers_one_exact_heading_over_partial_matches(client, identity):
    document = _document_from_blocks(
        client,
        identity,
        [
            ("heading", "Prevalence"),
            ("para", "Exact section."),
            ("heading", "Prevalence details"),
            ("para", "Partial section."),
        ],
    )

    match = resolve_heading_section_match(document, [], "Prevalence")

    assert match.match_kind == "exact"
    assert match.section is not None
    assert match.section.text == "Prevalence"


def test_resolver_reports_a_low_confidence_fuzzy_match(client, identity):
    document = _document(client, identity)

    match = resolve_heading_section_match(document, [], "Prevalance")

    assert match.match_kind == "closest"
    assert match.section is not None
    assert match.confidence is not None
    assert match.confidence < 0.8
