from __future__ import annotations

import time

from tests.helpers import build_png


def _tsv(rows):
    lines = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    word = 0
    for row_index, cells in enumerate(rows):
        for column_index, text in enumerate(cells):
            word += 1
            lines.append(
                f"5\t1\t1\t1\t1\t{word}\t{20 + column_index * 180}\t{20 + row_index * 40}"
                f"\t120\t24\t92\t{text}"
            )
    return "\n".join(lines) + "\n"


def _create_source(client):
    response = client.post(
        "/api/v1/topic-sources",
        files={"images": ("table.png", build_png(64), "image/png")},
        data={"skip_ocr": "true"},
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def test_table_read_and_export_are_owner_scoped(client, identity, make_user, monkeypatch):
    monkeypatch.setattr(
        "app.services.table_reader.run_tesseract_tsv",
        lambda *_args: _tsv([["Name", "Phone"], ["Ada", "0803"]]),
    )
    source_id = _create_source(client)

    read = client.post(
        "/api/v1/tables", json={"source_id": source_id, "image_index": 0}
    )
    assert read.status_code == 201, read.text
    body = read.json()
    assert body["columns"] == ["Name", "Phone"]
    assert body["rows"] == [["Ada", "0803"]]
    assert body["engine"] == "local"
    assert body["result_id"]

    other = make_user()
    denied = client.get(
        f"/api/v1/tables/results/{body['result_id']}", headers=other["headers"]
    )
    assert denied.status_code == 404
    denied_export = client.post(
        f"/api/v1/tables/results/{body['result_id']}/exports",
        json={"format": "csv"},
        headers=other["headers"],
    )
    assert denied_export.status_code == 404

    started = client.post(
        f"/api/v1/tables/results/{body['result_id']}/exports",
        json={
            "format": "csv",
            "columns": ["Name", "Phone"],
            "rows": [["Ada", "0803"], ["Grace", "+234"]],
        },
    )
    assert started.status_code == 202, started.text
    job = started.json()
    for _ in range(50):
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
        job = client.get(f"/api/v1/tables/exports/{job['job_id']}").json()
    assert job["status"] == "completed", job
    assert job["name"].endswith(".csv")

    download = client.get(f"/api/v1/tables/files/{job['file_id']}")
    assert download.status_code == 200
    assert "Ada" in download.content.decode("utf-8-sig")
    assert "0803" in download.content.decode("utf-8-sig")
    assert "+234" in download.content.decode("utf-8-sig")


    invalid = client.post(
        f"/api/v1/tables/results/{body['result_id']}/exports",
        json={
            "format": "csv",
            "columns": ["Name", "Phone"],
            "rows": [["only one"]],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "table_edit_invalid"


def test_table_result_expired_or_missing_is_a_typed_error(client):
    response = client.get("/api/v1/tables/results/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "table_result_not_found"


def test_xlsx_export_is_a_valid_zip_package():
    from app.services.table_results import TableResultRecord
    from app.services.table_exporter import build_csv, build_xlsx

    record = TableResultRecord(
        "r1",
        "u1",
        "s1",
        0,
        {
            "columns": ["Name", "Phone"],
            "rows": [["Ada", "0803"]],
        },
        0.0,
        1.0,
    )

    data = build_xlsx(record)
    csv_text = build_csv(record).decode("utf-8-sig")

    assert data[:2] == b"PK"
    assert b"inlineStr" in data
    assert "0803" in csv_text


def test_docx_export_is_a_real_table_without_a_duplicate_header():
    from docx import Document

    from app.services.table_results import TableResultRecord
    from app.services.table_exporter import build_docx

    record = TableResultRecord(
        "r1",
        "u1",
        "s1",
        0,
        {
            "columns": ["S/N", "Name", "Phone"],
            "rows": [[str(index), f"Person {index}", f"08030000{index:02d}"] for index in range(1, 13)],
        },
        0.0,
        1.0,
    )

    document = Document(__import__("io").BytesIO(build_docx(record)))
    table = document.tables[0]

    assert len(table.rows) == 13
    assert len(table.columns) == 3
    assert table.rows[0].cells[1].text == "Name"
    assert table.rows[1].cells[2].text == "0803000001"
