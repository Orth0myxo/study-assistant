#!/usr/bin/env python3
"""Verify that deterministic range extraction preserves stored source text.

Run from the backend directory:
    python scripts/verify_verbatim.py path/to/lecture.pdf
    python scripts/verify_verbatim.py path/to/lecture.docx

The script exercises the same parser and range-selection service used by the
API. It samples readable PDF pages or stored DOCX paragraphs and checks both
their anchors and exact raw text. No AI provider or API key is used.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings
from app.services.document_parser import ParsedDocument, parse_document
from app.services.range_extraction import extract_range


def _sample_numbers(values: list[int]) -> list[int]:
    if not values:
        return []
    positions = {0, len(values) // 2, len(values) - 1}
    return [values[index] for index in sorted(positions)]


def _anchor_number(section: Any, format_name: str) -> int | None:
    anchor = section.anchor
    key = "page" if format_name == "pdf" else "paragraph_index"
    value = anchor.get(key)
    return int(value) if value is not None else None


def verify(path: Path) -> tuple[int, list[dict[str, Any]]]:
    data = path.read_bytes()
    parsed: ParsedDocument = parse_document(path.name, data, Settings())
    anchor_values = sorted(
        {
            number
            for section in parsed.sections
            if (number := _anchor_number(section, parsed.format)) is not None
        }
    )
    sampled = _sample_numbers(anchor_values)
    if not sampled:
        raise RuntimeError("The parser returned no readable anchored sections to verify.")

    selector = ", ".join(map(str, sampled))
    selected = extract_range(
        document_id="local-verification",
        format=parsed.format,
        page_count=parsed.page_count,
        sections=[
            {
                "index": section.index,
                "anchor": section.anchor,
                "raw_text": section.raw_text,
                "normalized_text": section.normalized_text,
            }
            for section in parsed.sections
        ],
        selector=selector,
    )

    expected = [
        section
        for section in parsed.sections
        if _anchor_number(section, parsed.format) in sampled
    ]
    if len(selected.sections) != len(expected):
        raise AssertionError(
            f"Selected {len(selected.sections)} sections; expected {len(expected)}."
        )
    for index, (actual, source) in enumerate(zip(selected.sections, expected), start=1):
        if actual.get("anchor") != source.anchor:
            raise AssertionError(f"Sample {index} anchor changed during range extraction.")
        actual_text = str(actual.get("raw_text") or "").encode("utf-8")
        source_text = source.raw_text.encode("utf-8")
        if actual_text != source_text:
            raise AssertionError(f"Sample {index} raw text differs from parser source text.")
    return len(selected.sections), selected.warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="PDF or DOCX file to inspect")
    args = parser.parse_args()
    path = args.file.expanduser().resolve()
    if not path.is_file():
        parser.error(f"file not found: {path}")
    try:
        count, warnings = verify(path)
    except Exception as exc:
        print(f"Verbatim verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"Verified {count} sampled {path.suffix[1:].upper()} section(s) byte-for-byte.")
    for warning in warnings:
        print(f"Warning [{warning['code']}]: {warning['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
