"""Deterministic heading sections built from stored page structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.topic_matching import match_topics


@dataclass
class HeadingSection:
    section_id: str
    index: int
    text: str
    level: int
    page: int | None
    block: int | None
    path: list[str]
    passages: list[dict[str, Any]] = field(default_factory=list)
    parent_id: str | None = None
    match_text: str = ""

    @property
    def raw_text(self) -> str:
        return "\n".join(
            str(passage.get("text") or "") for passage in self.passages
        )


@dataclass
class HeadingIndex:
    source: str
    sections: list[HeadingSection]


def _page_number(page: dict[str, Any]) -> int | None:
    number = page.get("page_number")
    if isinstance(number, int):
        return number
    index = page.get("index")
    return int(index) + 1 if isinstance(index, int) else None


def _pdf_marks(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    marks: list[dict[str, Any]] = []
    for position, page in enumerate(pages):
        text = str(page.get("text") or "")
        if not text.strip():
            continue
        page_number = _page_number(page)
        seen_lines: set[int] = set()
        for block in page.get("structure") or []:
            if not isinstance(block, dict) or block.get("role") not in {
                "heading",
                "title",
            }:
                continue
            start = block.get("start")
            end = block.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            line_start = text.rfind("\n", 0, start) + 1
            line_end = text.find("\n", end)
            if line_end < 0:
                line_end = len(text)
            if line_start in seen_lines:
                continue
            heading_text = text[line_start:line_end].strip()
            if not heading_text:
                continue
            seen_lines.add(line_start)
            level = block.get("level")
            if not isinstance(level, int) or level < 1:
                level = 1 if block.get("role") == "title" else 2
            marks.append(
                {
                    "position": position,
                    "page": page_number,
                    "start": line_start,
                    "end": line_end,
                    "text": heading_text,
                    "level": level,
                }
            )
    marks.sort(key=lambda item: (item["position"], item["start"]))
    return marks


def _pdf_passages(
    pages: list[dict[str, Any]], mark: dict[str, Any], end_mark: dict[str, Any] | None
) -> list[dict[str, Any]]:
    end_position = end_mark["position"] if end_mark else len(pages) - 1
    end_offset = end_mark["start"] if end_mark else None
    passages: list[dict[str, Any]] = []
    for position in range(mark["position"], end_position + 1):
        page = pages[position]
        text = str(page.get("text") or "")
        start = mark["start"] if position == mark["position"] else 0
        stop = (
            end_offset
            if position == end_position and end_offset is not None
            else len(text)
        )
        piece = text[start:stop]
        if not piece.strip():
            continue
        page_number = _page_number(page)
        passages.append(
            {
                "page": page_number,
                "anchor": {
                    "kind": "pdf_page",
                    "page": page_number,
                    "page_index": page.get("index"),
                },
                "text": piece,
                "has_page_image": bool(page.get("has_page_image")),
            }
        )
    return passages


def _pdf_sections(
    document_id: str, pages: list[dict[str, Any]], marks: list[dict[str, Any]]
) -> list[HeadingSection]:
    stack: list[tuple[int, str]] = []
    sections: list[HeadingSection] = []
    for index, mark in enumerate(marks):
        section_id = f"{document_id}:h:{index}"
        while stack and stack[-1][0] >= mark["level"]:
            stack.pop()
        path = [text for _, text in stack] + [mark["text"]]
        parent_id = stack[-1][1] if stack else None
        stack.append((mark["level"], section_id))
        next_mark = next(
            (
                candidate
                for candidate in marks[index + 1 :]
                if candidate["level"] <= mark["level"]
            ),
            None,
        )
        next_any = marks[index + 1] if index + 1 < len(marks) else None
        passages = _pdf_passages(pages, mark, next_mark)
        match_passages = _pdf_passages(pages, mark, next_any)
        sections.append(
            HeadingSection(
                section_id=section_id,
                index=index,
                text=mark["text"],
                level=mark["level"],
                page=mark["page"],
                block=None,
                path=path,
                passages=passages,
                parent_id=parent_id,
                match_text="\n".join(
                    str(passage.get("text") or "") for passage in match_passages
                ),
            )
        )
    return sections


def _docx_is_heading(section: dict[str, Any]) -> bool:
    anchor = section.get("anchor") or {}
    marker = anchor.get("section_heading")
    if isinstance(marker, bool):
        return marker
    label = str(anchor.get("section_label") or "").strip()
    return bool(label and label == str(section.get("raw_text") or "").strip())


def _docx_sections(
    document_id: str, sections_data: list[dict[str, Any]]
) -> list[HeadingSection]:
    marks: list[dict[str, Any]] = []
    for index, section in enumerate(sections_data):
        if not _docx_is_heading(section):
            continue
        anchor = section.get("anchor") or {}
        level = anchor.get("section_level")
        if not isinstance(level, int) or level < 1:
            level = 1
        marks.append(
            {
                "position": index,
                "text": str(section.get("raw_text") or "").strip(),
                "level": level,
                "block": (
                    int(anchor["paragraph_index"]) + 1
                    if isinstance(anchor.get("paragraph_index"), int)
                    else None
                ),
            }
        )
    stack: list[tuple[int, str]] = []
    sections: list[HeadingSection] = []
    for index, mark in enumerate(marks):
        section_id = f"{document_id}:h:{index}"
        while stack and stack[-1][0] >= mark["level"]:
            stack.pop()
        path = [text for _, text in stack] + [mark["text"]]
        parent_id = stack[-1][1] if stack else None
        stack.append((mark["level"], section_id))
        next_mark = next(
            (
                candidate
                for candidate in marks[index + 1 :]
                if candidate["level"] <= mark["level"]
            ),
            None,
        )
        end = next_mark["position"] if next_mark else len(sections_data)
        pieces = [
            str(item.get("raw_text") or "")
            for item in sections_data[mark["position"] : end]
        ]
        text = "\n".join(pieces)
        if not text.strip():
            continue
        next_any = marks[index + 1] if index + 1 < len(marks) else None
        match_end = next_any["position"] if next_any else len(sections_data)
        match_text = "\n".join(
            str(item.get("raw_text") or "")
            for item in sections_data[mark["position"] : match_end]
        )
        first_anchor = dict(sections_data[mark["position"]].get("anchor") or {})
        first_anchor["section_label"] = mark["text"]
        first_anchor["section_end_index"] = end - 1
        sections.append(
            HeadingSection(
                section_id=section_id,
                index=index,
                text=mark["text"],
                level=mark["level"],
                page=None,
                block=mark["block"],
                path=path,
                passages=[
                    {
                        "page": None,
                        "anchor": first_anchor,
                        "text": text,
                        "has_page_image": any(
                            bool(item.get("has_page_image"))
                            for item in sections_data[mark["position"] : end]
                        ),
                    }
                ],
                parent_id=parent_id,
                match_text=match_text,
            )
        )
    return sections


def build_heading_index(document: Any, pages: list[dict[str, Any]]) -> HeadingIndex:
    if getattr(document, "format", None) == "pdf":
        marks = _pdf_marks(pages)
        if not marks:
            return HeadingIndex("none", [])
        return HeadingIndex("font", _pdf_sections(document.document_id, pages, marks))
    if getattr(document, "format", None) == "docx":
        sections = _docx_sections(
            document.document_id, list(getattr(document, "sections", []) or [])
        )
        return HeadingIndex("styles" if sections else "none", sections)
    return HeadingIndex("none", [])


def match_view(section: HeadingSection) -> dict[str, Any]:
    return {
        "index": section.index,
        "anchor": {
            "kind": "heading_section",
            "section_label": section.text,
            "page": section.page,
        },
        "raw_text": section.match_text or section.raw_text,
        "has_page_image": any(
            bool(passage.get("has_page_image")) for passage in section.passages
        ),
    }


def find_matching_sections(
    document: Any,
    pages: list[dict[str, Any]],
    topics: list[str],
    settings: Any,
) -> tuple[HeadingIndex, list[dict[str, Any]]]:
    index = build_heading_index(document, pages)
    if not index.sections:
        return index, []
    views = [match_view(section) for section in index.sections]
    candidate_settings = settings.model_copy(update={"min_match_score": 0.0})
    results = match_topics(
        topics,
        views,
        document_id=document.document_id,
        settings=candidate_settings,
    )
    grouped: dict[str, dict[str, Any]] = {}
    for result in results:
        for match in result.matches:
            if match.section_index < 0 or match.section_index >= len(index.sections):
                continue
            section = index.sections[match.section_index]
            item = grouped.setdefault(
                section.section_id,
                {
                    "section_id": section.section_id,
                    "text": section.text,
                    "level": section.level,
                    "path": section.path,
                    "page": section.page,
                    "block": section.block,
                    "topics": [],
                    "snippets": [],
                },
            )
            item["topics"].append(result.text)
            item["snippets"].append(
                {
                    "topic": result.text,
                    "text": (
                        "<untrusted source excerpt>"
                        + match.excerpt
                        + "</untrusted source excerpt>"
                    ),
                    "score": match.score,
                }
            )
    order = {section.section_id: position for position, section in enumerate(index.sections)}
    groups = sorted(grouped.values(), key=lambda item: order[item["section_id"]])
    for group in groups:
        group["snippets"] = group["snippets"][:3]
    return index, groups


def selected_sections(
    document: Any,
    pages: list[dict[str, Any]],
    section_ids: list[str],
) -> list[HeadingSection]:
    index = build_heading_index(document, pages)
    known = {section.section_id: section for section in index.sections}
    chosen: list[HeadingSection] = []
    seen: set[str] = set()
    for section_id in section_ids:
        section = known.get(section_id)
        if section is None:
            section = _flat_section(document, pages, section_id)
        if section is None or section.section_id in seen or not section.raw_text.strip():
            continue
        seen.add(section.section_id)
        chosen.append(section)
    chosen_ids = {section.section_id for section in chosen}
    filtered: list[HeadingSection] = []
    for section in chosen:
        parent = section.parent_id
        drop = False
        while parent:
            if parent in chosen_ids:
                drop = True
                break
            parent = known.get(parent).parent_id if parent in known else None
        if not drop:
            filtered.append(section)
    return filtered


def _flat_section(
    document: Any, pages: list[dict[str, Any]], section_id: str
) -> HeadingSection | None:
    prefix = f"{document.document_id}:hit:"
    if not section_id.startswith(prefix):
        return None
    try:
        section_index = int(section_id[len(prefix) :])
    except ValueError:
        return None
    section = next(
        (
            item
            for item in list(getattr(document, "sections", []) or [])
            if int(item.get("index", -1)) == section_index
        ),
        None,
    )
    if section is None:
        return None
    anchor = dict(section.get("anchor") or {})
    page = anchor.get("page")
    if not isinstance(page, int):
        page = None
    block = None
    if isinstance(anchor.get("paragraph_index"), int):
        block = int(anchor["paragraph_index"]) + 1
    passages = [
        {
            "page": page,
            "anchor": anchor,
            "text": str(section.get("raw_text") or ""),
            "has_page_image": bool(section.get("has_page_image")),
        }
    ]
    label = str(anchor.get("section_label") or "").strip()
    return HeadingSection(
        section_id=section_id,
        index=int(section.get("index", 0)),
        text=label or (f"Page {page}" if page else f"Block {block or 1}"),
        level=1,
        page=page,
        block=block,
        path=[label] if label else [],
        passages=passages,
    )
