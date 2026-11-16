# Agent endpoint, mobile chat rewrite and BYOK — implementation plan

For the coding agent and the auditor. Read first: `AGENTS.md`, `SESSION.md`, `docs/agent-architecture-spec.md` (design and tool table), `docs/pilot-test-findings-2026-11-15.md`.
Start only after Task 2b and Task 3 are finished and tested on a phone. One piece at a time: implement, test (CI plus phone where stated), push, then the next. One commit per piece, pushed without force; do not re-author published commits.

## Order of work

| Task | Scope | Needs new APK |
| --- | --- | --- |
| 2b | Export fixes (`docs/task-2b-export-fixes.md`) | no |
| 3 | OCR no-text pages, empty results (`docs/task-3-ocr-reliability-and-empty-results.md`) | no |
| 4 | Small mobile fixes | yes (APK 1) |
| 5 | Agent backend: tools, loop, route, eval set | no |
| 6 | Mobile chat rewrite on the agent route | yes (APK 2) |
| 7 | Bring-your-own-key | yes (APK 3) |
| later | Accounts, persistent storage, payments | — |

## What the pilot already proved (design inputs, do not re-learn)
- Phrase routing fails. "Summarise vaginal cytology only" was treated as a topic search, flagged "Needs review", and returned "No topic has a single strong source match, so no AI study material generated". A follow-up "Ok give me the original passages instead" got "Could you name a topic, page range, or study action?" because the topic was forgotten. Requirements: the conversation remembers its last topic/result on the server, and an unsure match still produces a best attempt or one specific question, never a refusal.
- Picture-only slides exist (pages 4, 17, 22, 27 of `Test 1.pdf`). They have no text and must be shown as page images, not as failures (Task 3).
- OCR is on demand and capped (5 pages / 20 s per request). Tools must report pending pages instead of hiding them.
- Logs: use `logger.info` lines with ids, counts, seconds and codes only. `LOG_LEVEL` is now applied; Render Logs search works.
- Free tiers allow about 8,000 tokens per minute. Tool results stay small.

## Task 4 — small mobile fixes (one APK)
1. Scanned PDFs: replace "no extractable text was found. Scanned pages need OCR…" (`mobile/app/workspace.tsx` near line 724) with "Scanned PDF added. Ask for a topic and I'll read the pages as needed." Use `ocr_status`/`needs_ocr` from the API, not `sections == 0`.
2. Show file names decoded (`decodeURIComponent` inside a try/catch) in chips and messages.
3. Show app version and the backend commit in Settings. EAS uses `appVersionSource: remote`, so every build reads 0.1.0: switch to `eas build:version:set`/`autoIncrement`, or show `nativeBuildVersion` plus the `/health` commit. Pick one and document it.
4. Show OCR progress text while pages are read ("Reading page 3 of 11") using `GET /documents/{id}/ocr`.
5. Make the study-mode shortcuts visible from the start as example prompts that fill the text box (they appear only after a reply today).
Test: unit tests where logic exists, then the owner installs the APK and repeats the cytology request.

## Task 5 — agent backend (flag `AGENT_ENABLED`, no app change)
Implement exactly the API, tools, loop limits and principles in `agent-architecture-spec.md`. Decisions the spec left open:

### 5.1 Server-side state (replaces client-supplied `state`)
- Tables (new schema version): `agent_conversations(conversation_id, owner_id, last_result_id, last_document_id, last_topics_json, updated_at)`, `agent_results(result_id, owner_id, document_id, kind, payload_json, created_at, expires_at)`, `agent_files(file_id, owner_id, path, name, format, created_at, expires_at)`.
- `result_id` payloads hold references (document id, page numbers, section indexes, topic labels, match scores) and AI summary text. They never copy source text; passages are read from `Section.raw_text` when rendering.
- The request carries `conversation_id` (created on first call). "Export that", "make it shorter", "original passages instead" resolve against `last_result_id`. Every id is owner-checked; rows expire with the retention setting.
- Files are written under the temp dir with retention cleanup and served by an owner-checked download route.

### 5.2 Ambiguous or low-confidence matches
`find_passages` returns per topic: `confidence`, up to 3 candidate page groups with a 120-character snippet. Rules:
1. At or above `MIN_MATCH_SCORE`: use it.
2. Below it: return the top candidates and let the model either use the best contiguous group or ask one question block with the candidates as options. A summarize request on an unsure match summarizes the best candidate and labels it, it does not stop.
3. Never return an empty body as source text: picture-only pages carry `has_page_image` and a "no text on this page" marker.

### 5.3 Outline images
`read_outline_image` returns `topics[]` with a 0–1 confidence and `unreadable_lines`. Zero topics, or any topic below 0.6, ends the turn with a question block that shows the topics for confirmation or editing. Confirmed topics are stored on the conversation.

### 5.4 Whole-document summaries
The agent does not run the full per-section job. `summarize` with `scope=document` summarizes a bounded sample (evenly spaced sections up to about 6,000 tokens) and says "based on N of M pages". The existing study-job endpoints stay for full runs.

### 5.5 OCR inside tools
`find_passages` and `get_pages` run OCR batches internally within the request time limit. If pages are still pending they return `pending_pages` and a warning; the model's reply must mention it. Pages with `ocr_empty` are picture pages, not warnings (Task 3).

### 5.6 Provider tool calling
Add a `ToolCallingClient` interface with a scripted fake for tests and real adapters for Groq (OpenAI-style `tools`) and Gemini (function calling). `AGENT_PROVIDER_ORDER` is separate from the summary order. Verify tool calling on the configured model before relying on it. Never set a small `max_tokens` on reasoning models; bound with call and time caps.

### 5.7 Safety
- Document text enters a model only inside the `summarize` prompt, in clearly delimited data blocks. Tool arguments from the model are validated (Pydantic, ids owned, page ranges bounded).
- Logs: tool name, seconds, outcome code, counts. Never message text, document text or keys.

### 5.8 Evaluation set and tests (build first)
Create `backend/tests/agent_eval/cases.json` from the table in the spec and add these pilot phrases:
- "Summarise vaginal cytology only" → find_passages, summarize
- "Ok give me the original passages instead" (after the case above) → no question; returns the last result's passages
- "Page 2" → get_pages
- "export as word" / "export that" → build_export on the last result
Scripted-model tests run in CI (loop limits, argument errors, ownership violation, injection text in a document, provider timeout, final-reply fallback). A live-model run is an on-demand script. Target: at least 90% expected-tool match before any old routing code is removed.

Definition of done for Task 5: CI green, eval cases pass with the scripted model, and a documented `curl` example the owner can run against Render with `AGENT_ENABLED=true`. State what was not verified.

## Task 6 — mobile chat rewrite (needs APK 2, behind `EXPO_PUBLIC_AGENT=1`)
1. One screen: message list, composer with attach, example-prompt shortcuts, upload progress. One call per user message to `POST /api/v1/agent/chat` with the `conversation_id`.
2. One block renderer: `passages` (fetched by `result_id`, page-image thumbnails for picture pages), `summary` (always labelled "AI summary"), `file` (open/share), `question` (option buttons that send the choice as the next message).
3. Keep the old path as the fallback while the flag is off. Do not delete `chatOrchestrator.ts`/`parseChatIntent` until the pilot week and the 90% target are met, then remove them in a separate commit.
4. Split `workspace.tsx` (about 3,300 lines) as the new screen replaces it; do not extend it.
5. Tests: block renderer and API client with mocked responses; CI `mobile-tests.yml` stays green.
Test with the owner: the phrases in the evaluation table typed into the app, including the two failing pilot phrases.

## Task 7 — bring-your-own-key
- Headers `X-AI-Provider` (allowlist: groq, gemini, deepseek) and `X-AI-Key`, per request only. Never stored, never logged, redacted from exception text and tracebacks. HTTPS only; reject over plain HTTP outside local dev.
- Add the headers to the CORS allowed headers only for configured origins (the native app does not use CORS).
- Provider auth errors map to `ai_key_invalid` with a clear message; quota errors keep their codes.
- Mobile: store the key in `expo-secure-store`, show "stored on this device only", offer remove/replace, and a "test key" button that calls a cheap health check.
- Tests: key never appears in logs (caplog), headers, error bodies or responses; invalid provider name is rejected.

## Later (not now)
Accounts and persistent storage (Render's free disk is wiped on deploy), payments and the paid path, provider/payment decision (owner's), background OCR worker.

## Owner decisions still needed before Task 5 goes live
Provider and plan for the agent; whether low-confidence outlines must always be confirmed (recommended: yes); page/file limits for free and paid paths.
