# AI Study Assistant — Architecture

Version 1.0 · Android-first · MVP · Status: **Stage 4 plus Phase 2/3 backend (topic review and study output)**

> Implementation follows the stage gates in `implementation-plan.md`.

## 1. Purpose and scope

A lightweight Android app that lets a student turn lecture materials (PDF/DOCX) into
organized study notes:

- Upload lecture material.
- Choose `extracted_text_only`, `text_and_summaries`, or `summaries_only`.
- Review notes with source references (page/section anchors).
- Optionally match reviewed topics to exact source passages and request original
  passages, source-grounded study material, or both.
- Export as PDF or DOCX.

In scope for the MVP:

- Android-only mobile client (React Native + Expo + TypeScript).
- Single monolith backend (Python + FastAPI).
- PDF and DOCX input/output.
- Ordered Groq and Gemini providers for grounded summaries, with provider failures
  falling through the configured chain.
- Local device persistence of study sessions.

Explicitly **out of scope** for the MVP:

- iOS build (the Expo choice keeps the door open, but iOS is not a target).
- User accounts, cloud sync, payments, admin dashboard.
- Vector databases, message brokers, background workers.
- Syllabus/curriculum ingestion or matching. Topic review only matches user-confirmed
  topics against the uploaded document; there is no external curriculum source.
- Cloud OCR services; OCR uses a local Tesseract executable with bounded page/time limits.
- Multi-tenant hosting / scaling concerns.

## 2. System context

```
┌──────────────────────────┐        HTTPS/JSON         ┌───────────────────────────┐
│  Android app             │  ───────────────────────▶ │  FastAPI backend (monolith)│
│  React Native + Expo     │  ◀─────────────────────── │  Python 3.11+             │
│  TypeScript              │   multipart upload /      │                           │
│                          │   JSON results / files    │  ┌─────────────────────┐  │
│  ┌────────────────────┐  │                           │  │ document service    │  │
│  │ Expo Router screens│  │                           │  │ study processor     │  │
│  ├────────────────────┤  │                           │  │ source text         │  │
│  │ local SQLite store │  │                           │  │ summary (AI)        │  │
│  └────────────────────┘  │                           │  │ output/export       │  │
└──────────────────────────┘                           │  └─────────────────────┘  │
                                                        │            │              │
                                                        │            ▼              │
                                                        │  Google Gemini API        │
                                                        │  (server-side key only)   │
                                                        └───────────────────────────┘
```

The client never holds the AI provider key. All AI traffic is proxied by the backend.

## 3. Mobile application architecture

### 3.1 Stack

| Concern            | Choice                          | Notes                                   |
| ------------------ | ------------------------------- | --------------------------------------- |
| Framework          | React Native (Expo, managed)    | Easiest Android build/dev loop          |
| Language           | TypeScript (strict mode)        | Shared request/response types           |
| Navigation         | Expo Router (file-based)        | `app/` directory screens                |
| History            | Backend `GET /study-jobs`       | Device-local SQLite deferred (Stage 4)  |
| File picking       | `expo-document-picker`          | Android SAF picker for PDF/DOCX         |
| File read/upload   | `expo-file-system` + `fetch`    | Multipart upload                        |
| Download/save      | `expo-file-system` + `expo-sharing` | Download export, then Android share sheet |
| HTTP client        | `fetch` wrapped in a typed `ApiClient` | Central error mapping             |

### 3.1.1 App identity

| Setting        | Value                        |
| -------------- | ---------------------------- |
| Display name   | Study Assistant              |
| Android package| `com.aidi.studyassistant`    |
| Platform       | Android-first (Expo managed) |

### 3.2 Screens and navigation flow

```
Home (index) -> [New study] -> Upload -> Topics & Mode -> Results (export)
   |                                                        |
   |                                                        +-> [Continue in chat] -> Workspace
   +-> [Ask / suggested prompt] -> Workspace (chat)
   +-> [Output chip] -> Results (by job_id) / Topic Results
   +-> [Continue studying] -> Workspace (latest job/result)
   +-> [Settings] -> Settings (connection, data handling, about)
```

Recent sessions are listed from `GET /api/v1/study-jobs` and
`GET /api/v1/topic-study-results`, then grouped on-device by study document in
`mobile/services/recentSessions.ts`. They reopen by `job_id` or `result_id`.

| Screen        | File                | Responsibilities                                                             |
| ------------- | ------------------- | ---------------------------------------------------------------------------- |
| Home          | `app/index.tsx`     | Chat-first ask box, suggested prompts, New study, grouped recent sessions    |
| Settings      | `app/settings.tsx`  | Connection/health, AI readiness, retention disclosure, app version           |
| Upload        | `app/upload.tsx`    | Pick PDF/DOCX, show filename/size, validate, upload, show extraction status  |
| Study Mode    | `app/topics.tsx`    | Choose `extracted_text_only` / `text_and_summaries` / `summaries_only`        |
| Results       | `app/results.tsx`   | Source text vs AI summaries, source refs, warnings, export buttons (Stage 4) |
| Jottings      | `app/photo-notes.tsx` | Pick PDF/DOCX, type jottings, add photos, review typed/OCR text |
| Topic Review  | `app/photo-topics.tsx` | Edit topics, inspect deterministic matches/provenance, choose A/B/C |
| Topic Results | `app/photo-results.tsx` | Show original passages, source-grounded AI material, or both |
| Workspace     | `app/workspace.tsx` | Chat-style routing to existing summary/topic endpoints, attachment, exports |

#### Mobile visual system

The Android-first visual direction is warm editorial rather than a generic SaaS
dashboard. Tokens live in `mobile/theme.ts`:

- Warm paper canvas `#F5F1E8`, warm surface `#FFFCF5`, forest accent `#2E5B43`,
  ink `#1C2A22`; muted text is darkened to meet contrast on the paper canvas.
- Display headings use the platform serif (`serif` on Android, Georgia on iOS);
  body copy uses the system sans-serif.
- Spacing follows a 4/8-based scale, cards use restrained 20px radii, and primary
  touch targets are at least 44dp.
- The app is locked to light appearance; the splash and Android adaptive icon use
  the same warm canvas.
- Home, Workspace and Settings were checked at a 360x800 CSS viewport through an
  Expo web preview. Physical Android validation remains outstanding.

Supporting directories:

- `components/` — presentational building blocks (buttons, section cards, banners).
- `services/` — `apiClient.ts`, `sessionStore.ts` (SQLite), `fileService.ts`, `chatOrchestrator.ts` (deterministic intent routing).
- `types/` — shared DTOs mirroring the backend schema.

### 3.3 Mobile state model

Screens hold local component state for the active flow: `{ documentId, mode, jobId,
jobStatus, results }`. History is fetched from the backend when Home regains focus;
Results loads either a freshly started job or an existing `job_id`.

### 3.4 Online behavior

- Internet is required for upload, processing, history, and export.
- Completed results are retrieved from the backend by `job_id` and exported on demand.
- Device-local offline storage (Expo SQLite) is deferred beyond Stage 4.

## 4. Backend architecture

### 4.1 Stack

| Concern              | Choice                | Notes                                        |
| -------------------- | --------------------- | -------------------------------------------- |
| Web framework        | FastAPI               | Async endpoints, automatic OpenAPI           |
| Runtime              | Python 3.11+          | (dev machine has 3.14; pin in deploy image)  |
| Validation           | Pydantic v2           | Request/response schemas                     |
| PDF parsing          | PyMuPDF (`fitz`)      | Per-page text + page indices                 |
| DOCX parsing         | python-docx           | Paragraphs, tables, heading styles           |
| OCR                  | Tesseract CLI         | Local, bounded; scanned PDF pages + DOCX images |
| Persistence          | SQLite (stdlib `sqlite3`) | Document metadata + study-job results; no ORM |
| PDF export           | PyMuPDF (existing dep) | Structured notes → PDF                      |
| DOCX export          | python-docx           | Structured notes → DOCX                      |
| AI provider          | Google Gemini Developer API | Server-side `GEMINI_API_KEY`; `gemini-3.8-flash` default |
| Server               | Uvicorn               | Dev + simple production                      |

### 4.2 Layering

The backend is a monolith with strictly separated services so the AI model can be
swapped without touching parsing/export.

```
api/routers ──▶ services ──▶ (parsers / AI client / exporters)
     │             │
     │             └── schemas (Pydantic DTOs)
     └── config.py (env-driven settings)
```

| Service                | Module                              | Responsibility                                                    |
| ---------------------- | ----------------------------------- | ----------------------------------------------------------------- |
| Document parser        | `services/document_parser.py`       | Validate, detect type, extract page/section-aware text            |
| Study processor        | `services/study_processor.py`       | Build bounded units; return source text and/or grounded summaries |
| Persistence            | `services/db.py`                    | SQLite connection + `user_version` migrations (optional)          |
| Document store         | `services/storage.py`               | Source files + document metadata; hydrates/persists via SQLite    |
| Job store              | `services/job_store.py`             | Study-job state/results; persisted, newest-first listing          |
| Output/export          | `services/exporter.py`              | Assemble blocks; render PDF (PyMuPDF) and DOCX (python-docx)      |
| AI client              | `services/ai_client.py`             | Gemini `generateContent` wrapper: timeouts, retries, typed errors |

## 5. Document parsing and source-location preservation

### 5.1 PDF (PyMuPDF)

1. Open with PyMuPDF; reject if encrypted and no password support configured.
2. Extract text **per page** (`page.get_text()`), keep zero-based page index internally.
3. Detect pages below `MIN_PDF_CHARS_PER_PAGE` non-whitespace characters and attempt
   bounded local Tesseract OCR for each page (up to `OCR_PAGE_LIMIT`, one page at a
   time, at `OCR_DPI`, with `OCR_TIMEOUT_S`). OCR text keeps the page's exact 1-based
   anchor. If OCR is unavailable, times out, or hits the limit, return warnings and a
   `no_text`/`partial` status instead of failing silently.
4. Preserve the **original raw text** per page (served by the text modes) and a
   **normalized copy** (whitespace-collapsed, lowercased) for searching only.
5. Display pages as **one-based** numbers to users; store zero-based internally.

### 5.2 DOCX (python-docx)

1. Read paragraphs in document order; retain `paragraph_index`.
2. Read tables cell-by-cell; flatten with row/column markers; retain order.
3. Track nearest preceding heading style (`Heading 1..6`) as a `section_label`.
4. DOCX has **no reliable page numbers** (layout-dependent). Internal references use
   `paragraph_index` and `section_label`. Displayed "page" for DOCX is approximate or
   omitted (documented limitation; see §5.3).
5. OCR images embedded in the document body with local Tesseract when available. OCR
   text is anchored to the containing paragraph and nearest heading; duplicate images
   are OCR'd once. Bounded by `OCR_PAGE_LIMIT` and `OCR_TIMEOUT_S`.

### 5.3 Reference model

| Source | Internal anchor         | User-visible reference              |
| ------ | ----------------------- | ----------------------------------- |
| PDF    | `page_index` (0-based)  | "Page N" (1-based, exact)           |
| DOCX   | `paragraph_index`, `section_label` | "Section: <heading>" / "¶ N" (approx.) |

### 5.4 Chunking

Long documents are split into **sections** at page boundaries (PDF) or heading
boundaries (DOCX), with an overlapping window of paragraphs to avoid cutting a fact in
half. Section size is bounded by a configurable character budget
(`MAX_SECTION_CHARS`, default ~6000) to fit the model context.

## 6. Study modes and topic study output

The existing `/study-jobs` modes are unchanged:

| Mode                  | AI call | Behaviour                                                      |
| --------------------- | ------- | -------------------------------------------------------------- |
| `extracted_text_only` | no      | Return the stored source text per section/page with its anchor |
| `text_and_summaries`  | yes     | Return the source text **and** a grounded AI summary per unit  |
| `summaries_only`      | yes     | Return grounded AI summaries + source refs, no source text     |

Sections are grouped into **units** of consecutive sections whose combined raw text does
not exceed `MAX_SUMMARY_CHUNK_CHARS`; an oversized single section is split at sentence
boundaries. Every unit keeps the set of source anchors it covers, so references survive
chunking. Mode `extracted_text_only` bypasses the AI client entirely.

### 6.1 Topic review

Topic review is deterministic and never calls an AI provider. Reviewed topic text is
segmented into normalized terms, candidate sections are scored with
`0.80 * coverage + 0.20 * phrase_match`, and only strong, unambiguous matches are
returned. Each match carries the exact stored `raw_text`, offsets, and source anchor.
Ambiguous or unmatched topics are reported explicitly rather than guessed.

### 6.2 Topic study output

`POST /topic-sources/{source_id}/study-output` builds one of three explicit outputs:

| Mode                | Original passages | AI material |
| ------------------- | ----------------- | ----------- |
| `source_passages`   | yes, verbatim     | no          |
| `ai_study_material` | no                | yes         |
| `both`              | yes, verbatim     | yes         |

AI material is generated only for matched topics, from the matched source passages.
Ambiguous and unmatched topics never receive generated material. The default
`ai_scope` is `source_only`. `source_and_beyond` is an explicit opt-in that adds a
separate `beyond_source` block, marked `grounded_in_uploaded_material: false`; it is
never mixed with source-grounded material or given fabricated citations. If the provider
fails for some topics, mode C still returns the original passages and reports `partial`
or `failed`; provider failures are never disguised as success.

Each output is persisted in `topic_study_results` with a `result_id`. Reopening and
exporting use the stored payload, so they never re-run matching or AI generation.
Saved results outlive the source and document; `document_available` is recomputed on
read. The Home screen merges study jobs and topic results into one recent list.

## 7. Source text (no AI rewriting)

- `extracted_text_only` and the text half of `text_and_summaries` return the parser's
  stored `raw_text` verbatim, never re-generated by the model.
- The model is never asked to reproduce source text.
- Text served for display comes from the parser's stored raw text, guaranteeing fidelity.

## 8. AI summarization (Groq and Gemini)

Successful summaries are cached in SQLite per user. Cache keys hash the purpose,
normalized source text, prompt version, and beyond-source flag; expired entries are
removed according to `RETENTION_MINUTES`. Result diagnostics report
`summary_cache_hits` without logging source content.

### 8.1 Grounding rules

- The model receives only the unit's source text (or the reduced unit summaries).
- System instruction: summarize **only** from the provided text; do not add external
  facts; preserve numeric/critical values; if information is absent, say so; do not give
  clinical/medical advice.
- Output is constrained to plain text and validated: empty or blocked responses raise
  `ai_invalid_response` rather than being returned.

### 8.2 Unit summaries (map step)

For each unit: send bounded source text → one summary. Prompts include the unit's source
label (for example `Page 12` or `Section: Anemias`).

### 8.3 Whole-document summary (reduce step)

1. Collect the unit summaries that succeeded.
2. If more than one unit, join them (bounded by `MAX_SUMMARY_CHUNK_CHARS`) and request a
   single overview summary (reduce step).
3. The overview is best-effort: if it fails, the job is `partial` and unit summaries are
   still returned.

### 8.4 Context-window and failure handling

- Requests are bounded by `MAX_SUMMARY_CHUNK_CHARS` and each provider's output token
  setting; the default source chunk is 4000 characters (about 500-800 words), no
  arbitrarily large document is sent in one request.
- Providers are attempted in `AI_PROVIDER_ORDER` (`groq,gemini` by default); missing
  keys are skipped. Retryable failures (`429`, `5xx`, network, timeout) are retried
  with backoff before moving to the next provider.
- A unit that still fails is marked with a warning and no summary; if **every** unit
  exhausts its providers, the client receives `ai_quota_exhausted`; verbatim source
  passages remain available. Summaries are never fabricated.

### 8.5 Topic study material

- Source-grounded material uses a topic-specific prompt containing only the matched
  passages. Its system instruction forbids outside facts, invented citations, and
  unverifiable numeric/clinical claims.
- Beyond-source material is generated only when the request explicitly sets
  `ai_scope: "source_and_beyond"`. It uses a different prompt and system instruction,
  may use general knowledge, and is returned separately with
  `grounded_in_uploaded_material: false`.
- The source-grounded block keeps `basis_passage_ids` and `basis_refs` copied from the
  exact matched passages. The beyond-source block has no citations and must not be
  presented as uploaded material.
- A failed provider call marks the topic `ai_failed`. Some failures yield `partial`; if
  every eligible topic fails, the response is `failed`. Modes A/C preserve original
  passages regardless of AI failure.

## 9. Export

| Format | Library     | Behavior                                                              |
| ------ | ----------- | --------------------------------------------------------------------- |
| DOCX   | python-docx | Title, metadata, per-item source text and/or summary                  |
| PDF    | PyMuPDF     | Same block structure, manual wrapped text layout (no new dependency)  |

Export content (mode-aware):

- Header: session title, display mode, status, generation date.
- `extracted_text_only`: per item, reference(s) + source text.
- `text_and_summaries`: per item, reference(s) + source text + AI summary.
- `summaries_only`: overall summary + per-item summaries and references, **no** full
  source text (items store `text: null` in that mode).
- Notes: any global warnings.

The export is rendered **on demand from the persisted job payload** (never by re-parsing
the source), so it still works after the source file is deleted, and streamed as a file
download with a download filename. On the device, the app downloads to the cache with
`expo-file-system` and opens the **Android share sheet** via `expo-sharing`.

## 10. API overview

Full contract in `api-contract.md`. Summary:

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET    | `/health` | Backend availability + AI configuration status |
| POST   | `/api/v1/documents` | Upload and parse a PDF/DOCX |
| POST   | `/api/v1/study-jobs` | Start extraction/summarization |
| GET    | `/api/v1/study-jobs` | Recent study jobs (newest first) |
| GET    | `/api/v1/study-jobs/{job_id}` | Job status + structured results |
| GET    | `/api/v1/study-jobs/{job_id}/export?format=pdf\|docx` | Download export |
| GET    | `/api/v1/topic-sources` | Recent jottings sources for the caller |
| GET    | `/api/v1/topic-sources/{source_id}/images/{index}` | Stream a stored note image inline |
| POST   | `/api/v1/topic-sources/{source_id}/topic-review` | Match reviewed topics to exact source passages |
| POST   | `/api/v1/topic-sources/{source_id}/study-output` | Original passages and/or AI study material |
| GET    | `/api/v1/topic-results` | Recent saved topic study outputs |
| GET    | `/api/v1/topic-results/{result_id}` | Reopen a saved output without recomputation |
| GET    | `/api/v1/topic-results/{result_id}/export?format=pdf\|docx` | Download a saved output |
| DELETE | `/api/v1/topic-results/{result_id}` | Delete a saved output |
| DELETE | `/api/v1/documents/{document_id}` | Delete document + source file (results kept) |

Processing is **synchronous for small documents** (≤ `SYNC_PROCESSING_MAX_SECTIONS`
sections, default 20) and returns results inline; larger jobs return a `job_id` for
status polling. No broker/worker infrastructure in the MVP — long jobs run in a bounded
async task with in-process status tracking.

## 11. Data model and file lifecycle

Full detail in `data-model.md`. Entities: Document and StudyJob, both persisted
server-side in SQLite (no ORM) and reconciled on startup.

File lifecycle:

1. Upload → source file in a managed directory + `documents` row with `expires_at`.
2. Parse → sections/warnings persisted as JSON on the document row.
3. Process → `study_jobs` row with items/summary/progress; results retrievable by id.
4. Export → rendered on demand from the job payload.
5. Cleanup → rows + source files removed on `DELETE`, expiry, or the periodic sweep
   (`RETENTION_MINUTES`, default 7 days). Deleting a document keeps its job results;
   a missing source file marks the document unavailable and blocks new processing.

## 12. Security, privacy, and resource limits

- **Secrets**: AI provider key lives only in backend environment variables
  (`.env`); never bundled in the mobile app. `.env` is git-ignored; `.env.example`
  documents required keys.
- **Uploads**: validate extension **and** magic bytes/type; cap size
  (`MAX_UPLOAD_MB`, default 25). Reject unsupported/oversized files with actionable errors.
- **Requests**: per-request timeout; global rate limit (simple in-process limiter for MVP).
- **AI usage**: cap tokens per request and per job
  (`MAX_AI_TOKENS_PER_REQUEST`, `MAX_SECTION_CHARS`); guard against runaway cost.
- **Logging**: never log API secrets, full document text, or full AI prompts/responses
  (log lengths/hashes, not content).
- **Document access**: `document_id`/`job_id` are unguessable (UUIDv4). No cross-user
  access because there is no multi-user server state in the MVP.
- **Retention/privacy**: uploads and results are retained server-side for
  `RETENTION_MINUTES` (default 7 days) and the SQLite file is git-ignored; no paths or
  content are logged. On the Gemini free tier, submitted summary text may be used by
  Google to improve its products.
- **Deletion**: `DELETE /documents/{id}` removes the source file and record immediately;
  completed results remain readable/exportable until their own expiry.

## 13. Error handling

See `api-contract.md` for the error envelope and codes. Required handled cases:

- Unsupported or oversized files.
- Encrypted, corrupted, empty, or scanned (no text) PDFs.
- DOCX with tables/lists/little extractable text.
- Topics with exact / partial / no matches.
- Documents exceeding the model context window.
- AI timeouts, rate limits, invalid responses, exhausted credits.
- Failed/incomplete exports.
- Network loss during upload or processing (client retries / resumes where safe).
- Duplicate passages, missing page references, accidental AI-invented claims
  (mitigated by grounding rules + validation).

## 14. Testing strategy (summary)

- **Backend unit**: parsers, study processor chunking, Gemini client error mapping,
  exporter (fixture documents).
- **Backend integration**: endpoint flows for all three job modes, topic review, and
  topic study outputs with an AI client stub (deterministic, no network).
- **Contract**: schema validation for every request/response.
- **Mobile**: component tests for screens; `ApiClient` error-mapping tests.
- **End-to-end**: full flow on an Android emulator/device against a local backend.
- **Failure injection**: empty content, malformed files, AI unavailable/429/timeout/
  malformed responses, export failure, network loss.

## 15. Technology decisions and dependencies

Approved stack (from the spec): React Native, Expo, TypeScript, Expo Router, FastAPI,
PyMuPDF, python-docx.

Additional dependencies and justification:

| Dependency | Layer | Justification |
| ---------- | ----- | ------------- |
| `expo-document-picker` | Mobile | Android file picker for PDF/DOCX |
| `expo-file-system`, `expo-sharing` | Mobile | Upload, download exports, Android share sheet |
| `sqlite3` (stdlib) | Backend | Persistence without an ORM or extra dependency |
| `pydantic` (v2) | Backend | Schema validation (FastAPI default) |
| `python-multipart` | Backend | Multipart upload parsing in FastAPI |
| `httpx` | Backend | Async HTTP client for the configurable AI API |
| `uvicorn` | Backend | ASGI server (FastAPI standard) |
| `python-dotenv` / `pydantic-settings` | Backend | Environment configuration |
| `pytest`, `pytest-asyncio` | Backend dev | Test runner |
| `anyio`/`asyncio` stdlib | Backend | Bounded async job execution (no broker) |

Rejected for the MVP: vector DB, Celery/Redis workers, auth platform, cloud storage.

## 16. Open questions and risks

See `implementation-plan.md` §Risks for the consolidated list.

## 17. Acceptance criteria (this phase)

- `docs/architecture.md`, `docs/api-contract.md`, `docs/data-model.md`,
  `docs/implementation-plan.md`, and `README.md` exist and are internally consistent.
- No application source files were created.
- Assumptions, unresolved decisions, and risks are listed.
- Implementation stops pending explicit approval.
