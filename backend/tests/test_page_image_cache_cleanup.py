from __future__ import annotations

from app.services.storage import storage
from tests.helpers import build_pdf


def test_document_delete_removes_png_and_export_jpeg_caches(client):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "cache-test.pdf",
                build_pdf(["Cache test page."]),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["document_id"]
    route_png = storage.root / f"{document_id}.page-1.png"
    export_jpeg = storage.root / f"{document_id}.page-1.export.jpg"
    route_png.write_bytes(b"png cache")
    export_jpeg.write_bytes(b"jpeg cache")

    deleted = client.delete(f"/api/v1/documents/{document_id}")

    assert deleted.status_code == 204
    assert not route_png.exists()
    assert not export_jpeg.exists()
