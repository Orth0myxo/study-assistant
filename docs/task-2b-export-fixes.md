# Task 2b — export fixes (do this before Task 3)

Evidence: phone test of 2026-11-16 08:24 on `Test 1.pdf` (1.28 MB, 53 pages). Export of one page ("Page 2", a title plus a figure) produced a PDF of about 6 MB. Deployed commit `32c93c4`.

## Problems seen in the exported PDF
1. **File size.** `exporter.py` embeds the cached page PNG from `render_page_image` (RGB, 144 DPI). PNG is a poor format for photographic or illustrated slides, so one page can be several MB. This is the likely cause; it was not measured here (no PyMuPDF locally). Measure it in a test.
2. **Layout.** The page text sits at the top of PDF page 1 and the slide image starts on PDF page 2, leaving page 1 almost empty. The image should follow the text on the same page when it fits (scaled to the remaining height), and move to the next page only when it does not.
3. **Wording.** Header reads "Topics requested: Pages 2" and "matched - source passages only". A single page should read "Page 2"; a range "Pages 2-4". Replace the second line with plain words such as "Original text from the source".
4. **OCR label.** "Text read by OCR; may contain errors" is shown above a title that OCR read correctly. Keep it, but place it after the text as a small note.

## Work, in this order (one piece at a time: implement, test, push)
### 2b-1 Smaller images
Add a separate export renderer in `page_images.py` that makes a JPEG (quality about 70) at about 110 DPI, cached beside the PNG, and use it only in `exporter.py` (PDF and DOCX). Leave the `/pages/{n}/image` endpoint on PNG. Test: a synthetic photographic page exports to a file far smaller than the PNG version, still has the image, and the 30-image / 25 MB limits still apply.
Report the real before/after size for `Test 1.pdf` page 2 in `SESSION.md` only if you can measure it; otherwise say it was not measured.

### 2b-2 Flow layout
Place the image directly after the page text on the same PDF page when it fits, otherwise on the next page. DOCX: same order, width limited to the text column. Test with a short-text page and a long-text page (PDF page count and image position).

### 2b-3 Wording
Fix the header and subtitle strings above. Test the strings for one page, a range, and several topics.

## Rules
- One commit per piece, pushed without force; do not re-author published commits.
- CI (backend-tests) is the check. State what could not be verified.
- Update `SESSION.md` with commit ids.
