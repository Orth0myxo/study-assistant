from __future__ import annotations

from app.services.heading_sections import resolve_heading_section
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
