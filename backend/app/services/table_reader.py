"""Read a table layout from Tesseract word boxes or an optional vision model.

The local path uses the geometry of recognized words only. It does not assume
fixed column names, row counts, or wording. Every returned cell is editable by
the user before export.
"""

from __future__ import annotations

import csv
import io
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.errors import ApiError
from app.services.ai_client import AiError, GeminiClient
from app.services.ocr import (
    OcrFailed,
    OcrTimeout,
    OcrUnavailable,
    run_tesseract_tsv,
)


@dataclass
class OcrWord:
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float


@dataclass
class TableRead:
    columns: list[str]
    rows: list[list[str]]
    confidence: list[list[float]]
    low_confidence: list[list[bool]]
    engine: str
    title: str = ""
    has_header: bool = True
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def as_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "has_header": self.has_header,
            "columns": self.columns,
            "rows": self.rows,
            "confidence": self.confidence,
            "low_confidence": self.low_confidence,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "engine": self.engine,
            "warnings": self.warnings,
        }


def parse_tesseract_tsv(value: str) -> list[OcrWord]:
    """Parse Tesseract's TSV word rows, ignoring page/line aggregate levels."""
    if not value.strip():
        return []
    reader = csv.DictReader(io.StringIO(value), delimiter="\t")
    words: list[OcrWord] = []
    for row in reader:
        try:
            level = int(row.get("level") or 0)
            if level != 5:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            left = float(row.get("left") or 0)
            top = float(row.get("top") or 0)
            width = float(row.get("width") or 0)
            height = float(row.get("height") or 0)
            confidence = float(row.get("conf") or -1)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        words.append(OcrWord(text, left, top, width, height, confidence))
    return words


def _row_groups(words: list[OcrWord], tolerance: float) -> list[list[OcrWord]]:
    rows: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (item.top + item.height / 2, item.left)):
        center = word.top + word.height / 2
        match = None
        for row in reversed(rows):
            if abs(center - row["center"]) <= tolerance:
                match = row
                break
        if match is None:
            rows.append({"center": center, "words": [word]})
        else:
            match["words"].append(word)
            weights = [item.height for item in match["words"]]
            match["center"] = sum(
                (item.top + item.height / 2) * weight
                for item, weight in zip(match["words"], weights)
            ) / max(sum(weights), 1)
    rows.sort(key=lambda row: row["center"])
    return [sorted(row["words"], key=lambda item: item.left) for row in rows]


def _column_regions(words: list[OcrWord], gap: float) -> list[tuple[float, float]]:
    intervals = sorted(
        (word.left, word.left + word.width) for word in words
    )
    merged: list[list[float]] = []
    for left, right in intervals:
        if not merged or left - merged[-1][1] > gap:
            merged.append([left, right])
        else:
            merged[-1][1] = max(merged[-1][1], right)
    return [(left, right) for left, right in merged]


def _row_cluster_count(words: list[OcrWord], gap: float) -> int:
    ordered = sorted(words, key=lambda item: item.left)
    count = 0
    right = -1.0
    for word in ordered:
        if word.left - right > gap:
            count += 1
        right = max(right, word.left + word.width)
    return count


def _split_title_rows(
    rows: list[list[OcrWord]], gap: float
) -> tuple[list[list[OcrWord]], list[list[OcrWord]]]:
    if not rows:
        return [], []
    first_table_row = next(
        (
            index
            for index, row in enumerate(rows)
            if _row_cluster_count(row, gap) >= 2
        ),
        None,
    )
    if first_table_row is None or first_table_row == 0:
        return [], rows
    return rows[:first_table_row], rows[first_table_row:]


def _column_index(word: OcrWord, regions: list[tuple[float, float]]) -> int:
    word_left, word_right = word.left, word.left + word.width
    best_index = 0
    best_overlap = -1.0
    for index, (left, right) in enumerate(regions):
        overlap = max(0.0, min(word_right, right) - max(word_left, left))
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index
    if best_overlap > 0:
        return best_index
    center = (word_left + word_right) / 2
    return min(
        range(len(regions)),
        key=lambda index: abs(center - (regions[index][0] + regions[index][1]) / 2),
    )


def _placeholder_columns(width: int) -> list[str]:
    return [f"Column {index + 1}" for index in range(width)]


def build_table(words: list[OcrWord]) -> TableRead:
    """Group word boxes into a title, header row, and data rows."""
    if not words:
        raise ApiError(
            "table_not_readable",
            "No table text was recognized. Try a clearer photo or read it with AI.",
            422,
        )
    heights = [word.height for word in words]
    median_height = statistics.median(heights)
    row_tolerance = max(8.0, median_height * 0.6)
    column_gap = max(10.0, median_height * 0.8)
    cluster_gap = max(12.0, median_height * 1.1)
    rows = _row_groups(words, row_tolerance)
    title_rows, table_rows = _split_title_rows(rows, cluster_gap)
    if not table_rows:
        raise ApiError("table_not_readable", "No table rows were recognized.", 422)
    table_words = [word for row in table_rows for word in row]
    regions = _column_regions(table_words, column_gap)
    if not regions:
        raise ApiError("table_not_readable", "No table columns were recognized.", 422)

    matrix: list[list[list[OcrWord]]] = [
        [[] for _ in regions] for _ in table_rows
    ]
    for row_index, row_words in enumerate(table_rows):
        for word in row_words:
            matrix[row_index][_column_index(word, regions)].append(word)

    cell_text: list[list[str]] = []
    cell_confidence: list[list[float]] = []
    cell_low: list[list[bool]] = []
    for row in matrix:
        texts: list[str] = []
        confidences: list[float] = []
        lows: list[bool] = []
        for cell_words in row:
            texts.append(
                " ".join(word.text for word in sorted(cell_words, key=lambda item: item.left))
            )
            valid = [word.confidence for word in cell_words if word.confidence >= 0]
            average = sum(valid) / len(valid) if valid else 0.0
            confidences.append(round(average, 1))
            lows.append(not cell_words or average < 70.0 or min(valid or [0.0]) < 45.0)
        cell_text.append(texts)
        cell_confidence.append(confidences)
        cell_low.append(lows)

    header = cell_text[0]
    columns = [
        header[index].strip() or f"Column {index + 1}"
        for index in range(len(regions))
    ]
    title = " ".join(
        " ".join(word.text for word in sorted(row, key=lambda item: item.left))
        for row in title_rows
    ).strip()
    data_rows = cell_text[1:]
    data_confidence = cell_confidence[1:]
    data_low = cell_low[1:]
    warnings: list[dict[str, str]] = []
    if any(any(row) for row in data_low):
        warnings.append(
            {
                "code": "table_low_confidence",
                "message": "Some cells were read with low confidence. Check them before export.",
            }
        )
    warnings.append(
        {
            "code": "table_from_image",
            "message": "This table was read from an image, not copied source text. Review every cell.",
        }
    )
    return TableRead(
        title=title,
        has_header=True,
        columns=columns,
        rows=data_rows,
        confidence=data_confidence,
        low_confidence=data_low,
        engine="local",
        warnings=warnings,
    )


def _split_text_rows(lines: list[str]) -> tuple[list[list[str]], bool]:
    if any("\t" in line for line in lines):
        return [line.split("\t") for line in lines], True
    if any("," in line for line in lines):
        try:
            return list(csv.reader(io.StringIO("\n".join(lines)))), True
        except csv.Error:
            return [line.split(",") for line in lines], True
    rows: list[list[str]] = []
    for line in lines:
        if re.search(r"\s{2,}", line):
            rows.append(re.split(r"\s{2,}", line.strip()))
        else:
            rows.append(line.split())
    return rows, False


def parse_table_text(text: str, settings: Settings) -> TableRead:
    """Split typed or pasted text without OCR or an AI provider."""
    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not lines:
        raise ApiError(
            "table_not_readable",
            "Enter table text with one row per line.",
            422,
        )
    raw_rows, explicit = _split_text_rows(lines)
    rows = [
        [cell.strip() for cell in row]
        for row in raw_rows
    ]
    title = ""
    if len(rows) > 1:
        later_width = max(len(row) for row in rows[1:])
        first_width = len(rows[0])
        is_title = first_width == 1 and later_width >= 2
        if not explicit and 1 < first_width < later_width:
            is_title = True
        if is_title:
            title = " ".join(cell for cell in rows[0] if cell)
            rows = rows[1:]
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    columns = [
        header[index].strip() or f"Column {index + 1}"
        for index in range(width)
    ]
    data_rows = rows[1:]
    confidence = [[100.0] * width for _ in data_rows]
    low = [[False] * width for _ in data_rows]
    return _enforce_limits(
        TableRead(
            title=title,
            has_header=True,
            columns=columns,
            rows=data_rows,
            confidence=confidence,
            low_confidence=low,
            engine="text",
            warnings=[
                {
                    "code": "table_from_text",
                    "message": "This table was split from typed or pasted text. Review every cell.",
                }
            ],
        ),
        settings,
    )


def _same_row(left: list[str], right: list[str]) -> bool:
    return [cell.strip().casefold() for cell in left] == [
        cell.strip().casefold() for cell in right
    ]


def _longest_overlap(previous: list[list[str]], following: list[list[str]]) -> int:
    limit = min(len(previous), len(following))
    for size in range(limit, 0, -1):
        if previous[-size:] == following[:size]:
            return size
    return 0


def merge_tables(tables: list[TableRead]) -> TableRead:
    """Merge several reads of one table in order, removing repeated headers/overlap."""
    if not tables:
        raise ApiError(
            "table_not_readable",
            "No table photos were read.",
            422,
        )
    width = max(
        [len(table.columns) for table in tables]
        + [len(row) for table in tables for row in table.rows]
    )
    header_tables = [
        table
        for table in tables
        if table.has_header and any(cell.strip() for cell in table.columns)
    ]
    header_source = header_tables[0] if header_tables else tables[0]
    columns = list(header_source.columns) + _placeholder_columns(
        width - len(header_source.columns)
    )
    title = next((table.title for table in tables if table.title.strip()), "")
    merged_rows: list[list[str]] = []
    merged_confidence: list[list[float]] = []
    merged_low: list[list[bool]] = []
    warnings: list[dict[str, str]] = []
    seen_warnings: set[str] = set()
    for index, table in enumerate(tables):
        for warning in table.warnings:
            code = warning.get("code", "")
            if code and code not in seen_warnings:
                seen_warnings.add(code)
                warnings.append(warning)
        rows: list[list[str]] = []
        confidence: list[list[float]] = []
        low: list[list[bool]] = []
        if (
            index > 0
            and table.has_header
            and table.columns
            and not _same_row(table.columns, columns[: len(table.columns)])
        ):
            rows.append(list(table.columns))
            confidence.append([100.0] * width)
            low.append([False] * width)
        for row_index, row in enumerate(table.rows):
            padded = list(row) + [""] * (width - len(row))
            rows.append(padded)
            confidence.append(
                list(table.confidence[row_index]) + [100.0] * (width - len(row))
            )
            low.append(
                list(table.low_confidence[row_index]) + [False] * (width - len(row))
            )
        overlap = _longest_overlap(merged_rows, rows)
        if overlap:
            rows = rows[overlap:]
            confidence = confidence[overlap:]
            low = low[overlap:]
        merged_rows.extend(rows)
        merged_confidence.extend(confidence)
        merged_low.extend(low)
    return TableRead(
        title=title,
        has_header=bool(header_tables),
        columns=columns,
        rows=merged_rows,
        confidence=merged_confidence,
        low_confidence=merged_low,
        engine=tables[0].engine,
        warnings=warnings,
    )


def _enforce_limits(table: TableRead, settings: Settings) -> TableRead:
    if table.row_count > settings.table_max_rows:
        raise ApiError(
            "table_too_large",
            f"The table has more than {settings.table_max_rows} rows. Split the image and try again.",
            422,
        )
    if table.column_count > settings.table_max_columns:
        raise ApiError(
            "table_too_large",
            f"The table has more than {settings.table_max_columns} columns. Split the image and try again.",
            422,
        )
    limit = max(settings.table_max_cell_chars, 1)
    if len(table.title) > limit:
        table.title = table.title[:limit]
        table.warnings.append(
            {
                "code": "table_title_truncated",
                "message": "The title was longer than the supported limit and was shortened.",
            }
        )
    if any(len(cell) > limit for cell in table.columns):
        table.columns = [cell[:limit] for cell in table.columns]
    if any(len(cell) > limit for row in table.rows for cell in row):
        table.rows = [
            [cell[:limit] for cell in row] for row in table.rows
        ]
        table.warnings.append(
            {
                "code": "table_cell_truncated",
                "message": "Some cells were longer than the supported limit and were shortened. Check them before export.",
            }
        )
    return table


def read_local_table(image: bytes, suffix: str, settings: Settings) -> TableRead:
    try:
        raw = run_tesseract_tsv(image, suffix, settings)
    except OcrUnavailable as exc:
        raise ApiError(
            "ocr_unavailable", "OCR is not available on the server.", 503
        ) from exc
    except OcrTimeout as exc:
        raise ApiError(
            "ocr_timeout", "The table image took too long to read.", 504
        ) from exc
    except OcrFailed as exc:
        raise ApiError("ocr_failed", "The table image could not be read.", 502) from exc
    return _enforce_limits(build_table(parse_tesseract_tsv(raw)), settings)


def read_ai_table(
    image: bytes,
    content_type: str,
    settings: Settings,
) -> TableRead:
    if settings.table_vision_provider != "gemini":
        raise ApiError(
            "table_vision_unavailable",
            "The configured table vision provider is not available.",
            503,
        )
    if not settings.gemini_api_key or not settings.gemini_model:
        raise ApiError(
            "table_vision_unavailable",
            "Table vision is not configured on the server.",
            503,
        )
    try:
        client = GeminiClient(settings)
        try:
            value = client.read_table_image(image, content_type)
        finally:
            client.close()
    except AiError as exc:
        raise ApiError(
            "table_vision_failed",
            "The image could not be read by the vision provider.",
            502,
        ) from exc
    columns = value.get("columns")
    rows = value.get("rows")
    if (
        not isinstance(columns, list)
        or not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, list) for row in rows)
        or any(not isinstance(column, (str, int, float)) for column in columns)
        or not any(
            any(str(cell).strip() for cell in row)
            for row in rows
        )
    ):
        raise ApiError(
            "table_not_readable",
            "The vision provider did not return a readable table.",
            422,
        )
    has_header = any(
        isinstance(column, str) and column.strip() for column in columns
    )
    unreadable: set[tuple[int, int]] = set()
    raw_unreadable = value.get("unreadable_cells") or []
    if isinstance(raw_unreadable, list):
        for item in raw_unreadable:
            if (
                isinstance(item, list)
                and len(item) == 2
                and all(isinstance(part, int) and not isinstance(part, bool) for part in item)
            ):
                unreadable.add((item[0], item[1]))
    width = max(
        [len(columns)] + [len(row) for row in rows]
    )
    normalized: list[list[str]] = []
    for row in rows:
        cells = [str(cell) if cell is not None else "" for cell in row]
        cells = (cells + [""] * width)[:width]
        normalized.append(cells)
    normalized_columns = [
        str(column).strip() or f"Column {index + 1}"
        for index, column in enumerate(columns)
    ]
    normalized_columns += _placeholder_columns(width - len(normalized_columns))
    low = [
        [not cell or (row_index, column_index) in unreadable for column_index, cell in enumerate(row)]
        for row_index, row in enumerate(normalized)
    ]
    confidence = [[0.0 if flagged else 100.0 for flagged in row] for row in low]
    title = value.get("title")
    return _enforce_limits(TableRead(
        title=title.strip() if isinstance(title, str) else "",
        has_header=has_header,
        columns=normalized_columns,
        rows=normalized,
        confidence=confidence,
        low_confidence=low,
        engine="ai",
        warnings=[
            {
                "code": "table_from_image",
                "message": "This table was read from an image by an AI provider, not copied source text. Review every cell.",
            }
        ],
    ), settings)
