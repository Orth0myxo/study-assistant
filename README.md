# AI Study Assistant

An Android-first mobile app that helps students turn lecture materials (PDF/DOCX) into
organized study notes without manually searching through hundreds of pages.

Upload a document, enter lecture topics (or process the whole material), choose
**verbatim**, **summary**, or **both**, review notes with source page references, and
export as PDF or DOCX.

> **Status: Architecture phase.** This repository currently contains **documentation
> only**. No application code has been written. Implementation starts only after the
> architecture is reviewed and approved (see `docs/implementation-plan.md`).

## Documentation

| Document | Contents |
| -------- | -------- |
| [`docs/architecture.md`](docs/architecture.md) | System design, screens, backend services, parsing, AI grounding, security |
| [`docs/api-contract.md`](docs/api-contract.md) | Endpoints, schemas, errors, configurable settings |
| [`docs/data-model.md`](docs/data-model.md) | Server (temp) and device (persistent) entities, file lifecycle |
| [`docs/implementation-plan.md`](docs/implementation-plan.md) | Stages, gates, assumptions, risks |

## Proposed system

```
mobile/   React Native + Expo + TypeScript (Expo Router) — Android app, local SQLite
backend/  Python + FastAPI monolith — parsing, topic retrieval, AI summaries, exports
docs/     Architecture and contracts
```

- **AI processing** requires internet. Saved notes remain readable offline.
- **AI provider credentials live on the backend only** (environment variables). They are
  never bundled in the mobile app.
- Scanned PDFs (no extractable text) are detected and reported, not OCR'd.

## Proposed repository structure

```
ai-study-assistant/
├── mobile/                      # React Native + Expo + TypeScript
│   ├── app/                     # Expo Router screens
│   │   ├── index.tsx            # Home
│   │   ├── upload.tsx           # Upload
│   │   ├── topics.tsx           # Topics & mode
│   │   └── results.tsx          # Results
│   ├── components/
│   ├── services/
│   ├── types/
│   └── package.json
├── backend/                     # Python + FastAPI
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   ├── document_parser.py
│   │   │   ├── topic_finder.py
│   │   │   ├── summarizer.py
│   │   │   └── exporter.py
│   │   └── config.py
│   ├── tests/
│   └── requirements.txt
├── docs/
│   ├── architecture.md
│   ├── api-contract.md
│   ├── data-model.md
│   └── implementation-plan.md
├── .env.example
├── .gitignore
└── README.md
```

## Proposed stack

| Layer | Technology |
| ----- | ---------- |
| Mobile | React Native, Expo, TypeScript, Expo Router |
| Mobile persistence | Expo SQLite |
| Backend | Python, FastAPI, Uvicorn |
| PDF parsing | PyMuPDF |
| DOCX parsing | python-docx |
| AI | Configurable provider API (server-side key) |
| Export | ReportLab (PDF), python-docx (DOCX) |

## Setup (planned — Stage 1)

> Not yet runnable. Provided as the intended setup once implementation begins.

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
copy ..\.env.example .env      # then fill in AI provider values
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Health check: `GET http://localhost:8000/health`

### Mobile

```bash
cd mobile
npm install
npx expo start
```

Then run on an Android emulator or device (`a` in the Expo CLI).

Set the backend base URL in the app settings to your machine's LAN IP
(e.g. `http://192.168.1.10:8000`) so the emulator/device can reach it.

## Configuration

All secrets and limits are environment-driven (see `.env.example`). The mobile app
never contains provider keys.

## Constraints

- No accounts, cloud sync, payments, admin dashboard, microservices, vector DB, or
  background-worker infrastructure in the MVP.
- Upload size, request timeouts, model tokens, and temp-file lifetime are configurable.

## License

TBD.
