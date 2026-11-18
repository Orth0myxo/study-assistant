from __future__ import annotations

import time

from tests.helpers import build_png


def _tsv(rows, *, title=None):
    lines = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    word = 0
    row_index = 0
    if title:
        for index, part in enumerate(title.split()):
            word += 1
            lines.append(
                f"5\t1\t1\t1\t1\t{word}\t{20 + index * 56}\t{10}"
                f"\t52\t20\t94\t{part}"
            )
        row_index = 1
    for cells in rows:
        for column_index, text in enumerate(cells):
            word += 1
            lines.append(
                f"5\t1\t1\t1\t1\t{word}\t{20 + column_index * 150}"
                f"\t{30 + row_index * 40}\t60\t22\t92\t{text}"
            )
        row_index += 1
    return "\n".join(lines) + "\n"


def _rows(width, count, start=0):
    header = [f"h{index + 1}" for index in range(width)]
    body = [
        [
            f"r{start + row + 1}c{column + 1}"
            if column
            else f"00{start + row + 1}"
            for column in range(width)
        ]
        for row in range(count)
    ]
    return header, body


def _create_source(client, image_count=1):
    files = [
        ("images", (f"table-{index}.png", build_png(64 + index), "image/png"))
        for index in range(image_count)
    ]
    response = client.post(
        "/api/v1/topic-sources",
        files=files,
        data={"skip_ocr": "true"},
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def _wait_for_job(client, job):
    for _ in range(50):
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
        job = client.get(f"/api/v1/tables/exports/{job['job_id']}").json()
    return job


def test_table_read_and_export_are_owner_scoped(
    client, identity, make_user, monkeypatch
):
    header, body = _rows(width=6, count=2)
    title = " ".join(f"t{index}" for index in range(3))
    monkeypatch.setattr(
        "app.services.table_reader.run_tesseract_tsv",
        lambda *_args: _tsv([header, *body], title=title),
    )
    source_id = _create_source(client)

    read = client.post(
        "/api/v1/tables", json={"source_id": source_id, "image_index": 0}
    )
    assert read.status_code == 201, read.text
    result = read.json()
    assert result["title"] == title
    assert result["columns"] == header
    assert result["rows"] == body
    assert result["engine"] == "local"
    assert result["result_id"]

    other = make_user()
    denied = client.get(
        f"/api/v1/tables/results/{result['result_id']}",
        headers=other["headers"],
    )
    assert denied.status_code == 404
    denied_export = client.post(
        f"/api/v1/tables/results/{result['result_id']}/exports",
        json={"format": "csv"},
        headers=other["headers"],
    )
    assert denied_export.status_code == 404

    edited = [row[:] for row in body]
    edited.append([f"x{index}" for index in range(len(header))])
    started = client.post(
        f"/api/v1/tables/results/{result['result_id']}/exports",
        json={
            "format": "csv",
            "title": title,
            "columns": header,
            "rows": edited,
        },
    )
    assert started.status_code == 202, started.text
    job = _wait_for_job(client, started.json())
    assert job["status"] == "completed", job
    assert job["name"].endswith(".csv")

    download = client.get(f"/api/v1/tables/files/{job['file_id']}")
    assert download.status_code == 200
    text = download.content.decode("utf-8-sig")
    assert title in text
    assert body[0][0] in text
    assert edited[-1][0] in text

    invalid = client.post(
        f"/api/v1/tables/results/{result['result_id']}/exports",
        json={
            "format": "csv",
            "columns": header,
            "rows": [body[0][:-1]],
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "table_edit_invalid"


def test_typed_text_reads_without_a_photo_source(client):
    header, body = _rows(width=5, count=2)
    text = "\n".join("\t".join(row) for row in [header, *body])

    response = client.post("/api/v1/tables/text", json={"text": text})

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["engine"] == "text"
    assert result["source_id"] == ""
    assert result["columns"] == header
    assert result["rows"] == body
    assert result["title"] == ""


def test_several_photos_merge_into_one_result(client, monkeypatch):
    header, first = _rows(width=4, count=1)
    _, second = _rows(width=4, count=1, start=1)
    tables = [([header, *first]), ([header, *second])]
    calls = iter(tables)
    monkeypatch.setattr(
        "app.services.table_reader.run_tesseract_tsv",
        lambda *_args: _tsv(next(calls)),
    )
    source_id = _create_source(client, image_count=2)

    response = client.post(
        "/api/v1/tables",
        json={"source_id": source_id, "image_indices": [0, 1]},
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["columns"] == header
    assert result["rows"] == first + second


def test_table_result_expired_or_missing_is_a_typed_error(client):
    response = client.get("/api/v1/tables/results/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "table_result_not_found"


def test_xlsx_export_preserves_title_header_and_values():
    import io

    from openpyxl import load_workbook

    from app.services.table_results import TableResultRecord
    from app.services.table_exporter import build_csv, build_xlsx

    header, body = _rows(width=4, count=2)
    title = " ".join(f"x{index}" for index in range(2))
    record = TableResultRecord(
        "r1",
        "u1",
        "s1",
        0,
        {"title": title, "columns": header, "rows": body},
        0.0,
        1.0,
    )

    data = build_xlsx(record)
    csv_text = build_csv(record).decode("utf-8-sig")

    assert data[:2] == b"PK"
    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet["A1"].value == title
    assert list(sheet.values)[1] == tuple(header)
    assert list(sheet.values)[2] == tuple(body[0])
    assert csv_text.splitlines()[0] == title
    assert body[0][0] in csv_text


def test_docx_export_is_a_real_table_without_a_duplicate_header():
    from docx import Document

    from app.services.table_results import TableResultRecord
    from app.services.table_exporter import build_docx

    header, body = _rows(width=6, count=11)
    title = " ".join(f"k{index}" for index in range(2))
    record = TableResultRecord(
        "r1",
        "u1",
        "s1",
        0,
        {"title": title, "columns": header, "rows": body},
        0.0,
        1.0,
    )

    document = Document(__import__("io").BytesIO(build_docx(record)))
    table = document.tables[0]

    assert document.paragraphs[0].text == title
    assert len(table.rows) == len(body) + 1
    assert len(table.columns) == len(header)
    assert table.rows[0].cells[1].text == header[1]
    assert table.rows[1].cells[-1].text == body[0][-1]
