# Task 4d — summary-only export layout and number of summaries

Source: owner's test after build 11 (`42c70e5`), 2026-11-17 08:50. Read `SESSION.md` and `docs/task-4c-export-layout-and-app-fixes.md` first. One piece at a time: implement, test, push, then the next. One commit per piece, pushed without force; do not re-author published commits. CI is the check; say what could not be verified.
Task 4 is not complete until the owner's `HERPES VIRIDAE FAMILY.docx` summary export looks as specified below on the phone.

## What the owner saw
1. More summaries than expected; he suspects each part (page/chunk) is summarised.
2. The export still prints the paragraph/page reference as a bold heading for each part.
3. He wants the references on one line below the summaries (or text), and references that are not needed removed entirely.

## Code facts (reviewer's reading, not yet confirmed on a device)
- `exporter.py::organize_study_job_payload` turns every study item into a "topic" whose heading is the reference label (`_page_range_label` / `_payload_refs_label`, else "Part N"). In a summary-only export each part therefore gets a bold heading, an "AI summary, not source text" heading, the summary, and a "Source:" line.
- Whole-material summary-only makes one `section` AI call per part of at most `MAX_SUMMARY_CHUNK_CHARS` (4,000 chars) plus one `overview` call when the text is longer than `SUMMARY_SINGLE_CALL_CHARS` (12,000). A DOCX of about 20,000 characters gives about 5 parts + 1 overview = 6 calls and 6 printed summaries. This matches the owner's suspicion. Confirm with the Render log line `study done mode=summaries_only ai_calls=N` (ask the owner for it; he can search Logs for `study done`).

## 4d-1 Export layout for summary-only (backend, no APK)
For `mode=summaries_only` (study jobs and topic results alike):
- Header: title, date, source file name, in small grey lines. Remove "Topics requested: …" and the long reference lists from the header. Keep "Summary coverage" only when some parts failed.
- Section "Summary" (centered heading): the overall summary when there is one; if there is no overall summary, the part summaries in order as plain paragraphs without per-part headings.
- Per-part summaries are not printed when an overall summary exists (they are intermediate steps; the app already hides them under Details). Add an optional export query `include_parts=true` (default false) that prints them, each as a plain paragraph.
- One small grey note, once, near the top: "AI-written summary, not source text". No repeated "AI summary, not source text" headings.
- References: a single small grey line below the summary: "References: Whole document" / "Pages 2-5" / "Paragraphs 1-71" (compact labels from Task 4c-1). Never a heading, never bold, wrapped within the margins. Omit it when the labels carry no information (a one-part whole-document result may say "Whole document" or be omitted; choose and document).
- Source-text exports keep page headings (`Page 2`) because they separate pages; the topic heading is the user's topic, not a reference list. In all modes a reference list is never used as a heading.
- Do the same in the DOCX and the PDF writer. Keep Task 4c sizes and alignment.
Tests (PDF spans and DOCX XML): a 71-paragraph, 6-part summary-only payload exports one summary section, no bold reference heading, one references line under the summary, no "Topics requested" line, no "AI summary" heading repeats, all text inside the page width; `include_parts=true` prints the parts; source-text modes unchanged.

## 4d-2 Fewer AI calls for summary-only (backend, small)
- Raise `SUMMARY_SINGLE_CALL_CHARS` so ordinary lecture documents use one call, for example 40,000 characters (about 10,000 tokens), and document that Groq free-tier limits may reject a prompt that large; the provider chain then falls back to Gemini. Verify the fallback with a fake that rejects long prompts on the first provider.
- Keep the per-part path only above that limit.
- Keep the `study done ... ai_calls=N` line and the `estimated_calls` shown before a whole-material job; update the estimate to the new rule.
- Tests: a 30,000-character document makes one call; a 100,000-character document makes one call per part plus one overview call; `estimated_calls` matches the real count.

## 4d-3 Owner check (not code)
After deployment the owner re-exports the Herpes document as PDF and DOCX and checks: one summary, no bold reference heading, one small references line, readable size, nothing cut off at the page edge. Record the result in `SESSION.md` only after he confirms.
