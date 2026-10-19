from __future__ import annotations

import io

import pymupdf
from docx import Document as DocxDocument


def build_pdf(pages: list[str]) -> bytes:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def build_docx(blocks: list[tuple[str, object]]) -> bytes:
    document = DocxDocument()
    for kind, value in blocks:
        if kind == "heading":
            document.add_heading(str(value), level=1)
        elif kind == "table":
            rows = value  # type: ignore[assignment]
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            for row_index, row in enumerate(rows):
                for col_index, cell in enumerate(row):
                    table.cell(row_index, col_index).text = cell
        else:
            document.add_paragraph(str(value))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_png(size: int = 64) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=size, height=size)
    page.draw_rect(
        pymupdf.Rect(2, 2, size / 2, size / 2),
        color=(0, 0, 0),
        fill=(0, 0, 0),
    )
    data = page.get_pixmap(
        dpi=72,
        colorspace=pymupdf.csGRAY,
        alpha=False,
    ).tobytes("png")
    document.close()
    return data


def build_docx_with_images(
    heading: str | None,
    text: str,
    images: list[bytes],
) -> bytes:
    document = DocxDocument()
    if heading:
        document.add_heading(heading, level=1)
    if text:
        document.add_paragraph(text)
    for image in images:
        document.add_picture(io.BytesIO(image))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
