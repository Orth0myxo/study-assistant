# AI Study Assistant

An Android-first mobile app that helps students turn lecture materials (PDF/DOCX) into
organized study notes without manually searching through hundreds of pages.

Upload a document, choose how it is presented — **extracted text only**,
**text + AI summaries**, or **AI summaries only** — review the results with source page
references, reopen previous sessions, and export as PDF or DOCX.

> **Status: Stage 4 (persistence, study history, export).** The backend uploads,
> validates and extracts text from PDF/DOCX files with source anchors, including bounded
> OCR for scanned pages and embedded images. Study jobs support the three display modes;
> the two AI modes are grounded in the extracted source and use the Google Gemini
> Developer API when `GEMINI_API_KEY` is configured. Documents and study results are
> persisted in SQLite (default 7-day retention) so history survives restarts, and results
> can be exported as PDF/DOCX even after the source file is deleted. Topic matching is
> **out of scope**. See `docs/implementation-plan.md`.

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
│   │   ├── index.tsx            # Home (backend status)
│   │   ├── upload.tsx           # Upload + extraction
│   │   ├── topics.tsx           # Study mode selection
│   │   └── results.tsx          # Study results (export in Stage 4)
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
| `GET` | `/api/v1/study-jobs` | Recent study jobs (newest first) for the history list |
| `GET` | `/api/v1/study-jobs/{job_id}` | Job status + structured results (poll for large jobs) |
| `GET` | `/api/v1/study-jobs/{job_id}/export?format=pdf\|docx` | Download an export of a completed job |
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

### Gemini AI setup (optional)

Only the two AI study modes need this. `extracted_text_only` works with no AI
configuration at all.

1. Create a Gemini API key in Google AI Studio.
2. Set `GEMINI_API_KEY` and (optionally) `GEMINI_MODEL` in `backend/.env`.
   The default model is `gemini-3.8-flash`; verify current model names at
   <https://ai.google.dev/gemini-api/docs/models>.
3. Restart the backend. `/health` then reports `status: "ok"` and
   `ai.provider: "configured"`.

The key is read **only** by the backend and is never sent to the mobile app, logged, or
included in responses. Requests send only the extracted source chunks needed for a
summary, and the model is instructed to summarise **only** from that source.

> **Free tier and data use.** The Gemini Developer API free tier has quota limits and,
> on the free tier, Google may use submitted content to improve its products. Review
> Google's current terms and quotas before uploading sensitive material.

AI failures are surfaced clearly: `ai_not_configured` (503) before a job starts,
`ai_rate_limited` (429), `ai_timeout` (504), `ai_provider_error` (502), and
`ai_invalid_response` (502). AI modes never return empty summaries or pretend success.

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
- **No topic matching / syllabus matching**; study output is per-document source text
  and/or grounded AI summaries.
- Upload size, request timeouts, model tokens, and data retention
  (`RETENTION_MINUTES`, default 7 days) are configurable.

## License

TBD.
