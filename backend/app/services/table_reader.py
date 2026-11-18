"""Read a table layout from Tesseract word boxes or an optional vision model.

The local path uses the geometry of recognized words only. It does not assume
fixed column names, row counts, or wording. Every returned cell is editable by
the user before export.
"""

from __future__ import annotations

import csv
import io
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
    warnings: list[dict[str, str]] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    def as_payload(self) -> dict[str, Any]:
        return {
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


def build_table(words: list[OcrWord]) -> TableRead:
    """Group word boxes into rows and columns from their positions."""
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
    rows = _row_groups(words, row_tolerance)
    regions = _column_regions(words, column_gap)
    if not regions:
        raise ApiError("table_not_readable", "No table columns were recognized.", 422)

    matrix: list[list[list[OcrWord]]] = [
        [[] for _ in regions] for _ in rows
    ]
    for row_index, row_words in enumerate(rows):
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

    header = cell_text[0] if cell_text else []
    columns = [
        (header[index].strip() if index < len(header) and header[index].strip() else f"Column {index + 1}")
        for index in range(len(regions))
    ]
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
        columns=columns,
        rows=data_rows,
        confidence=data_confidence,
        low_confidence=data_low,
        engine="local",
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
        or not columns
        or not isinstance(rows, list)
        or not rows
        or any(not isinstance(row, list) for row in rows)
        or any(not isinstance(column, (str, int, float)) for column in columns)
    ):
        raise ApiError(
            "table_not_readable",
            "The vision provider did not return a readable table.",
            422,
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
    width = len(columns)
    normalized: list[list[str]] = []
    for row in rows:
        cells = [str(cell) if cell is not None else "" for cell in row]
        cells = (cells + [""] * width)[:width]
        normalized.append(cells)
    low = [
        [not cell or (row_index, column_index) in unreadable for column_index, cell in enumerate(row)]
        for row_index, row in enumerate(normalized)
    ]
    confidence = [[0.0 if flagged else 100.0 for flagged in row] for row in low]
    return _enforce_limits(TableRead(
        columns=[str(column) for column in columns],
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
