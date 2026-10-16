# Implementation Plan

Version 1.0 · Proposed · Status: **awaiting approval** — do not start Stage 1 until authorized.

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

## Stage 2 — Document processing

**Work**

- `POST /api/v1/documents` upload endpoint + validation (type, size, magic bytes).
- `document_parser.py`: PyMuPDF per-page extraction; python-docx paragraphs/tables/headings.
- Detect scanned/empty PDFs (`extraction_status: no_text`) and corrupted/encrypted files.
- Return `document_id` + extraction metadata; implement `DELETE /documents/{id}` and TTL sweep.
- Mobile Upload screen: picker, filename/size display, upload, status.

**Gate**

- Fixture PDFs and DOCX files produce verifiable, page/section-anchored extracted text.
- Scanned/corrupt/oversized fixtures yield the specified errors.
- Unit tests for the parser pass.

## Stage 3 — Topic retrieval and AI

**Work**

- `topic_finder.py`: keyword/phrase matching, scoring, match-status classification,
  source-order collection, overlap dedupe.
- Verbatim mode from stored raw text (no AI).
- `ai_client.py`: configurable provider, timeouts, retry/backoff, token caps.
- `summarizer.py`: topic summaries (grounded) + whole-document chunk/aggregate.
- `POST /study-jobs` + `GET /study-jobs/{id}`; synchronous for small, polled for large.
- Structured results per topic; not-found/partial warnings.

**Gate**

- Automated tests verify matching, page references, dedupe, and summary grounding
  (with a stubbed AI client).
- Missing-topic and AI-failure paths return the specified codes/warnings.

## Stage 4 — Export and mobile workflow

**Work**

- `exporter.py`: DOCX (python-docx) + PDF (ReportLab) generation.
- `GET /study-jobs/{id}/export?format=pdf|docx` streaming download.
- Results screen: sections, verbatim vs summary, source refs, warnings.
- Local persistence via Expo SQLite (`sessions`, `topics`, `results`, `exports`).
- Save/share export files; delete sessions + files.
- Actionable error messages wired to API error codes.

**Gate**

- Full workflow succeeds end-to-end on an Android device/emulator:
  upload → topics/mode → results → export PDF and DOCX → reopen offline.

## Stage 5 — Real-world testing

**Work**

- Test representative lecture PDFs/DOCX, long documents, tables/lists, missing topics.
- Test network loss, AI timeouts/rate limits, exhausted credits, export failures.
- Measure AI usage/cost per job and tune chunk/token limits.
- Fix critical failures before sharing with other students.

**Gate**

- No critical failures; documented residual limitations.
- Cost/time per typical job is acceptable.

## Dependencies

- Stage 1: env setup (Node/Expo, Python venv).
- Stage 2: PyMuPDF, python-docx.
- Stage 3: `httpx`, AI provider credentials, `pytest`.
- Stage 4: ReportLab, Expo SQLite/FileSystem/Sharing.
- Stage 5: real sample materials.

## Assumptions

1. Backend runs on a small single-instance host; in-process job state is acceptable.
2. AI provider exposes an HTTP JSON API with a chat/completions-style interface.
3. Users have internet for upload/processing; offline is read-only for saved notes.
4. PDFs are text-based (no OCR requirement in the MVP).
5. DOCX page numbers are approximate; paragraph/section anchors are acceptable.
6. Dev/test uses English lecture material (UI copy is English).

## Unresolved decisions (need input or Stage-1 timebox)

1. **AI provider specifics** — which provider/model (`AI_PROVIDER_BASE_URL`, `AI_MODEL`)?
   Impact: summarizer prompts/limits. Defaulting to an OpenAI-compatible chat API shim.
2. **Job model** — fully synchronous vs. poll-based for large files. Plan: sync for small,
   poll for large; threshold tunable.
3. **Mobile app package id / display name** — needed for Expo build.
4. **Export destination** — app storage + share sheet vs. user-chosen folder.
5. **DOCX "page" display** — show section/paragraph anchors only, or approximate pages.

## Risks and mitigations

| Risk                                   | Mitigation                                                    |
| -------------------------------------- | ------------------------------------------------------------- |
| Scanned PDFs with no text              | Detect and report clearly; no OCR in MVP                      |
| DOCX page numbers unreliable           | Use section/paragraph anchors; label as approximate           |
| Long docs exceed model context         | Chunk + aggregate; bounded sections and tokens                |
| AI hallucinations / invented facts     | Grounding prompt + validation + source refs on every claim    |
| AI cost overruns                       | Token caps + section caps + detail limits                     |
| Provider outages/rate limits           | Timeouts, backoff, clear error codes, `partial` status        |
| Duplicate/missing passages             | Overlap dedupe + always attach source refs                    |
| Temp file leakage                      | TTL sweep + DELETE endpoint + no web-served temp path         |
| OneDrive/AV interference during dev    | Project kept outside OneDrive (per user choice)               |

## Acceptance criteria summary

| Stage | Key acceptance criterion                                             |
| ----- | -------------------------------------------------------------------- |
| 0     | Docs complete, consistent; no code; approval requested                |
| 1     | App runs; `/health` OK; navigation works                             |
| 2     | Fixtures produce verifiable, anchored text; errors handled           |
| 3     | Matching, refs, dedupe, grounding verified by tests                  |
| 4     | Full flow + exports work on Android; offline read works              |
| 5     | Real materials pass; critical failures fixed                         |
