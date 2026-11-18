# Task 12: agent exports, output behaviour, tool reliability, timeouts, chat history, OCR speed

Rules as before: one piece at a time (test, fix, retest), no built-in wording or phrase lists, report what was not verified.
Do not push docs-only commits during the owner's test sessions (Render restarts on every push).

## Evidence (owner's phone test and Render log, 2026-11-18)

- 53-page PDF upload now takes 0.96 s (`table_ms=0.0`): Task 11 part A worked.
- Many agent tool calls end with `outcome=invalid_arguments`, mostly `build_export`. `_build_export` raises `ValueError` for
  "result not found" and "no summary is available for that result"; the loop maps every `ValueError` to the same generic
  message ("Tool arguments were invalid..."), so the model cannot tell what to fix and retries blindly.
- `build_export` returned `ocr_incomplete` twice (pages 27, 31, 32, 41, 43, 47 pending) -> no file.
- One export took 107 s (`agent tool=build_export seconds=107.08`); the 3-page export took 23 s. The server finished, the phone
  had already given up ("request timed out"). One PDF the owner received was empty (cause not yet reproduced).
- A request got HTTP 429 with `Limit 1000, Used 368, Requested 793` (the owner was testing fast). The owner will try
  `AGENT_TOOL_MAX_OUTPUT_TOKENS=300` on Render.
- Chat replies show raw markdown asterisks; the same OCR warning is shown several times.
- A summary request returns the summary plus all passages; a page request returns the page plus the remaining passages.
- Chat history disappears when the owner leaves the app (server state is lost on Render sleep/redeploy).

## A. Export control (mobile + backend)

1. Exports must not go through the model. Add a backend endpoint that builds a PDF or DOCX from a stored `result_id`
   directly (reuse `_build_export` logic), owner-scoped, and have the app call it.
2. UI: one collapsed "Export" control per result, using the same collapsed/expand pattern the app already uses for the
   alignment options. Collapsed it is one small line; tapping it reveals PDF and DOCX choices. Never print "Export PDF /
   Export DOCX" under every result.
3. Long builds: make export a short job (start, poll, download) so a slow build never depends on one long HTTP request.
   Show progress state in the app's own wording.
4. If OCR is incomplete, say which pages are pending and offer "export what is ready" (clearly marked in the file) or
   "finish OCR first". Never return an empty file silently.
5. Reproduce the empty PDF (use a result with pending/empty pages and one with a summary-only result) and fix with a test.
   An export with no content must fail with a clear status, not produce a blank file.

## B. Output follows the request

- A summary request shows the summary only. Passages appear only when the user asks for them (or both).
- A page/range request shows those pages only, not the remaining passages.
- Find where it happens: the model setting `include_source`, or the app presenting every stored passage of a result.
  Fix at the source and add a test per case.
- Render markdown in chat bubbles (bold, lists, line breaks) with a safe renderer; no raw `**`. Do not rely on telling the
  model to avoid markdown.
- Show each warning once (deduplicate by message and pages), and group OCR notes into one short block.

## C. Tool reliability

- Return specific tool errors to the model: which field failed validation (field names only) and the real reason class
  ("result not found", "no summary for this result", "pages pending OCR"). No document text, no exception strings.
- Default `result_id` (and `document_id`) from conversation state (`last_result_id`, `last_document_id`) when omitted.
- Keep `invalid_arguments` only for real schema errors. Log the failing field name.
- Add behaviour scenarios for a user asking for an export after a passages result and after a summary result.

## D. Timeouts that agree with each other

- Document the server budget (`AGENT_REQUEST_TIMEOUT_S`, loop timeout, per-tool budgets) and the phone's request timeout in
  one place. The phone timeout must be at least the server budget plus a margin, or the long work must be a job (A3).
- When the server hits its budget, return a distinct status the app shows in its own words, never a bare network timeout.
- If the phone does time out on a job that is still running, the app must be able to pick the result up later by polling.

## E. Chat history on the device

- Keep the visible chat history on the phone (not on the server), so it survives Render sleep and redeploys. This is also
  better for privacy. Honour the existing delete controls.
- When a stored `result_id`, document or file has expired on the server, the app shows an "expired" state for that item and the
  agent must handle "not found" gracefully (see C). Never crash or show an empty result.

## F. OCR speed experiments (measure first, change only if it wins)

The owner asked for faster scanning, independent of the agent. Current: Tesseract, 200 dpi, 2.3-14.5 s per page on the free
tier, `OcrTimeout` on dense pages, 5 pages / 20 s caps per request.
- Measure per-page OCR time on a fixed set of scanned pages before changes, and report it.
- Try, one at a time: lower render DPI (150, 120), downscale/crop to the text area, Tesseract flags (`--oem 1`, page
  segmentation mode), `OMP_THREAD_LIMIT=1`, skipping near-blank pages by pixel statistics. Keep accuracy: compare characters
  read per page against the 200 dpi result.
- Evaluate RapidOCR (ONNX) only as an experiment. One public single-image comparison found it not faster and less accurate than
  Tesseract on clean English print, with about 300 MB memory against about 100 MB (Render free is 512 MB), so only adopt it if
  your measurements on the owner's own scans clearly beat Tesseract in time and accuracy and memory stays safe.
- Optional (Task 3e): start OCR for likely-needed pages in the background after upload so the first request does not wait.
- Report: table of time and characters before/after for each experiment.

## G. Small items

- Make the summary provider order a setting (e.g. `SUMMARY_PROVIDER_ORDER`), default Gemini then Groq, so moving summaries to
  the agent model later (paid tier) needs only an environment change.
- Document in `.env.example`: `AGENT_MAX_OUTPUT_TOKENS`, `AGENT_TOOL_MAX_OUTPUT_TOKENS`, `AGENT_REASONING_EFFORT` and what each does.

## APK

Parts A (UI), B (markdown, warnings), D (phone timeout) and E (history) change the mobile app, so one new APK is needed after
they are done. Batch them into a single build. Backend-only parts (A endpoint, C, F, G) deploy with the backend.

## Report

Commit hashes, CI results, measured numbers (export time, OCR time per page, upload time), what was not verified on a device.
