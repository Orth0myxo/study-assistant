from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.services import table_reader
from app.services.table_reader import (
    TableRead,
    _enforce_limits,
    build_table,
    merge_tables,
    parse_table_text,
    parse_tesseract_tsv,
)


def _tsv(rows, *, title=None, column_gap=150, row_gap=40, word_width=60):
    lines = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    word = 0

    def add(text, left, top, width, height, confidence):
        nonlocal word
        word += 1
        lines.append(
            f"5\t1\t1\t1\t1\t{word}\t{left}\t{top}\t{width}\t{height}"
            f"\t{confidence}\t{text}"
        )

    row_index = 0
    if title:
        for index, part in enumerate(title.split()):
            add(part, 20 + index * (word_width - 4), 10, word_width - 8, 20, 95)
        row_index = 1
    for cells in rows:
        for column_index, text in enumerate(cells):
            if text:
                add(
                    text,
                    20 + column_index * column_gap,
                    30 + row_index * row_gap,
                    word_width,
                    22,
                    92,
                )
        row_index += 1
    return "\n".join(lines) + "\n"


def _rows(column_count, row_count):
    header = [f"h{index + 1}" for index in range(column_count)]
    body = [
        [f"r{row_index + 1}c{column_index + 1}" for column_index in range(column_count)]
        for row_index in range(row_count)
    ]
    return header, body


@pytest.mark.parametrize("column_count", [2, 4, 6, 9])
def test_word_boxes_are_grouped_into_the_layout_they_form(column_count):
    header, body = _rows(column_count, 3)

    table = build_table(parse_tesseract_tsv(_tsv([header, *body])))

    assert table.columns == header
    assert table.rows == body
    assert table.column_count == column_count
    assert table.title == ""
    assert table.engine == "local"


@pytest.mark.parametrize("column_count", [3, 6, 8])
def test_a_single_cluster_above_the_table_is_a_title_not_a_row(column_count):
    header, body = _rows(column_count, 2)
    title = " ".join(f"t{index}" for index in range(3))

    table = build_table(
        parse_tesseract_tsv(_tsv([header, *body], title=title))
    )

    assert table.title == title
    assert table.columns == header
    assert table.rows == body
    assert title not in [cell for row in table.rows for cell in row]


@pytest.mark.parametrize(
    "separator",
    ["\t", ",", "  ", " "],
)
def test_typed_text_is_split_without_ocr_or_ai(separator, monkeypatch):
    header, body = _rows(6, 2)
    lines = [header, *body]
    text = "\n".join(separator.join(row) for row in lines)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("text input must not call OCR or AI")

    monkeypatch.setattr(table_reader, "run_tesseract_tsv", unexpected)
    monkeypatch.setattr(table_reader, "get_image_reader", unexpected)

    table = parse_table_text(
        text,
        SimpleNamespace(table_max_rows=100, table_max_columns=50, table_max_cell_chars=500),
    )

    assert table.engine == "text"
    assert table.columns == header
    assert table.rows == body


@pytest.mark.parametrize("separator", ["\t", "  ", " "])
def test_typed_text_keeps_a_title_line_out_of_the_table(separator):
    header, body = _rows(5, 2)
    title = " ".join(f"t{index}" for index in range(2))
    text = "\n".join(
        [title] + [separator.join(row) for row in [header, *body]]
    )

    table = parse_table_text(
        text,
        SimpleNamespace(table_max_rows=100, table_max_columns=50, table_max_cell_chars=500),
    )

    assert table.title == title
    assert table.columns == header
    assert table.rows == body


def test_low_confidence_cells_are_flagged_for_review():
    header, body = _rows(4, 1)
    raw = _tsv([header, *body])
    raw = raw.replace(
        f"\t92\t{body[0][0]}\n",
        f"\t30\t{body[0][0]}\n",
    )

    table = build_table(parse_tesseract_tsv(raw))

    assert table.low_confidence[0][0] is True
    assert any(item["code"] == "table_low_confidence" for item in table.warnings)


def test_non_word_rows_are_ignored():
    raw = _tsv([["only"]]) + "1\t1\t1\t1\t0\t0\t0\t0\t0\t0\t-1\t\n"

    words = parse_tesseract_tsv(raw)

    assert [word.text for word in words] == ["only"]


def test_unreadable_image_is_a_typed_client_error():
    with pytest.raises(ApiError) as caught:
        build_table([])

    assert caught.value.code == "table_not_readable"
    assert caught.value.status_code == 422


def test_row_and_column_limits_reject_oversized_tables():
    header, body = _rows(3, 3)
    table = build_table(parse_tesseract_tsv(_tsv([header, *body])))
    settings = SimpleNamespace(
        table_max_rows=2, table_max_columns=3, table_max_cell_chars=500
    )

    with pytest.raises(ApiError) as caught:
        _enforce_limits(table, settings)

    assert caught.value.code == "table_too_large"


def test_headerless_reads_keep_every_row_and_use_editable_placeholders():
    width = 4
    first = TableRead(
        columns=[],
        rows=[[f"a{index}" for index in range(width)]],
        confidence=[[100.0] * width],
        low_confidence=[[False] * width],
        engine="ai",
        has_header=False,
    )
    second = TableRead(
        columns=[],
        rows=[[f"b{index}" for index in range(width)]],
        confidence=[[100.0] * width],
        low_confidence=[[False] * width],
        engine="ai",
        has_header=False,
    )

    merged = merge_tables([first, second])

    assert merged.has_header is False
    assert merged.columns == [f"Column {index + 1}" for index in range(width)]
    assert merged.rows == first.rows + second.rows


def test_several_reads_merge_in_order_and_remove_a_repeated_boundary():
    header, first, shared, last = (
        [f"h{index}" for index in range(5)],
        [f"a{index}" for index in range(5)],
        [f"b{index}" for index in range(5)],
        [f"c{index}" for index in range(5)],
    )
    title = " ".join(f"t{index}" for index in range(2))
    left = TableRead(
        columns=header,
        rows=[first, shared],
        confidence=[[100.0] * 5, [100.0] * 5],
        low_confidence=[[False] * 5, [False] * 5],
        engine="local",
        title=title,
    )
    right = TableRead(
        columns=header,
        rows=[shared, last],
        confidence=[[100.0] * 5, [100.0] * 5],
        low_confidence=[[False] * 5, [False] * 5],
        engine="local",
    )

    merged = merge_tables([left, right])

    assert merged.title == title
    assert merged.columns == header
    assert merged.rows == [first, shared, last]
