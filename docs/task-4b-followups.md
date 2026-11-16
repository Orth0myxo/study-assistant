# Task 4b — follow-ups from the owner's test of 2026-11-16 12:50

Run after Task 4 and before or alongside Task 5 (the agent). One piece at a time: implement, test, push.

## 4b-1 `%20` still shows in file names
Task 4 decoded names at some display sites, but `mobile/app/workspace.tsx` still prints `doc.filename` raw (for example the lines that build "Matching OCR text to …", "I found … in …", "Searching for … in …", the pending-job detail strings, and `source.filename` in the material chips). Decode once where the document enters app state (the API client response mapping, using the existing `displayName` helper inside try/catch) so every screen and every exported file name gets the readable name. Remove the per-site decoding that becomes redundant. Test: a name containing `%20` and a malformed `%` sequence.

## 4b-2 Summary-only costs too many AI calls
What the code does today:
- Whole-material summary-only (`process_document`, mode `summaries_only`): one `section` summary call per unit of at most `MAX_SUMMARY_CHUNK_CHARS` (4,000 characters), plus one `overview` call. The card shows each part's summary and the overall one. `Test 1.pdf` has about 15,800 native characters, so roughly 5 to 7 calls, more after OCR text is added.
- Topic summary: one call per matched topic, plus a second `topic_beyond` call per topic only when `ai_scope=source_and_beyond`.
- The owner's request "Summarise vaginal cytology only" must use the topic path. If the app starts a whole-material job for a topic-scoped request, that is a routing bug.

Required:
1. Verify first. Add one log line per finished study job and per topic study output: `study done mode=… ai_calls=N cache_hits=M seconds=S` (counts only). Ask the owner to repeat the request and read it in Render Logs.
2. Route "summarise <topic> only" to the topic summary in the mobile app (interim, until the agent route). Whole-material summary only for explicit whole-document requests.
3. Whole-material summary-only shows one overall summary by default. Per-part summaries go under a collapsed "Details" control. Add `SUMMARY_SINGLE_CALL_CHARS` (default 12000): at or below it, make one call and skip the overview call. Above it, keep the current packing.
4. Before a whole-material job starts, the app shows "This will use about N AI requests" (N from the unit count; the API can return `estimated_calls` on job creation). It helps users on their own key.
5. Keep the existing word caps. Tests: call count for a small and a large document with a counting fake client, and the log line.

## 4b-3 Text alignment in DOCX and PDF exports
Add an export option `text_align` with values `left` (default), `center`, `right`, `justify`, plus a separate `heading_align` (`left` default, `center`). Applies to PDF and DOCX for extraction, study-job and topic-result exports; add it to the three export endpoints and `docs/api-contract.md`.
- DOCX: set `paragraph.alignment` on body and heading paragraphs.
- PDF: the custom writer in `exporter.py` must pass the matching alignment to its text placement (PyMuPDF text boxes take an `align` argument) and keep the page-flow behaviour from Task 2b.
- Mobile (Task 6 or a small Task 4 follow-up): an "Alignment" choice in the export sheet.
Tests: each value produces the expected alignment in the DOCX XML, and in the PDF the first text line starts at the expected x position for left, center and right.

## 4b-4 Follow-up requests lose the previous result (interim fix in the current chat)
Reproduced twice. 1) `Test 1.pdf`: summary of "vaginal cytology", then "Ok give me the original passages instead" → "Could you name a topic, page range, or study action?". 2) `UPDATED CHS 2nd….pdf` (6 pages): a whole-material summary of exam rules, then "Ok give me the original passages" → the same question. So this is general, not topic-specific.
Interim rule until the agent route replaces this code (Task 6): keep the last result in chat state (kind, document id, topic or page scope). A message that asks for the original / verbatim / passages / exact text, or for a summary, export or shorter version, with no new topic or range named, applies to that last result's scope: original passages → `extracted_text_only` for the same document and scope; "summarise it" → summary for the same scope. Only ask a question when there is no previous result. Put this in `chatOrchestrator.ts` as a pure function with unit tests for the phrases above. Do not add more phrase lists beyond these follow-up forms; the agent plan (section 5.1) is the real fix.
