import pytest

from app.errors import ApiError
from app.services.range_extraction import extract_range
from app.services.storage import storage
from tests.helpers import build_pdf


@pytest.fixture
def pdf_sections():
    return [
        {"index": 0, "anchor": {"kind": "pdf_page", "page": 1}, "raw_text": "  Page one\nExact.  "},
        {"index": 1, "anchor": {"kind": "pdf_page", "page": 2}, "raw_text": "Question 1: alpha\nQuestion 2: beta"},
        {"index": 2, "anchor": {"kind": "pdf_page", "page": 4}, "raw_text": "Chapter 3\nThird chapter"},
    ]


@pytest.mark.parametrize("selector,expected", [
    ("1", [1]), ("1-2", [1, 2]), ("2 to 1", [1, 2]),
    ("pages 1, 2 and 4", [1, 2, 4]),
])
def test_pdf_page_ranges_lists_and_reversed(selector, expected, pdf_sections):
    result = extract_range(document_id="doc", format="pdf", page_count=4, sections=pdf_sections, selector=selector)
    assert [s["anchor"]["page"] for s in result.sections] == expected


def test_exact_raw_text_is_returned(pdf_sections):
    result = extract_range(document_id="doc", format="pdf", page_count=4, sections=pdf_sections, selector="1")
    assert result.sections[0]["raw_text"] == pdf_sections[0]["raw_text"]


def test_out_of_bounds_includes_actual_page_count(pdf_sections):
    with pytest.raises(ApiError) as caught:
        extract_range(document_id="doc", format="pdf", page_count=4, sections=pdf_sections, selector="4-5")
    assert caught.value.status_code == 422
    assert caught.value.details["page_count"] == 4


def test_unreadable_pages_are_warned(pdf_sections):
    result = extract_range(document_id="doc", format="pdf", page_count=4, sections=pdf_sections, selector="2-4")
    assert result.warnings[0]["code"] == "unreadable_pages_dropped"
    assert "3" in result.warnings[0]["message"]


def test_question_range_selects_source_sections(pdf_sections):
    result = extract_range(document_id="doc", format="pdf", page_count=4, sections=pdf_sections, selector="question 1 to 50")
    assert result.sections[0]["raw_text"] == pdf_sections[1]["raw_text"]


def test_docx_chapter_uses_heading_label_and_warns():
    sections = [{"index": 0, "anchor": {"kind": "docx_section", "section_label": "Chapter 3: Cells", "paragraph_index": 3}, "raw_text": "Cell material"}]
    result = extract_range(document_id="doc", format="docx", page_count=None, sections=sections, selector="chapter 3")
    assert result.sections[0]["raw_text"] == "Cell material"
    assert result.warnings[0]["code"] == "docx_no_pages"


def test_docx_numeric_range_maps_to_paragraph_indices():
    sections = [{"index": i, "anchor": {"kind": "docx_section", "section_label": "Document", "paragraph_index": i}, "raw_text": f"P{i}"} for i in range(1, 4)]
    result = extract_range(document_id="doc", format="docx", page_count=None, sections=sections, selector="2-3")
    assert [item["raw_text"] for item in result.sections] == ["P2", "P3"]


def test_range_study_reuses_exportable_study_job_shape_without_ai(client):
    uploaded = client.post(
        "/api/v1/documents",
        files={"file": ("range.pdf", build_pdf(["First source page with enough readable study text.", "Second source page with enough readable study text."]), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document_id"]
    record = storage.get(document_id)
    assert record is not None

    response = client.post(
        f"/api/v1/documents/{document_id}/range-study",
        json={"selector": "pages 2", "mode": "source_passages"},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["mode"] == "extracted_text_only"
    assert result["items"][0]["text"] == record.sections[1]["raw_text"]
    exported = client.get(f"/api/v1/study-jobs/{result['job_id']}/export?format=pdf")
    assert exported.status_code == 200
    assert exported.content.startswith(b"%PDF")
