# Development session handoff

Updated: 2026-11-15

## Current task and Git state

- Branch: `feature/verbatim-groq-pipeline`, tracking `origin/feature/verbatim-groq-pipeline`.
- Base HEAD is `2053d73`; the reliability follow-up is committed as `ae41173` and `ec0e477` and pushed to the tracked branch.
- `origin/main` is `4db2b2e` and is an ancestor; this remains a feature branch, not a merge.
- The real `Test 1.pdf` is not present in this checkout, so no device or production-scale OCR measurement was made.

## Task 1 implementation

- PDF ingest extracts native text and stores per-page native character count, image coverage, `needs_ocr`, `ocr_status`, and `has_page_image`. It does not run OCR. Piece A's candidate rule is retained: under the native-text minimum, or 15–119 compact characters with at least 20% image coverage.
- OCR runs only for pending pages selected by topic matches, page/range extraction, whole-document study, or export. Results are persisted in the page record and `Section.raw_text`; subsequent requests reuse them. Each request has both a page cap and time budget. Warnings list pending pages, and failed pages retain native text with `ocr_status=failed`.
- Added authenticated `GET /api/v1/documents/{id}/pages/{n}/image`, cached under document retention, with pixel/byte bounds. Topic passages expose `has_page_image`.
- `OCR_DPI` remains 200. The API docs recommend trying 150 on Render.
- A synthetic 53-page upload timing check requires under 10 seconds and prints `Server-Timing`; Actions measured 14.7 ms total. This is not the owner's actual `Test 1.pdf` measurement.

## Follow-up reliability changes

- Added owner-scoped `GET/POST /api/v1/documents/{id}/ocr` for resumable OCR progress and bounded batches. Progress is derived from persisted page state; no eager OCR was added.
- Whole-document study, range study, and PDF/DOCX/topic exports now return `409 ocr_incomplete` with pending/failed page details instead of silently using partially OCR'd text.
- Topic review reads pending OCR pages before declaring a document empty or returning no match, including fully scanned and mixed PDFs.
- Document editing now uses the configured Groq/Gemini provider chain with fallback; exact-span and review validation are unchanged.
- Mobile prepares scanned pages in batches before whole-document study, topic matching, and export retries. Export downloads recover from a `409` by preparing OCR and retrying once. Edit previews no longer auto-export; PDF/Word buttons require explicit confirmation.
- Mobile chat routes outline/image requests to the existing photo-notes outline-review flow.
- Mobile `ApiError` now preserves server `details`, so pending/failed OCR pages reach the retry logic.
- Added backend regression tests for OCR progress/resume, partial-study blocking, scanned/full topic recovery, and edit-provider fallback. Added mobile tests for OCR error details and batch-resume behavior.
- Updated `docs/api-contract.md` for the OCR endpoints, `ocr_incomplete`, topic-review OCR fallback, and editing provider/confirmation behavior.

## Verification

- `git diff --check`: passed.
- `python -m compileall -q backend/app backend/tests`: passed after the follow-up changes.
- Local full pytest suite: not run. This Termux Python still has no `pytest`, FastAPI, or PyMuPDF modules; no local dependency installation was attempted.
- GitHub Actions [backend run 43767428151](https://github.com/Orth0myxo/study-assistant/actions/runs/43767428151) on `ec0e477`: passed; 443 tests.
- Mobile `npm run typecheck`: passed.
- Mobile `npm test -- --run`: 65 tests passed.
- Mobile `npm run lint`: 0 errors, 2 existing unused-code warnings.
- The workflow has one existing Actions deprecation annotation for Node.js 20 action runtimes; it is unrelated to the tests.
- Upload duration for the actual 53-page `Test 1.pdf`: not measured because the PDF is absent.

## Known limits and next step

- First-time OCR still costs CPU for selected pages. Default cap is 5 pages and 20 seconds per request; the new progress/batch endpoints make the remaining pages resumable.
- Failed OCR pages block study/export until the user supplies a clearer scan or re-uploads the document; they are not silently treated as empty or retried automatically.
- Backend tests were executed in GitHub Actions, not locally. The editing endpoint tests pin the Gemini test client so a local Groq key cannot make them hit a real provider.
- After deployment, re-upload `Test 1.pdf`, request `cytology`, and confirm complete OCR progress, repeat-request caching, and Render page-level OCR logs.
- Background ingest now logs one line when it finishes: `ingest done document_id=... format=... pages=... kb=... parse_s=... store_s=...` (ids and numbers only). Search Render logs for `ingest done` to see how long the "extracting" step really takes. Test: `test_ingest.py::test_ingest_logs_timing_without_the_file_name`.
- Added `.github/workflows/mobile-tests.yml` (Node 22: `npm ci`, typecheck, lint, tests) so mobile changes are checked in CI, not only locally. First run result is the verification; fix the workflow, not the app, if it fails for environment reasons.
