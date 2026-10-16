# AI Study Assistant — Architecture

Version 1.0 · Android-first · MVP · Status: **Proposed — awaiting approval**

> This document is part of the architecture-first phase. No application code has been
> written. Implementation begins only after explicit approval (see `implementation-plan.md`).

## 1. Purpose and scope

A lightweight Android app that lets a student turn lecture materials (PDF/DOCX) into
organized study notes:

- Upload lecture material.
- Enter one or more topics (or choose whole-material scope).
- Choose verbatim extraction, AI summary, or both.
- Review notes with source references.
- Export as PDF or DOCX.

In scope for the MVP:

- Android-only mobile client (React Native + Expo + TypeScript).
- Single monolith backend (Python + FastAPI).
- PDF and DOCX input/output.
- One configurable AI provider for summaries.
- Local device persistence of study sessions.

Explicitly **out of scope** for the MVP:

- iOS build (the Expo choice keeps the door open, but iOS is not a target).
- User accounts, cloud sync, payments, admin dashboard.
- Vector databases, message brokers, background workers.
- Scanned-PDF OCR (detected and reported, not performed).
- Multi-tenant hosting / scaling concerns.

## 2. System context

```
┌──────────────────────────┐        HTTPS/JSON         ┌───────────────────────────┐
│  Android app             │  ───────────────────────▶ │  FastAPI backend (monolith)│
│  React Native + Expo     │  ◀─────────────────────── │  Python 3.11+             │
│  TypeScript              │   multipart upload /      │                           │
│                          │   JSON results / files    │  ┌─────────────────────┐  │
│  ┌────────────────────┐  │                           │  │ document service    │  │
│  │ Expo Router screens│  │                           │  │ topic retrieval     │  │
│  ├────────────────────┤  │                           │  │ extraction          │  │
│  │ local SQLite store │  │                           │  │ summary (AI)        │  │
│  └────────────────────┘  │                           │  │ output/export       │  │
└──────────────────────────┘                           │  └─────────────────────┘  │
                                                        │            │              │
                                                        │            ▼              │
                                                        │   Configurable LLM API     │
                                                        │   (server-side key only)   │
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
| Local persistence  | Expo SQLite (`expo-sqlite`)     | Sessions, results, exports metadata     |
| File picking       | `expo-document-picker`          | Android SAF picker for PDF/DOCX         |
| File read/upload   | `expo-file-system` + `fetch`    | Multipart upload                        |
| Download/save      | `expo-file-system` + `expo-sharing` | Save/share exported PDF/DOCX         |
| HTTP client        | `fetch` wrapped in a typed `ApiClient` | Central error mapping             |

### 3.2 Screens and navigation flow

```
Home (index)  ──[New study]──▶  Upload  ──▶  Topics & Mode  ──▶  Results
   │                                                              │
   ├─[Open saved session]──────────────────────────────────────▶ Results (from cache)
   └─[Settings: backend URL + AI status]
```

| Screen        | File                | Responsibilities                                                             |
| ------------- | ------------------- | ---------------------------------------------------------------------------- |
| Home          | `app/index.tsx`     | New study, recent sessions list, open saved notes, AI/backend status         |
| Upload        | `app/upload.tsx`    | Pick PDF/DOCX, show filename/size, validate, upload, show extraction status  |
| Topics & Mode | `app/topics.tsx`    | Multiline topics, whole-material toggle, mode + detail selection, start job  |
| Results       | `app/results.tsx`   | Topic sections, verbatim vs summary, source refs, warnings, export buttons   |

Supporting directories:

- `components/` — presentational building blocks (buttons, section cards, banners).
- `services/` — `apiClient.ts`, `sessionStore.ts` (SQLite), `fileService.ts`.
- `types/` — shared DTOs mirroring the backend schema.

### 3.3 Mobile state model

A single lightweight store (React context + reducer) holds the active flow:
`{ document, topics, mode, scope, summaryDetail, jobId, jobStatus, results }`.
Persisted sessions are written to SQLite on successful `GET /study-jobs/{id}`.

### 3.4 Offline behavior

- Internet is required for upload, processing, and AI summarization.
- Previously saved sessions (results + export file references) are read from SQLite
  and are fully readable offline.
- Export of an already-saved session that failed while offline can be retried when online.

## 4. Backend architecture

### 4.1 Stack

| Concern              | Choice                | Notes                                        |
| -------------------- | --------------------- | -------------------------------------------- |
| Web framework        | FastAPI               | Async endpoints, automatic OpenAPI           |
| Runtime              | Python 3.11+          | (dev machine has 3.14; pin in deploy image)  |
| Validation           | Pydantic v2           | Request/response schemas                     |
| PDF parsing          | PyMuPDF (`fitz`)      | Per-page text + page indices                 |
| DOCX parsing         | python-docx           | Paragraphs, tables, heading styles           |
| PDF export           | ReportLab             | Structured notes → PDF                       |
| DOCX export          | python-docx           | Structured notes → DOCX                      |
| AI provider          | Configurable HTTP API | Key from environment; provider-agnostic shim  |
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
| Topic retrieval        | `services/topic_finder.py`          | Match topics to sections; keep source order + page refs           |
| Extraction             | (in `topic_finder`/parser output)   | Return original passages verbatim (no AI)                         |
| Summarizer             | `services/summarizer.py`            | Source-grounded summaries via AI; chunk + aggregate               |
| Output/export          | `services/exporter.py`              | Assemble sections; render PDF (ReportLab) and DOCX (python-docx)  |
| AI client              | `services/ai_client.py`             | Provider-agnostic wrapper: timeouts, retries, token limits        |

## 5. Document parsing and source-location preservation

### 5.1 PDF (PyMuPDF)

1. Open with PyMuPDF; reject if encrypted and no password support configured.
2. Extract text **per page** (`page.get_text()`), keep zero-based page index internally.
3. Detect "no extractable text": if total extracted characters are below a threshold
   (configurable, e.g. < 20 chars/page average), classify as `scanned_or_empty` and
   return a clear limitation message instead of failing silently.
4. Preserve the **original raw text** per page (used by verbatim mode) and a
   **normalized copy** (whitespace-collapsed, lowercased) for searching only.
5. Display pages as **one-based** numbers to users; store zero-based internally.

### 5.2 DOCX (python-docx)

1. Read paragraphs in document order; retain `paragraph_index`.
2. Read tables cell-by-cell; flatten with row/column markers; retain order.
3. Track nearest preceding heading style (`Heading 1..6`) as a `section_label`.
4. DOCX has **no reliable page numbers** (layout-dependent). Internal references use
   `paragraph_index` and `section_label`. Displayed "page" for DOCX is approximate or
   omitted (documented limitation; see §5.3).

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

## 6. Topic retrieval

1. Normalize the query topic and candidate sections.
2. **Local keyword matching first**: token overlap + phrase matching (case-insensitive,
   whitespace-normalized). Score each section by:
   - exact phrase hit (highest weight),
   - significant-token overlap,
   - heading/label hit bonus.
3. Classify a topic's match status: `matched`, `partial`, or `not_found` using
   configurable thresholds.
4. **AI-assisted matching only when local matching is weak** (`partial`/`not_found`)
   and only if enabled — used to propose related wording, never to author content.
5. Collect matching passages in **source order**; drop duplicate overlapping spans
   while keeping distinct content (dedupe by normalized-span identity + overlap ratio).
6. Attach the source anchor (page/section) to every passage.
7. `not_found` topics are still returned, with a warning and no fabricated content.

Whole-document scope skips topic matching and processes all sections in order.

## 7. Verbatim extraction (no AI rewriting)

- Verbatim mode returns **original extracted text**, sliced by span from the raw text,
  never re-generated by the model.
- The model is never asked to "reproduce" source text.
- Passage text is served from the parser's stored raw text, guaranteeing fidelity.

## 8. AI summarization

### 8.1 Grounding rules

- The model receives only retrieved source text for the topic (or a document section).
- System instruction: summarize **only** from the provided text; do not add external
  facts; preserve numeric/critical values; if information is absent, say so.
- Output is constrained (plain prose or bullet list) and validated.

### 8.2 Topic summaries

For each matched topic: send retrieved passages (bounded) → receive a summary sized by
`summary_detail` (`concise` ≈ 3–5 bullets, `standard` ≈ 1 short paragraph,
`detailed` ≈ 2–3 paragraphs).

### 8.3 Whole-document summaries (chunk + aggregate)

1. Split the document into sections (§5.4).
2. Summarize each section independently (map step).
3. Aggregate section summaries in batches (reduce step) until one coherent overall
   summary remains.
4. Validate the final summary length against the detail setting.
5. Every stage is bounded by token limits and timeouts; failure of one section degrades
   gracefully with a warning rather than failing the whole job.

### 8.4 Context-window overflow

When content exceeds the model window: chunk first, summarize per chunk, then reduce.
If a single chunk still exceeds the window, it is further split; if impossible, the job
returns a partial result with a warning.

## 9. Export

| Format | Library     | Behavior                                                        |
| ------ | ----------- | --------------------------------------------------------------- |
| DOCX   | python-docx | Title, session metadata, per-topic sections, verbatim + summary |
| PDF    | ReportLab   | Same structure; portable fonts for common characters            |

Export content:

- Header: session title, document name, generation date, mode/scope.
- Per topic: topic name, match status, verbatim passage(s) with source refs, summary
  (if requested), warnings.
- Footer/notes: any global warnings (e.g. scanned PDF, partial processing).

The export is generated **on demand** from the stored job result and streamed to the
client as a file download. Export is a read-only operation on already-retrieved results.

## 10. API overview

Full contract in `api-contract.md`. Summary:

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| GET    | `/health` | Backend availability + AI configuration status |
| POST   | `/api/v1/documents` | Upload and parse a PDF/DOCX |
| POST   | `/api/v1/study-jobs` | Start extraction/summarization |
| GET    | `/api/v1/study-jobs/{job_id}` | Job status + structured results |
| GET    | `/api/v1/study-jobs/{job_id}/export?format=pdf\|docx` | Download export |
| DELETE | `/api/v1/documents/{document_id}` | Delete document + temporary data |

Processing is **synchronous for small documents**; the request returns a `job_id`
either immediately (small) or for polling (large). No broker/worker infrastructure in
the MVP — long jobs run in a bounded async task with in-process status tracking.

## 11. Data model and file lifecycle

Full detail in `data-model.md`. Summary of entities: Document, StudySession, Topic,
StudyResult, Export. Sessions/results are stored **locally on the device**; the server
holds uploads temporarily only.

File lifecycle:

1. Upload → stored in a managed temp directory with a generated `document_id`.
2. Parse → raw + normalized text held in memory / temp; original file retained
   only as long as needed.
3. Process → results returned to client.
4. Cleanup → temporary files deleted after processing or after `FILE_TTL_MINUTES`
   (default 60), whichever comes first. A scheduled sweep removes expired temp files.

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
  access because there is no multi-user server state in the MVP; temp files are scoped
  and short-lived.
- **Device storage**: users can delete saved sessions and their files from the app.

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

- **Backend unit**: parsers, topic matcher, chunker, exporter (fixture documents).
- **Backend integration**: endpoint flows with an AI client stub (deterministic).
- **Contract**: schema validation for every request/response.
- **Mobile**: component tests for screens; `ApiClient` error-mapping tests.
- **End-to-end**: full flow on an Android emulator/device against a local backend.
- **Failure injection**: missing topics, duplicate passages, malformed files, AI failure,
  export failure, network loss.

## 15. Technology decisions and dependencies

Approved stack (from the spec): React Native, Expo, TypeScript, Expo Router, FastAPI,
PyMuPDF, python-docx, ReportLab.

Additional dependencies and justification:

| Dependency | Layer | Justification |
| ---------- | ----- | ------------- |
| `expo-sqlite` | Mobile | Local session persistence (spec allows SQLite/Expo SQLite) |
| `expo-document-picker` | Mobile | Android file picker for PDF/DOCX |
| `expo-file-system`, `expo-sharing` | Mobile | Upload/download/save of documents |
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
