# Implementation Plan

Version 1.0 · Status: **Stage 4 implemented — awaiting review**

Each stage has a **completion gate**. A stage must not begin until the previous gate passes.
This mirrors the specification's staged plan.

## Stage 0 — Architecture documentation (current)

**Deliverables**

- `docs/architecture.md`
- `docs/api-contract.md`
- `docs/data-model.md`
- `docs/implementation-plan.md` (this file)
- `README.md`, `.gitignore`, `.env.example`

**Gate** — stop and wait for explicit approval. No application code. ✅ (pending review)

## Stage 1 — Project foundation

**Work**

- Initialize `mobile/` with Expo + TypeScript + Expo Router (blank template).
- Add base navigation: Home → Upload → Topics → Results (placeholder screens).
- Initialize `backend/` with FastAPI, `config.py` (pydantic-settings), `GET /health`.
- Wire the mobile `ApiClient` to call `/health`; show backend/AI status on Home.
- Add run scripts and a local dev setup section in `README.md`.

**Gate**

- App starts on an Android emulator/device.
- `/health` returns `200` and the app displays the status.
- `pytest` runs (even if only a health test exists).

**Status — implemented, pending approval.** Delivered: Expo SDK 57 app with Expo Router
Stack navigation and four screens (Home, Upload, Topics, Results); typed `apiClient`
calling `/health` with backend-offline handling; FastAPI app with `GET /health`,
pydantic-settings `config.py`, and a small pytest suite. Verified locally: `pytest`
(2 passed), `uvicorn` serving `GET /health` → `200`, `tsc --noEmit` clean, `eslint`
clean, and a Metro Android export succeeded. Not yet run on a physical device/emulator
(bundler verified via export only).

## Stage 2 — Document processing

**Work**

- `POST /api/v1/documents` upload endpoint + validation (type, size, magic bytes).
- `document_parser.py`: PyMuPDF per-page extraction; python-docx paragraphs/tables/headings.
- Detect scanned/empty PDFs (`extraction_status: no_text`) and corrupted/encrypted files.
- Bounded local Tesseract OCR for low-text PDF pages and images embedded in DOCX, with
  clear warnings when OCR is unavailable, times out, or hits the page limit.
- Return `document_id` + extraction metadata; implement `DELETE /documents/{id}` and TTL sweep.
- Mobile Upload screen: picker, filename/size display, upload, status.

**Gate**

- Fixture PDFs and DOCX files produce verifiable, page/section-anchored extracted text.
- Scanned/corrupt/oversized fixtures yield the specified errors.
- OCR tests use an injected runner and pass without Tesseract installed.
- Unit tests for the parser pass.

**Status — implemented, pending approval.** Delivered: `services/document_parser.py`
(PyMuPDF per-page PDF extraction with one-based pages; python-docx paragraphs/tables with
section/paragraph anchors), `services/storage.py` (temp files + TTL sweep + delete),
`api/documents.py` (`POST`/`DELETE` `/api/v1/documents`), shared error envelope
(`errors.py`), and a minimal mobile Upload screen (`expo-document-picker` + extraction
summary). Batch B adds `services/ocr.py` (bounded Tesseract CLI runner) and OCR for
low-text PDF pages and DOCX images. Verified locally: **42 backend tests pass** (extraction,
anchors, invalid/empty/corrupt files, size limit, delete, expiry sweep, OCR success/limit/
timeout/unavailable/failure with an injected runner), live `uvicorn` smoke test (201 upload,
204 delete, 415 error envelope), `tsc` clean, `eslint` clean, Android export succeeds.
Uploads have been exercised on a physical device; OCR still needs a physical-device pass.

## Stage 3 — Core study processing (three modes + Gemini)

**Work**

- `study_processor.py`: group stored sections into bounded units
  (`MAX_SUMMARY_CHUNK_CHARS`), preserving source anchors; return source text and/or
  grounded summaries. Topic matching is **out of scope**.
- `extracted_text_only` from stored raw text (no AI call at all).
- `ai_client.py`: Google Gemini Developer API (`generateContent`) with timeouts,
  retry/backoff, token caps, and typed errors.
- `summaries_only` / `text_and_summaries`: map (per unit) + optional reduce
  (`document_summary`); no fabricated content; per-unit failures degrade to `partial`.
- `job_store.py`: in-memory job registry (non-persistent).
- `POST /study-jobs` + `GET /study-jobs/{id}`; synchronous for small jobs, polled for
  large jobs (`SYNC_PROCESSING_MAX_SECTIONS`).

**Gate**

- Automated tests verify all three modes, mode-1 never calls AI, source references
  survive chunking, and summaries are grounded (stubbed AI client — no network/key).
- AI unavailable (503), 429, timeout, provider error, and malformed output paths return
  the specified codes; the job is never silently "successful" with empty summaries.

**Status — implemented, pending approval.** Delivered: `services/ai_client.py`
(`GeminiClient` + typed errors), `services/study_processor.py`, `services/job_store.py`,
`schemas/study.py`, `api/study.py`, Gemini settings in `config.py`, and a minimal mobile
mode selector + results screen. Verified locally: **69 backend tests pass**, `tsc` clean,
`eslint` clean, Android export succeeds. Live Gemini smoke tests pass with the user's key
(all three modes).

## Stage 4 — Persistence, study history, and export

**Work**

- SQLite persistence (stdlib `sqlite3`) for document metadata/sections and study-job
  state/results; stores hydrate on startup and write through on update.
- Startup reconciliation: orphan source files removed; missing files mark the document
  unavailable (blocking new jobs) while keeping completed results.
- `GET /api/v1/study-jobs` recent list (newest first, `limit`/`offset`).
- `exporter.py`: PDF (PyMuPDF) + DOCX (python-docx), generated from the persisted job
  payload and mode-aware (source-only / text+summaries / summaries-only).
- `GET /study-jobs/{id}/export?format=pdf|docx` download with clear `404`/`409`.
- Retention: `RETENTION_MINUTES` (default 7 days); document delete keeps job results.
- Mobile: recent-session list on Home, reopen results by `job_id`, PDF/DOCX export via
  `expo-file-system` + `expo-sharing`.

**Gate**

- Full flow succeeds end-to-end on an Android device/emulator:
  upload → mode → results → export PDF and DOCX → reopen a saved session.

**Status — implemented, pending approval.** Delivered: `services/db.py` (SQLite +
`user_version` migrations), persistence in `services/storage.py` and
`services/job_store.py` (write-through, hydrate, reconcile), `services/exporter.py`,
`GET /api/v1/study-jobs` (list) and `GET /api/v1/study-jobs/{id}/export`, config
`RETENTION_MINUTES`/`DATABASE_PATH`, mobile history + reopen + export/share. Verified
locally: **85 backend tests pass** (persistence across reload, retention expiry, orphan
reconciliation, deletion preserving results, history ordering, and PDF/DOCX exports for
all three modes re-read with PyMuPDF/python-docx), `tsc` clean, `eslint` clean, Android
export succeeds, and a live smoke test on a throwaway instance confirmed persistence
across a real restart plus export-after-delete. Physical-phone export/share pending.

## Stage 5 — Real-world testing

**Work**

- Test representative lecture PDFs/DOCX, scanned documents, long documents, tables/lists.
- Test network loss, AI timeouts/rate limits, exhausted free-tier quota, export failures.
- Measure AI usage/cost per job and tune chunk/token limits.
- Fix critical failures before sharing with other students.

**Gate**

- No critical failures; documented residual limitations.
- Cost/time per typical job is acceptable.

## Dependencies

- Stage 1: env setup (Node/Expo, Python venv).
- Stage 2: PyMuPDF, python-docx, Tesseract CLI (optional at runtime).
- Stage 3: `httpx`, a Google Gemini API key (optional at runtime), `pytest`.
- Stage 4: stdlib `sqlite3` (backend), PyMuPDF/python-docx (existing) for exports,
  Expo FileSystem/Sharing (mobile). No new backend packages.
- Stage 5: real sample materials.

## Assumptions

1. Backend runs on a small single-instance host; in-process job state is acceptable.
2. AI summaries use the Google Gemini Developer API (`generateContent`).
3. Users have internet for upload/processing; offline is read-only for saved notes.
4. PDFs may be text-based or scanned; scanned pages and DOCX images use bounded local
   Tesseract OCR when available, and OCR text is eligible for summarization.
5. DOCX page numbers are approximate; paragraph/section anchors are acceptable.
6. Dev/test uses English lecture material (UI copy is English).

## Resolved decisions (approved at Stage 0 → Stage 1 gate)

1. **AI provider** — **Google Gemini Developer API**. `GEMINI_API_KEY` and `GEMINI_MODEL`
   (`gemini-3.8-flash` default) are supplied via environment variables. Secrets remain
   server-side only; never bundled in the mobile app, logs, or responses.
2. **Job model** — **synchronous for small jobs, polling for large jobs**, with a
   configurable threshold (`SYNC_PROCESSING_MAX_SECTIONS`, default 20 sections).
   Jobs at or below the threshold return results inline; larger jobs return a `job_id`
   for status polling.
3. **Mobile app identity** — display name **Study Assistant**, Android package ID
   **`com.aidi.studyassistant`**.
4. **Exports** — generate PDF/DOCX on the backend and deliver to the device, where the
   app uses the **Android share sheet** so users can save or share the file.
5. **Source locations** — **exact PDF page references** (1-based, from the source index);
   DOCX uses **section/paragraph anchors** instead of misleading page numbers.
6. **No topic matching** — study jobs are per-document and mode-driven only.

## Remaining open questions (non-blocking)

- Gemini model choice is deployment-time configuration; a specific model is not
  required to develop or test (tests use a stubbed AI client).
- The Gemini free tier has quota limits and its data-use terms differ from the paid tier;
  review before uploading sensitive material.

## Risks and mitigations

| Risk                                   | Mitigation                                                    |
| -------------------------------------- | ------------------------------------------------------------- |
| Scanned PDFs / image-only DOCX         | Bounded local OCR; clear warnings when unavailable/limited    |
| DOCX page numbers unreliable           | Use section/paragraph anchors; label as approximate           |
| Long docs exceed model context         | Chunk to `MAX_SUMMARY_CHUNK_CHARS`; bounded tokens; reduce step |
| AI hallucinations / invented facts     | Grounding prompt + validation + source refs on every unit     |
| AI cost/rate limits                    | Token caps + chunk caps + retries with backoff + clear errors |
| Provider outages/rate limits           | Timeouts, backoff, typed error codes, `partial`/`failed` status |
| OCR text quality                       | OCR text is clearly labelled as source extracted from a scan  |
| Temp file leakage                      | TTL sweep + DELETE endpoint + no web-served temp path         |
| OneDrive/AV interference during dev    | Project kept outside OneDrive (per user choice)               |

## Acceptance criteria summary

| Stage | Key acceptance criterion                                             |
| ----- | -------------------------------------------------------------------- |
| 0     | Docs complete, consistent; no code; approval requested                |
| 1     | App runs; `/health` OK; navigation works                             |
| 2     | Fixtures produce verifiable, anchored text; errors handled           |
| 3     | Three modes work; refs + grounding + AI failure paths verified by tests |
| 4     | Full flow + exports work on Android; offline read works              |
| 5     | Real materials pass; critical failures fixed                         |
