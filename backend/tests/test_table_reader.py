from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.errors import ApiError
from app.services.table_reader import (
    _enforce_limits,
    build_table,
    parse_tesseract_tsv,
)


def _tsv(words):
    lines = [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    ]
    for index, (text, left, top, width, height, conf) in enumerate(words, start=1):
        lines.append(
            f"5\t1\t1\t1\t1\t{index}\t{left}\t{top}\t{width}\t{height}\t{conf}\t{text}"
        )
    return "\n".join(lines) + "\n"


def test_word_boxes_are_grouped_into_rows_and_columns():
    words = [
        ("Name", 10, 10, 55, 20, 95),
        ("Phone", 220, 10, 60, 20, 92),
        ("Ada", 10, 45, 40, 20, 90),
        ("0803", 220, 45, 50, 20, 88),
        ("Grace", 10, 80, 60, 20, 91),
        ("+234", 220, 80, 55, 20, 86),
    ]

    table = build_table(parse_tesseract_tsv(_tsv(words)))

    assert table.columns == ["Name", "Phone"]
    assert table.rows == [
        ["Ada", "0803"],
        ["Grace", "+234"],
    ]
    assert table.engine == "local"
    assert not any(any(row) for row in table.low_confidence)


def test_low_confidence_cells_are_flagged_for_review():
    words = [
        ("Name", 10, 10, 55, 20, 90),
        ("Phone", 220, 10, 60, 20, 90),
        ("Ada", 10, 45, 40, 20, 90),
        ("0803", 220, 45, 50, 20, 30),
    ]

    table = build_table(parse_tesseract_tsv(_tsv(words)))

    assert table.low_confidence[0] == [False, True]
    assert any(item["code"] == "table_low_confidence" for item in table.warnings)


def test_missing_header_text_gets_a_visible_placeholder():
    words = [
        ("Ada", 10, 10, 40, 20, 90),
        ("0803", 220, 10, 50, 20, 90),
    ]

    table = build_table(parse_tesseract_tsv(_tsv(words)))

    assert table.columns == ["Ada", "0803"]
    assert table.rows == []


def test_non_word_rows_are_ignored():
    raw = _tsv([("Ada", 10, 10, 40, 20, 90)]) + "1\t1\t1\t1\t0\t0\t0\t0\t0\t0\t-1\t\n"

    words = parse_tesseract_tsv(raw)

    assert [word.text for word in words] == ["Ada"]


def test_unreadable_image_is_a_typed_client_error():
    with pytest.raises(ApiError) as caught:
        build_table([])

    assert caught.value.code == "table_not_readable"
    assert caught.value.status_code == 422


def test_row_and_column_limits_reject_oversized_tables():
    table = build_table(
        parse_tesseract_tsv(
            _tsv([
                ("A", 10, 10, 20, 20, 90),
                ("B", 100, 10, 20, 20, 90),
                ("1", 10, 40, 20, 20, 90),
                ("2", 100, 40, 20, 20, 90),
            ])
        )
    )
    settings = SimpleNamespace(
        table_max_rows=1, table_max_columns=30, table_max_cell_chars=500
    )

    with pytest.raises(ApiError) as caught:
        _enforce_limits(table, settings)

    assert caught.value.code == "table_too_large"
