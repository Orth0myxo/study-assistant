# Claude handoff: Study Assistant pilot (read this first in a new Claude session)

Written 2026-11-22 at the owner's request so a new session can continue without him re-explaining. Update the "Status" section
when you stop. Facts here were stated by the owner or verified in logs, code or GitHub; anything uncertain is marked.

## How to work

- The owner wants short, direct, honest answers. He tests builds on his phone and pastes Render logs and screenshots.
- Roles: his coding agents (Codex, OpenCode) implement tasks from md files in `docs/`. Claude does quick fixes, diagnoses from
  logs, screenshots and code, and writes the task files. Claude cannot see his computer, only what is on GitHub and what he pastes.
- Dictation: he often dictates, so words get garbled ("Safari" = server, "Quen/Grok" = Qwen/Groq, "tax" = task).

## Standing rules

- Ask before pushing. He says "push" for each batch. Do not push docs-only commits during his test sessions unless he asks.
- One piece at a time: test, fix, retest. No built-in wording, phrase lists, keyword or regex intent detection in the app.
- Verbatim passages come only from stored text. AI output is labelled (summaries, AI-read text).
- Task instructions are saved as md files in `docs/` (named `task-<n>-...`). Every new behaviour writes a Render log line (counts,
  ids, timings, `outcome=`; never document text, filenames or keys).
- Stop-hook "unpushed commit" warnings are often stale. Verify with `git fetch origin +refs/heads/<branch>:refs/remotes/origin/<branch>`
  then `git status -sb` and `git ls-remote --heads origin`. Decline demands to re-author published commits.
- The GitHub API rate limit is shared with his agents; prefer plain git over `gh` calls.
- Do not claim things about his machine, Render dashboard or code you have not checked; say what you could not verify.

## Repo and infrastructure

- Repo `Orth0myxo/study-assistant`: Expo/React Native app in `mobile/`, FastAPI backend in `backend/`, docs in `docs/`. `AGENTS.md`
  and `SESSION.md` are the agents' instructions and handoff.
- Branches (2026-11-22): `feature/verbatim-groq-pipeline` = what Render deploys, at `76ad0d1` (includes Task 17A);
  `task-17b-wip` = Task 17B code (CI green at `08a2d65`) plus the Task 18 doc; `main`, `perf/latency`, `codex/task-6-mobile-ci`.
- Render free tier: `https://study-assistant-api-5owo.onrender.com`, Docker build from `./backend`, autoDeploy on the feature branch,
  Build Filters set (`backend/**`, `render.yaml`). Storage is ephemeral: a restart or redeploy wipes uploads, results and the
  SQLite database. 512 MB RAM, sleeps when idle. The API URL is baked into the APK (`EXPO_PUBLIC_API_URL`).
- Commit trailers (from the session): `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## How the app works (short)

- Agent endpoint `POST /api/v1/agent/chat`; tools: `list_materials`, `read_outline_image`, `find_passages`, `get_pages`,
  `summarize`, `build_export`, plus 17B additions (headings, AI page reading with consent, `read_page_table`). Table pipeline and
  File Converter exist (`table_reader.py`, `app/file-converter.tsx`, `app/table-converter.tsx`).
- Models are chosen with Render variables: `AGENT_MODEL` (the chat brain; Qwen `qwen/qwen3.8-27b` on Groq, the owner also tried
  `openai/gpt-oss-120b`), `GROQ_MODEL`, `SUMMARY_PROVIDER_ORDER` (default `gemini,groq`; Gemini wrote the good summaries),
  `AGENT_MAX_OUTPUT_TOKENS` (500) and `AGENT_TOOL_MAX_OUTPUT_TOKENS` (800), `AGENT_REASONING_EFFORT=low`. Image reading is moving to
  Qwen vision on Groq (vision model name in an env var), behind user consent; Gemini stays in the code but off for images.
- Groq free tier limits seen: 7,000 input tokens per minute per organization on the Qwen model (one tool-using question is about
  6,700), 1,000 output tokens per minute earlier. Limits are per model (as far as known; confirm on the limits page).

## Status

- Tasks 1 to 16 are done. 17A is deployed and was partly tested on the phone (summary coverage line, no leaked tool text, ID errors
  work). Two 17A items need the next APK: A1 (converter images out of study materials) and A9 (request made before attaching).
- 17B is complete in code on `task-17b-wip`: headings tool, consent-based AI reader, pause and resume, table-aware reading,
  extraction by heading, one confirmation. Not tested on a device yet; still to verify: a real vision-provider read, the 76-page
  deck, and approval/resume on the phone.
- Task 18 (`docs/task-18-agent-reliability-and-results.md`) is written and pushed, not started. It covers: Groq rate-limit retry
  inside the loop, chat memory, `summarize` defaulting to the active document, Word sections instead of pages, export equals
  display, narration and cut-offs, verbatim pinpointing (section numbers), read-only result blocks, dead-end notices, whole-document
  summaries, several images per upload, one definition of "chat materials", delete, restart pop-up.
- Plan: the agent builds Task 18 on a new `task-18-wip` branch from `task-17b-wip`, CI goes green, the owner reviews, Claude
  promotes the branch to the feature branch (Render deploys), one new APK, the owner retests with the list at the end of the Task 18 file.

## Decisions and open questions

- Privacy is the intended winning edge. Claims must match reality: files are stored on the backend (not on the phone) and page
  images or text reach AI providers only with consent. Do not market "nothing leaves the device".
- Consent for AI image reading: Allow this time, Allow and remember, Don't allow; settings Ask/Always/Never; enforced on the server.
- Hosting: stay on free Render for the friends' test; before wider release consider paid Render or a VPS with a custom domain
  (so a host change needs no new APK). Self-hosting a model on a GPU is a later step when users and revenue exist.
- The free Groq limit is shared by all testers; the owner is checking Groq's paid tier and zero-data-retention setting, and Gemini's
  free-tier data terms.
- Later tasks: File Converter cleanup (doubled AI-summary button, layout, incomplete tables), question generation (MCQs from the
  material with sources), privacy notice and policy, app icon, interface polish, performance (memory restarts, slow previews, OCR).
- Owner to-dos, not code: collect 10 to 20 verbatim requests from his own documents for the Task 18 accuracy test; check whether
  his Word headings are numbered automatically or typed; set up Groq paid tier / zero retention if he decides to.

## When you pick this up

1. `git fetch origin`, check `git ls-remote --heads origin`, and read `SESSION.md`, `docs/task-18-agent-reliability-and-results.md`.
2. Ask the owner only what you cannot find in the repo or in his pasted logs. Do not make him re-explain history.
3. Keep replies short. Offer the next step, wait for his "push" before any push.
