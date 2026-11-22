# Agent guide

Instructions for Codex, OpenCode/DeepSeek, and other coding agents working in this repository.

## Inspect before changing anything

At the start of every task, inspect `git status --short --branch`, the current branch and remotes, recent commits, and relevant diffs. Read the relevant project instructions and source before editing. Identify user changes and preserve them. Never use `git reset`, `git clean`, checkout-overwrite, force-push, or broad revert operations to make the tree convenient. Ask before replacing or removing user work.

Before committing or synchronizing, review the complete staged diff and verify that secrets, local configuration, caches, virtual environments, and generated artifacts are excluded. Fetch and inspect remote changes before pushing; do not rewrite published history.

## Project architecture

- `mobile/`: Android-first React Native app using Expo SDK 57, TypeScript, and Expo Router. Screens live in `mobile/app/`; reusable UI in `mobile/components/`; API and routing helpers in `mobile/services/`.
- `backend/`: FastAPI monolith. Routes are in `backend/app/api/`, Pydantic request/response models in `backend/app/schemas/`, and parsing, study processing, AI providers, storage, matching, and export code in `backend/app/services/`.
- `docs/`: architecture, API contract, data model, implementation plan, security review, and architecture audit.
- SQLite and uploaded files are local backend state, normally under `backend/.tmp/`. They are not synchronized by Git. A deployment needs persistent storage if that state must survive restarts.

The backend parses PDF/DOCX documents and owns deterministic extraction, matching, validation, persistence, and file generation. Exact passages must come from stored `Section.raw_text`; AI output must never be labeled or returned as verbatim source. Groq command interpretation receives a bounded user message and document metadata, not document contents or chat history. Summaries must be grounded in extracted source. Provider errors should retain their actual classification.

The app has a chat workspace, study jobs, deterministic range extraction, and a separate photo-jottings/topic-review flow. Current note-image transcription uses bounded local Tesseract OCR. Do not assume lecturer-outline vision interpretation, saved/exportable photo-topic results, or document editing exists; check code and `SESSION.md` before making capability claims.

## Coding and change conventions

- Keep the established FastAPI + Pydantic and Expo + TypeScript architecture. Prefer focused changes in existing services and routes. Do not add frameworks, dependencies, database systems, workers, or broad refactors without a concrete need and user approval.
- Preserve API schemas and mobile/backend behavior unless the requested change requires an explicit contract update. Keep source extraction deterministic and AI summaries grounded.
- Add focused regression tests for changed behavior. Run the relevant complete suite when requested or when a change affects shared behavior. Report commands and actual results; never infer that a test passed because it was added.
- Keep secrets in ignored local environment files or secret stores. Never copy API keys, tokens, personal documents, or local `.env` contents into Markdown, logs, commits, or prompts unnecessarily.
- Keep `SESSION.md` current at meaningful handoff points: record branch/commit, completed work, known limitations, test outcomes, and the next useful action.

## Dependencies

Backend runtime dependencies are declared in `backend/requirements.txt`: FastAPI/Uvicorn, Pydantic settings, multipart parsing, HTTPX, PyMuPDF, and python-docx. Test dependencies are in `backend/requirements-dev.txt` (currently pytest and pytest-asyncio).

Mobile dependencies and scripts are declared in `mobile/package.json`; `mobile/package-lock.json` is authoritative for npm installs. The app uses Expo SDK 57, React Native, Expo Router, TypeScript, Vitest, and Expo modules for file/image selection, secure storage, downloads, and sharing. Do not edit generated Android/iOS directories or dependency lockfiles without a dependency change.

## Setup and checks

### Windows backend

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item ..\.env.example .env  # optional; fill provider keys locally only
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Run backend tests from `backend/`:

```powershell
python -m pytest
```

### Windows mobile app

```powershell
cd mobile
npm ci
Copy-Item .env.example .env.local
npm test
npm run typecheck
npm run lint
npm start
```

If PowerShell blocks `npm.ps1`, invoke `npm.cmd` instead. The mobile `.env.local` file is local-only. Set `EXPO_PUBLIC_API_URL` there when automatic backend host detection is unsuitable. Expo SDK 57 upload behavior requires `EXPO_PUBLIC_USE_RN_FETCH=true` as shown in `mobile/.env.example`.

### Termux on Android

Use a 64-bit Android/Termux installation. Install Git, Python, and Node.js using Termux packages, clone the same branch, then install backend and mobile dependencies using the commands above (`python -m venv .venv`, `pip install -r requirements-dev.txt`, and `npm ci`). Keep `.env` and `.env.local` device-local. If the backend and Expo Go run on the same phone, configure the app to reach the backend at `http://127.0.0.1:8000`; if the backend runs on another device, use its reachable LAN address and bind the backend to `0.0.0.0`. Physical-device networking and native build support can vary by Android/Termux environment.

### OpenCode/DeepSeek and Codex

Both tools must follow this file, inspect the existing branch and worktree before edits, and update `SESSION.md` before handing the project back. Configure OpenCode/DeepSeek provider credentials in OpenCode's local user configuration or environment; configure Codex through its local supported settings. Never put either provider's key in the repository. Do not assume one tool's local configuration, installed skills, or credentials exist on the other device. GitHub synchronizes committed source and documentation, not local secrets, databases, uploads, caches, or uncommitted edits.

For normal synchronization, commit reviewed work on the current branch, fetch and inspect the corresponding remote branch, then push without force. On the other device, fetch and fast-forward/pull that branch before continuing; do not run two independent histories or force-push to resolve divergence.
