from __future__ import annotations

from tests.helpers import build_pdf

PDF_CT = "application/pdf"
PAGES = [f"Page {i} original wording about anemia." for i in range(1, 6)]


def _upload(client, identity):
    r = client.post(
        "/api/v1/documents",
        files={"file": ("n.pdf", build_pdf(PAGES), PDF_CT)},
        headers=identity["headers"],
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["document_id"]


def test_extract_pages_verbatim(client, identity):
    doc = _upload(client, identity)
    r = client.post(
        f"/api/v1/documents/{doc}/extract",
        json={"selector": {"type": "pages", "spec": "2-3"}, "mode": "source_passages"},
        headers=identity["headers"],
    )
    assert r.status_code == 200, r.text
    texts = [p["text"] for p in r.json()["passages"]]
    assert len(texts) == 2 and "Page 2 original" in texts[0]


def test_export_pdf_and_docx(client, identity):
    doc = _upload(client, identity)
    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
        r = client.get(
            f"/api/v1/documents/{doc}/extract/export",
            params={"format": fmt, "type": "pages", "spec": "1-2"},
            headers=identity["headers"],
        )
        assert r.status_code == 200 and r.content.startswith(magic)


def test_other_user_cannot_extract(client, identity, make_user):
    doc = _upload(client, identity)
    other = make_user()
    r = client.post(
        f"/api/v1/documents/{doc}/extract",
        json={"selector": {"type": "whole"}},
        headers=other["headers"],
    )
    assert r.status_code == 404


def test_chat_command_unavailable_without_groq(client, identity, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    r = client.post("/api/v1/chat/command", json={"message": "pages 1-2"}, headers=identity["headers"])
    assert r.status_code == 200 and r.json()["command"] is None
