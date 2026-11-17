# Agent endpoint, mobile chat rewrite and BYOK — implementation plan

For the coding agent and the auditor. Read first: `AGENTS.md`, `SESSION.md`, `docs/agent-architecture-spec.md` (design and tool table), `docs/pilot-test-findings-2026-11-15.md`.
Start only after Task 2b and Task 3 are finished and tested on a phone. One piece at a time: implement, test (CI plus phone where stated), push, then the next. One commit per piece, pushed without force; do not re-author published commits.

## Order of work

| Task | Scope | Needs new APK |
| --- | --- | --- |
| 2b | Export fixes (`docs/task-2b-export-fixes.md`) | no |
| 3 | OCR no-text pages, empty results (`docs/task-3-ocr-reliability-and-empty-results.md`) | no |
| 4 | Small mobile fixes | yes (APK 1) |
| 5 | Agent backend: tools, loop, route, behaviour tests (no phrase table) | no |
| 6 | Mobile chat rewrite on the agent route | yes (APK 2) |
| 7 | Bring-your-own-key | yes (APK 3) |
| later | Accounts, persistent storage, payments | — |

## Owner's rule: no built-in wording, no phrase tests (applies to Tasks 5, 6 and 7)
The agent must understand whatever the user asks, in any wording, language, spelling or order. The owner does not want a table of test phrases, and he does not want the app to choose words for the user.
- No phrase lists, keyword or regex intent matching, and no sample user sentences in the agent code path, the system prompt or the tool descriptions. Describe what each tool does and when it applies; do not list example requests. Any illustration in a prompt uses abstract placeholders (for example "<topic>", "<pages>"), never real topics from the owner's files.
- Do not validate the agent against a fixed phrase table. Validate behaviour: for a fixture (document, conversation state) and a user goal, check the outcome (right tools called with valid arguments, correct content returned, no refusal, no empty file, one question at most when information is truly missing). Wording of the goal varies between runs (see 5.8).
- The existing `backend/tests/agent_eval/cases.json` and any phrase tables in `docs/agent-architecture-spec.md` section 7 are superseded by 5.8. Remove them in the first Task 5 commit.
- Reviewers: grep the agent code and prompts for sample sentences; a hit blocks the task.

## What the pilot already proved (design inputs, do not re-learn)
- Phrase routing fails. A request to summarise one topic was treated as a topic search, flagged "Needs review", and ended with "No topic has a single strong source match, so no AI study material generated". A follow-up asking for the original passages instead got "Could you name a topic, page range, or study action?" because the topic was forgotten. Requirements: the conversation remembers its last topic/result on the server, and an unsure match still produces a best attempt or one specific question, never a refusal.
- Picture-only slides exist (pages 4, 17, 22, 27 of `Test 1.pdf`). They have no text and must be shown as page images, not as failures (Task 3).
- OCR is on demand and capped (5 pages / 20 s per request). Tools must report pending pages instead of hiding them.
- Logs: use `logger.info` lines with ids, counts, seconds and codes only. `LOG_LEVEL` is now applied; Render Logs search works.
- Free tiers allow about 8,000 tokens per minute. Tool results stay small.

## Task 4 — small mobile fixes (one APK)
1. Scanned PDFs: replace "no extractable text was found. Scanned pages need OCR…" (`mobile/app/workspace.tsx` near line 724) with "Scanned PDF added. Ask for a topic and I'll read the pages as needed." Use `ocr_status`/`needs_ocr` from the API, not `sections == 0`.
2. Show file names decoded (`decodeURIComponent` inside a try/catch) in chips and messages.
3. Show app version and the backend commit in Settings. EAS uses `appVersionSource: remote`, so every build reads 0.1.0: switch to `eas build:version:set`/`autoIncrement`, or show `nativeBuildVersion` plus the `/health` commit. Pick one and document it.
4. Show OCR progress text while pages are read ("Reading page 3 of 11") using `GET /documents/{id}/ocr`.
5. Make the study-mode shortcuts visible from the start as neutral action buttons (Task 4c-4); no example sentences.
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
Add a `ToolCallingClient` interface with a scripted fake for tests and real adapters for the configured OpenAI-compatible agent endpoint (default, OpenAI-style `tools`), Groq (OpenAI-style `tools`), and Gemini (function calling). `AGENT_PROVIDER_ORDER` is separate from the summary order. Verify tool calling on the configured model before relying on it. Never set a small `max_tokens` on reasoning models; bound with call and time caps.

### 5.7 Safety
- Document text enters a model only inside the `summarize` prompt, in clearly delimited data blocks. Tool arguments from the model are validated (Pydantic, ids owned, page ranges bounded).
- Logs: tool name, seconds, outcome code, counts. Never message text, document text or keys.

### 5.8 Behaviour tests (build first), no phrase table
1. Scripted-model tests for the mechanics only (loop limits, argument errors, ownership violation, injection text inside a document, provider timeout, final-reply fallback). User text in these tests is neutral filler such as "request A"; the scripted model, not the wording, decides the tool.
2. Fixture-based scenarios in `backend/tests/agent_eval/scenarios.json`. Each scenario holds: a document fixture, conversation state (for example an earlier result), and a goal in abstract terms (for example `goal: original_text_of_topic`, `goal: summary_of_last_result`, `goal: export_last_result`, `goal: pages_range`, `goal: outline_photo_to_topics`, `goal: ambiguous_topic_best_attempt`), with the checks to apply to the outcome. No natural-language sentence is stored as the expected input.
3. Live-model runner (an on-demand script, not CI): for each scenario it asks the model provider to write several different user messages for the goal (varied wording, length, typos, other languages), runs the agent on each, and applies the outcome checks. Report the pass rate per goal and the failures with the generated message. Generated messages are test output only; they are never copied into prompts or code.
4. Target: at least 90% of generated messages pass per goal before any old routing code is removed.

Definition of done for Task 5: CI green, the scripted-model tests and fixture scenarios pass, a live-model run report is recorded in `SESSION.md`, and a documented `curl` example the owner can run against Render with `AGENT_ENABLED=true`. State what was not verified.

## Task 6 — mobile chat rewrite (needs APK 2, behind `EXPO_PUBLIC_AGENT=1`)
1. One screen: message list, composer with attach, neutral action buttons (no example sentences), upload progress. One call per user message to `POST /api/v1/agent/chat` with the `conversation_id`.
2. One block renderer: `passages` (fetched by `result_id`, page-image thumbnails for picture pages), `summary` (always labelled "AI summary"), `file` (open/share), `question` (option buttons that send the choice as the next message).
3. Keep the old path as the fallback while the flag is off. Do not delete `chatOrchestrator.ts`/`parseChatIntent` until the pilot week and the 90% target are met, then remove them in a separate commit.
4. Split `workspace.tsx` (about 3,300 lines) as the new screen replaces it; do not extend it.
5. Tests: block renderer and API client with mocked responses; CI `mobile-tests.yml` stays green.
Test with the owner using his own wording; do not script or suggest the words he should type.

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
