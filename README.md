# AI Study Assistant

An Android-first mobile app that helps students turn lecture materials (PDF/DOCX) into
organized study notes without manually searching through hundreds of pages.

Upload a document, choose how it is presented — **extracted text only**,
**text + AI summaries**, or **AI summaries only** — review the results with source page
references, reopen previous sessions, and export as PDF or DOCX.

> **Status: chat-first study workspace with deterministic range extraction and topic review.** The backend uploads, validates and
> extracts text from PDF/DOCX files with source anchors, including bounded OCR for
> scanned pages and embedded images. Study jobs support the three original display modes;
> source-passages mode copies exact stored extraction text and works with no AI provider.
> Summary modes are grounded in extracted source and use Groq with Gemini fallback when
> credentials are configured. Topic review deterministically
> matches user-confirmed topics to exact source passages, and topic study output offers
> original passages, source-grounded AI material, or both. AI material uses only uploaded
> material unless the user explicitly opts in to separate beyond-source background.
> Documents and study results are persisted in SQLite (default 7-day retention) so history
> survives restarts, and results can be exported as PDF/DOCX even after the source file is
> deleted. See `docs/implementation-plan.md` and `docs/api-contract.md`.

## Documentation

| Document | Contents |
| -------- | -------- |
| [`docs/architecture.md`](docs/architecture.md) | System design, screens, backend services, parsing, AI grounding, security |
| [`docs/api-contract.md`](docs/api-contract.md) | Endpoints, schemas, errors, configurable settings |
| [`docs/data-model.md`](docs/data-model.md) | Server (temp) and device (persistent) entities, file lifecycle |
| [`docs/implementation-plan.md`](docs/implementation-plan.md) | Stages, gates, assumptions, risks |

## Proposed system

```
mobile/   React Native + Expo + TypeScript (Expo Router) — Android app
backend/  Python + FastAPI monolith — parsing, study processing, AI summaries, SQLite persistence, exports
docs/     Architecture and contracts
```

- **Internet is required** for upload, processing, history, and export. Saved results
  live on the backend (SQLite) and can be reopened/exported until their retention window
  ends.
- **AI provider credentials live on the backend only** (environment variables). They are
  never bundled in the mobile app.
- Scanned PDF pages and images embedded in DOCX can be OCR'd with the local
  Tesseract CLI. OCR is bounded by a page/image limit and per-item timeout.
- **App identity**: display name **Study Assistant**, Android package
  `com.aidi.studyassistant`.

## Repository structure

Files marked _(Stage N)_ are planned and not yet present.

```
ai-study-assistant/
├── mobile/                      # React Native + Expo + TypeScript
│   ├── app/                     # Expo Router screens
│   │   ├── _layout.tsx          # Stack navigator
│   │   ├── index.tsx            # Chat-first home, recent sessions
│   │   ├── upload.tsx           # Upload + extraction
│   │   ├── topics.tsx           # Study mode selection
│   │   ├── results.tsx          # Study results (export in Stage 4)
│   │   ├── workspace.tsx        # Study Workspace chat
│   │   └── settings.tsx         # Connection, data handling, about
│   ├── components/              # Screen, buttons, cards
│   ├── services/                # apiClient.ts
│   ├── types/                   # api.ts (shared DTOs)
│   ├── app.json                 # Expo config (package id, name)
│   ├── eslint.config.js
│   ├── tsconfig.json
│   └── package.json
├── backend/                     # Python + FastAPI
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── errors.py            # ApiError + error envelope handlers
│   │   ├── api/
│   │   │   ├── health.py
│   │   │   ├── documents.py     # POST/DELETE /api/v1/documents
│   │   │   └── study.py         # POST/GET /api/v1/study-jobs
│   │   ├── schemas/
│   │   │   ├── health.py
│   │   │   ├── documents.py
│   │   │   └── study.py
│   │   └── services/
│   │       ├── document_parser.py   # PDF (PyMuPDF) + DOCX (python-docx)
│   │       ├── ocr.py               # bounded Tesseract CLI runner
│   │       ├── ai_client.py         # Gemini Developer API client
│   │       ├── study_processor.py   # grounded summaries + chunking
│   │       ├── db.py                # SQLite connection + migrations
│   │       ├── job_store.py         # persisted study job registry
│   │       ├── exporter.py          # PDF (PyMuPDF) + DOCX (python-docx)
│   │       └── storage.py           # source files + metadata persistence
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── requirements-dev.txt
├── docs/
│   ├── architecture.md
│   ├── api-contract.md
│   ├── data-model.md
│   └── implementation-plan.md
├── .env.example
├── .gitignore
└── README.md
```

## Stack

| Layer | Technology |
| ----- | ---------- |
| Mobile | React Native 0.86, Expo SDK 57, TypeScript, Expo Router |
| Mobile files | expo-file-system + expo-sharing (download/share exports) |
| Backend | Python, FastAPI, Uvicorn |
| Persistence | SQLite (stdlib `sqlite3`) |
| PDF parsing | PyMuPDF |
| DOCX parsing | python-docx |
| OCR | Local Tesseract CLI (optional) |
| AI | Google Gemini Developer API (server-side key; optional) |
| Export | PyMuPDF (PDF), python-docx (DOCX) |

## Setup

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements-dev.txt     # runtime + test deps
copy ..\.env.example .env               # optional: fill in Gemini AI values
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: `GET http://localhost:8000/health`

With no AI credentials configured, `/health` returns `200` with `status: "degraded"`
and `ai.provider: "missing"`.

Endpoints:

| Method | Endpoint | Purpose |
| ------ | -------- | ------- |
| `GET` | `/health` | Backend availability + AI configuration status |
| `POST` | `/api/v1/documents` | Upload and parse a PDF/DOCX; returns extraction metadata |
| `POST` | `/api/v1/study-jobs` | Start a study job (`extracted_text_only`, `text_and_summaries`, `summaries_only`) |
| `POST` | `/api/v1/documents/{id}/ranges` | Extract exact stored sections for page, numbered-item, or heading selectors (no AI) |
| `POST` | `/api/v1/documents/{id}/range-study` | Run selected sections through the existing study modes and export pipeline |
| `POST` | `/api/v1/chat/command` | Convert a short request and metadata into a validated command |
| `GET` | `/api/v1/study-jobs` | Recent study jobs (newest first) for the history list |
| `GET` | `/api/v1/study-jobs/{job_id}` | Job status + structured results (poll for large jobs) |
| `GET` | `/api/v1/study-jobs/{job_id}/export?format=pdf\|docx` | Download an export of a completed job |
| `POST` | `/api/v1/topic-sources` | Create jottings from typed text and/or note photos, linked to a source document |
| `GET` | `/api/v1/topic-sources/{source_id}` | Read jottings, photo transcripts, and source metadata |
| `PATCH` | `/api/v1/topic-sources/{source_id}` | Correct typed jottings, transcripts, or reviewed topics |
| `POST` | `/api/v1/topic-sources/{source_id}/topic-review` | Deterministically match reviewed topics to exact source passages |
| `POST` | `/api/v1/topic-sources/{source_id}/study-output` | Build original passages, source-grounded AI material, or both |
| `DELETE` | `/api/v1/documents/{document_id}` | Delete a document and its source file (results kept) |

Documents and study results are persisted in SQLite and retained for
`RETENTION_MINUTES` (default 7 days), surviving backend restarts. They are removed on
delete, on expiry, or by the periodic cleanup sweep. Deleting a document removes the
source file but keeps completed results readable and exportable (they report
`document_available: false`), and blocks new jobs for that document.

Pages with fewer than `MIN_PDF_CHARS_PER_PAGE` non-whitespace characters are treated
as having no usable text. Damaged PDFs that the parser has to repair are returned as
`partial` with a `document_repaired` warning rather than being reported as scanned.
DOCX archives that expand beyond `MAX_DOCX_UNCOMPRESSED_MB` are rejected.

### AI setup (optional)

Summary/explanation study modes need an AI provider. `extracted_text_only`, topic
`source_passages`, and document range extraction work with no AI configuration at all.

Set `AI_PROVIDER_ORDER=groq,gemini` and one or both backend-only environment keys:
`GROQ_API_KEY` and `GEMINI_API_KEY`. Groq is tried first; providers without keys are
skipped, and transient/provider errors fall back to the next configured provider.
Defaults are `GROQ_MODEL=qwen/qwen3.8-27b` and
`GROQ_COMMAND_MODEL=qwen/qwen3.8-27b`. Groq currently documents this model with tool use
and JSON mode ([model details](https://console.groq.com/docs/model/qwen/qwen3.8-27b),
[model directory](https://console.groq.com/docs/models)). Check current availability
and pricing in Groq Console before deployment. Gemini remains available as fallback.

The key is read **only** by the backend and is never sent to the mobile app, logged, or
included in responses. Requests send only the extracted source chunks needed for a
summary, and the model is instructed to summarise **only** from that source. Topic study
output defaults to `ai_scope: "source_only"`; `source_and_beyond` is an explicit opt-in
and returns any extra background in a separate block marked as not grounded in the
uploaded material.

> **Free tier and data use.** The Gemini Developer API free tier has quota limits and,
> on the free tier, Google may use submitted content to improve its products. Review
> Google's current terms and quotas before uploading sensitive material.

If all summary providers are unavailable, combined mode retains verbatim passages and
adds `ai_quota_exhausted` with "Summary limit reached. Original text is still available."
Successful summaries are cached per user and prompt version for `RETENTION_MINUTES`;
failures are not cached. The chat command endpoint sends only the bounded user message and
document metadata, never document text or chat history. Obvious selectors use deterministic
handling without a model call.

AI failures are surfaced clearly: `ai_not_configured` (503), `ai_rate_limited` (429),
`ai_timeout` (504), `ai_provider_error` (502), and `ai_invalid_response` (502). AI modes
never return empty summaries or pretend success.

### Verify verbatim extraction

From `backend/`, run `python scripts/verify_verbatim.py path/to/file.pdf` or pass a DOCX.
The script samples readable PDF pages or stored DOCX paragraphs and exercises the parser
and range-selection service used by the API. It checks selected anchors and raw text
character-for-character and needs no AI credentials.

### OCR setup (optional)

Scanned PDF pages and images embedded in DOCX are OCR'd with the Tesseract CLI.
Text-based documents do not need it. If Tesseract is missing, uploads still succeed
and the response includes an `ocr_unavailable` warning.

**Windows**

1. Install the UB Mannheim 64-bit Tesseract build and keep the English language data.
2. Either add `C:\Program Files\Tesseract-OCR` to `PATH`, or set
   `OCR_TESSERACT_PATH=C:\Program Files\Tesseract-OCR\tesseract.exe` in `backend/.env`.
3. Verify with `tesseract --version`.
4. If the language data is not found, set `TESSDATA_PREFIX` to the folder containing
   `eng.traineddata` (usually `C:\Program Files\Tesseract-OCR\tessdata`).

**Linux (deployment)**

```bash
sudo apt-get update
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng
tesseract --version
```

Install additional language packs as needed (`tesseract-ocr-deu`, etc.) and set
`OCR_LANGUAGE`. Ensure the service account can execute `tesseract`; otherwise set an
absolute `OCR_TESSERACT_PATH`.

**Bounds and configuration**

- `OCR_PAGE_LIMIT` (default 30): maximum PDF pages or DOCX images OCR'd per upload;
  `0` disables OCR.
- `OCR_DPI` (default 200): PDF render resolution. Lower it on memory-constrained hosts.
- `OCR_TIMEOUT_S` (default 30): per-page/image timeout.
- `OCR_LANGUAGE` (default `eng`): Tesseract language pack(s), for example `eng+deu`.

The mobile upload timeout is 16 minutes to cover the worst-case 30-page OCR run.
OCR runs one page/image at a time. No cloud OCR service is used.

### Mobile

```bash
cd mobile
npm install
copy .env.example .env.local
npx expo start
```

Then run on an Android emulator or device (`a` in the Expo CLI).

`mobile/.env.example` sets `EXPO_PUBLIC_USE_RN_FETCH=true`. Expo SDK 57's global
`fetch` cannot serialize React Native multipart file parts, so PDF/DOCX uploads fail
with `Unsupported FormDataPart implementation` without this flag. `.env.local` is
gitignored; restart Expo with `npx expo start -c` after changing it.

> **Expo Go compatibility.** The project targets **Expo SDK 57**, matching the Expo Go
> client installed on the test device (SDK 57.0.9). Expo Go only supports one SDK
> version at a time, so the project SDK and the Expo Go app must match. To move to a
> newer SDK later, update Expo Go from the Play Store (or expo.dev/go) and upgrade the
> project with `npm install expo@^<sdk>` + `npx expo install --fix`.

The app resolves its backend URL automatically:

1. `EXPO_PUBLIC_API_URL` if set (e.g. `EXPO_PUBLIC_API_URL=http://192.168.1.10:8000 npx expo start`).
2. Otherwise the host running the Expo dev server (`Constants.expoConfig.hostUri`), so a
   physical device on the same network reaches the backend with no configuration.
3. Otherwise `http://10.0.2.2:8000` (Android emulator's alias for the host machine).

For a physical device the backend must listen on all interfaces (default `HOST=0.0.0.0`)
and the OS firewall must allow inbound TCP `PORT` (8000).

The **Jottings & Source** flow (available from **New study**) lets you choose the
PDF/DOCX that defines the source material, type or paste the topics to explain,
optionally add note photos, review the typed jottings and OCR transcripts, then choose
original passages, source-grounded AI material, or both.

The **Study Workspace** adds a chat-style interface over the same backend: attach a
PDF/DOCX, name a topic to find matching passages, ask for a topic summary, request a
whole-material summary, switch between passages/AI material/both, and export or reopen
saved sessions. Intent routing runs on-device in `mobile/services/chatOrchestrator.ts`;
there is no general-purpose chat model or new chat endpoint. Results and topic screens
link into the workspace with **Continue in chat**, and saved sessions reopen by
`job_id`/`result_id`.

The home screen is chat-first: an ask box and suggested prompts open the workspace (the
prompt is queued until a PDF/DOCX is attached), **New study** keeps the existing upload
flow, and recent outputs are grouped by study document. Grouping is client-side in
`mobile/services/recentSessions.ts`; no backend persistence or schema changes are
involved. Connection details, AI readiness, retention and version information live in
**Settings**.

## Checks

```bash
# Backend
cd backend && .venv\Scripts\python -m pytest        # or pytest, in the venv

# Mobile
cd mobile && npm run typecheck && npm run lint
```

## Configuration

All secrets and limits are environment-driven (see `.env.example`). The mobile app
never contains provider keys.

## Constraints

- No accounts, cloud sync, payments, admin dashboard, microservices, vector DB, or
  background-worker infrastructure in the MVP.
- **No syllabus/curriculum ingestion.** Topic review matches user-confirmed topics to
  exact extracted source passages deterministically. Topic study output can return
  those passages, source-grounded AI material, or both; beyond-source background is a
  separate explicit opt-in, never a default.
- Upload size, request timeouts, model tokens, and data retention
  (`RETENTION_MINUTES`, default 7 days) are configurable.

## License

TBD.
