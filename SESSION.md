# Development session handoff

Updated: 2026-11-15

## Current task and Git state

- Branch: `feature/verbatim-groq-pipeline`, tracking `origin/feature/verbatim-groq-pipeline`.
- Task 1 (lazy PDF OCR) is being implemented on top of fetched and fast-forwarded commit `9d0044d`.
- The requested findings were reviewed, including pilot pieces A and F. The real `Test 1.pdf` is not present in this checkout.
- Lecturer outline interpretation and document editing are already part of the branch; this task makes no changes to those features or to mobile code.

## Task 1 implementation

- PDF ingest extracts native text and stores per-page native character count, image coverage, `needs_ocr`, `ocr_status`, and `has_page_image`. It does not run OCR. Piece A's candidate rule is retained: under the native-text minimum, or 15–119 compact characters with at least 20% image coverage.
- OCR runs only for pending pages selected by topic matches, page/range extraction, whole-document study, or export. Results are persisted in the page record and `Section.raw_text`; subsequent requests reuse them. Each request has both a page cap and time budget. Warnings list pending pages, and failed pages retain native text with `ocr_status=failed`.
- Added authenticated `GET /api/v1/documents/{id}/pages/{n}/image`, cached under document retention, with pixel/byte bounds. Topic passages expose `has_page_image`.
- `OCR_DPI` remains 200. The API docs recommend trying 150 on Render.
- The backend suite includes a synthetic 53-page upload timing check that requires under 10 seconds and prints `Server-Timing`; it is not the owner's actual `Test 1.pdf` measurement.

## Verification

- `git diff --check`: passed.
- `python3 -m compileall -q backend/app backend/tests`: passed.
- Local full pytest suite: not run. This Termux Python has no `pytest` or PyMuPDF module. PyMuPDF compilation is intentionally not attempted.
- GitHub Actions backend suite: pending the task push. Do not claim this implementation has passed until that run succeeds.
- Upload duration for the actual 53-page `Test 1.pdf`: not measured because the PDF is absent. The synthetic 53-page timing will be recorded from GitHub Actions output.

## Known limits and next step

- First-time OCR still costs CPU for the selected pages. Default cap is 5 pages and 20 seconds per request; pages not completed stay pending for a later request.
- Verify the synthetic 53-page timing and complete backend suite from GitHub Actions, then record the run result and measured timing here. After deployment, the owner should re-upload `Test 1.pdf`, request `cytology`, and confirm body text, repeat-request caching, and Render page-level OCR logs.
