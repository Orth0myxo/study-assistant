# Development session handoff

Updated: 2026-11-21

## Task 17B — B1 headings tool (local review pending)

- Pulled `feature/verbatim-groq-pipeline` with `git pull --ff-only`; it fast-forwarded from `fa02c2c` to `76ad0d1`, which adds only `docs/task-17b-document-reading.md`.
- Implemented B1 locally in `backend/app/services/agent_service.py`: `list_headings` is a no-AI agent tool; PDF bookmarks are read from the owner-scoped stored PDF on demand, otherwise stored PDF font structure is used; DOCX headings use stored Word style structure. The result identifies its source and computation mode, keeps Word page numbers explicitly unavailable, and returns page image numbers when present. The agent prompt routes document topic/heading requests to this tool; `read_outline_image` remains for attached topic-list images.
- Added `backend/tests/test_agent_headings.py` cases for a 16-page bold-heading PDF, bookmark precedence, DOCX heading styles, no headings, safe logging, and prompt/tool registration. Backend tests have not been run; prior project handoff requires backend test verification through GitHub Actions, while the owner explicitly asked not to push before review. No dependencies were installed and no test suite was run locally.
- Local checks: `python3 -m py_compile backend/app/services/agent_service.py backend/tests/test_agent_headings.py` and `git diff --check` passed after the final edits. Backend tests were not run locally per the GitHub Actions-only verification restriction.
- Owner changed the provider decision: use Qwen through Groq by default for image reading, retain Gemini as an explicit configured option, and do not change summary providers. Checked current Groq official docs: Qwen 3.8 accepts base64 data URLs in Chat Completions, allows up to 3 images and 20 MB per image request, and charges 2,048 input tokens per image. The published 8K TPM limit is the Developer-plan baseline; exact Free-tier limits are account-specific and remain unverified.
- B2 remains in progress locally. Added a per-user `ask|always|never` preference (schema v20), authenticated preferences API, common provider adapter (`IMAGE_READING_PROVIDER`, default `groq`; `GROQ_VISION_MODEL` from environment), preserved Gemini adapter, and a Groq Qwen implementation with one image per provider request, 14 MiB raw image guard, 1,024 completion-token cap, an in-process rolling estimated 7K TPM budget, and specific logs/errors for rate limits, oversized images, and models that reject images. No dependencies were installed.
- Wired provider selection and consent into converter table reads, topic-outline reads, and the agent's scanned-page/outline tools. Added page transcript persistence and an owner-scoped edit endpoint; the agent chat can display the AI-read label and page image beside editable text. Under `ask`, agent image tools return `needs_approval`; the pause/resume UX and server-side resume flow are still B3 and are not implemented. Outline reading currently handles the first two images and reports omitted images; table AI reads are capped at two images per request.
- Added focused backend/mobile regression tests and updated `docs/api-contract.md`. No backend/mobile tests or CI workflows have run. The only checks before this handoff were syntax and diff checks for an earlier, smaller state; they have not been rerun after the Groq changes. The new code needs review, syntax checks, and GitHub Actions after owner review. B3–B6 have not started. Keep implementation changes local; do not push.

## Task 17A — agent fixes before the friends test

- Branch `feature/verbatim-groq-pipeline`, based on `16f3c31`. Task 17A is committed locally in `a0f8c9e`; `b5b4102` fixes the detailed-summary fixture. The commits were pushed only to temporary CI branch `task-17a-ci`; `origin/feature/verbatim-groq-pipeline` and production remain at `16f3c31`.
- User restriction: do not install backend/mobile dependencies or run test suites locally. Verification must use GitHub Actions. Only `python3 -m py_compile` and `git diff --check` have been run locally; both passed.
- Implemented A1–A9:
  - A1: schema v19 adds `topic_sources.purpose` (`study` default, `table` for converter uploads); converter/legacy converter sources are excluded from agent materials, outline reading, study-source lists, and stale active selections.
  - A2: `list_materials` returns only the material selected in the current chat, or an explicit no-material note; it no longer reads every stored document/source.
  - A3: leaked tool-call markup is detected structurally, logged without arguments, retried once, and replaced by one plain retry message if repeated.
  - A4: PDF/DOCX topic requests use `get_pages`/`find_passages`; the system prompt forbids asking for a clearer image for a document, and `read_outline_image` is limited to attached topic-list images.
  - A5: materials expose explicit `document_id`/`source_id` with `id_kind`; wrong-kind IDs return actionable errors and log the kind.
  - A6: `summarize(length=detailed)` chunks long documents, reports part/page coverage, avoids the 80/150-word cut, and displays the full summary while truncating only model history.
  - A7: OCR warnings are one plain sentence for requested pages only, while logs retain page numbers and failure kinds.
  - A8: PDF upload logs bounded per-page native/image/OCR metrics and thresholds, never text.
  - A9: the existing queued-prompt rerun is extracted to `takePendingRequest` and logs `pending_request outcome=ran`; no device verification.
- Added `backend/tests/test_agent_17a.py`, markup regressions in `backend/tests/test_agent_loop.py`, updated OCR expectations in `backend/tests/test_agent_blocks.py`, schema-version assertions to 19, mobile pending-request tests, and a converter-purpose API test.
- Updated `docs/api-contract.md` and `docs/data-model.md` for source `purpose`, selected-only materials, explicit IDs, and detailed summaries.
- GitHub Actions: Mobile checks `44896548305` passed on `a0f8c9e`. Backend tests `44896548334` failed only `test_detailed_summary_uses_all_text_chunks_without_word_cut` because the fixture stayed under the 12,000-char chunk default; `b5b4102` bounds `max_summary_chunk_chars` to 300, and Backend tests `44897219394` passed all 621 tests on `b5b4102`.
- Not verified: real PDFs for A4/A6, live provider behavior, and Android queued-request behavior. No Android device/ADB.
- Next: review the two commits and `task-17a-ci`, then decide whether to fast-forward the feature branch and deploy, or proceed directly to Task 17B after review. Do not build the single APK until 17B is complete.

## Tasks 15–16 — saved chats, tables, and File Converter

- Branch `feature/verbatim-groq-pipeline`, in sync with origin at last check; last commit is `c66d4eb`. Task 15/16 landed in `608e88f`; `c66d4eb` fixes the new active-source route test to upload a real PDF instead of using an unattached fake id. Production `/health` reports `c66d4eb`.
- Task 15 is implemented: saved chats with a versioned on-device index and per-chat files, migration of the old single snapshot, rename/delete/load/save bounds; markdown table rendering in chat, preview, and DOCX/PDF export; active-source handoff from the app's source chips; and the duplicate outline-review prompt fix.
- Task 16 is implemented: standalone `/file-converter` screen with registry-driven input/output choices, background jobs, polling, sheet selection, editable summary tables, AI consent, preview, export, and share. `/table-converter` remains the photo/text table step and is linked from the new screen.
- Backend Task 16 adds `SCHEMA_VERSION = 18`, `conversion_jobs`, `/api/v1/convert/formats`, job start/status/export/file routes, a TTL store and sweep, and the conversion engine for CSV/XLSX, CSV/XLSX DOCX tables, Word tables to Excel, text/Word to PDF, PDF to Word, Word to PDF, and AI summary tables to Excel/Word.
- Conversions use 25 MB, 5,000 rows, 200 columns, 5,000 cell characters, 50 PDF pages, and 20 Word tables. Formula-leading cells and cells beginning with a tab or carriage return are neutralized. Multi-sheet XLSX requires an explicit sheet. AI summary tables use `use_ai=true` consent, structured rows, and caps of 20 rows, 6 columns, and 160 characters.
- One heavy conversion at a time is enforced per process with a semaphore. The normal `RETENTION_MINUTES` window governs conversion inputs, outputs, and rows; there is no separate short TTL.
- Local verification: backend `python -m compileall -q backend/app backend/tests` and `git diff --check` pass. Mobile `npm test -- --run` passes 121 tests across 13 files; `npm run typecheck` passes; lint has 0 errors and the existing unused `commandFromLocalIntent` warning.
- Backend pytest is still unavailable locally (`pytest`, `pydantic`, `pymupdf`, `openpyxl`, and `fastapi` are missing). Backend Actions `44733055609` failed on `608e88f` because the new route test used an unattached fake document; `c66d4eb` fixed the test and Backend Actions `44734540670` passed. Mobile Actions `44733055707` passed on `608e88f`; the test-only follow-up did not trigger mobile checks.
- Preview APK build `4f0f04e5-82bc-4a4b-a45d-48648bf38bf2` finished from `c66d4eb`, versionCode 23: `/storage/emulated/0/Download/Study-Assistant-preview-v23-c66d4eb.apk`, 43,356,472 bytes, SHA-256 `a8948be885caaf334214cd0ea4eb1d36c28cb5701c5176cf5191edd42e9b75bc`. ZIP integrity check passed.
- No Android device/ADB is available, so picker behavior, sheet prompt retry, table editing, preview, OCR, sharing, and deployed exports remain device-unverified.
- Final review hardening: switching the active source now clears stale result/topic context, while same-id image/outline kind labels keep it (`test_switching_active_source_clears_stale_result_context`, `test_active_source_kind_rename_keeps_same_source_context`); blank detected Word/PDF tables are skipped instead of failing the whole conversion (covered in the Word-table regression); the v1 migration test asserts `conversion_jobs` exists.
- Converter jobs now log outcome, conversion, input/output kinds, input/output bytes, elapsed seconds, and a failure code only; file names and cell text are excluded (`test_conversion_logs_metadata_without_filename_or_cells`). This supplies the free-tier timing data requested by Task 16 without logging content.
- Known risks: `_read_xlsx` uses `worksheet.max_row`/`max_column`, so sparse or format-heavy workbooks can be rejected as too large; the conversion semaphore is per-process, not global across workers.
- Next: install the v23 APK and verify saved chats, markdown tables, active-source switching, File Converter picker/sheet prompt/table edit/preview/share, and live AI summary consent against the deployed backend.


## Tasks 13–14 — photo to table, edit and preview results

- Branch `feature/verbatim-groq-pipeline`; Task 13 commits `702cea1`, `41ed117`, `547745a`; combined Task 13 follow-ups and Task 14 are in `32e362f` (source + `docs/api-contract.md`); this handoff records verification.
- Photo-to-table now supports multi-image input (`image_indices`), typed/copied text through `POST /api/v1/tables/text`, an optional title above the table, and an explicit `has_header` flag. Local grouping derives the title/header from geometry without fixed wording lists.
- Mobile keeps an editable title and header toggle, highlights low-confidence cells, and adds a table preview modal that renders the current title, columns, and rows before export. Preview is local; edits remain unsent until export.
- Agent result edits are phone-side until preview/export: AI summary text is editable; stored source passages are read-only but can be removed, restored, reordered within their topic, or filtered by page. Unknown or foreign passage ids return `422 agent_edit_invalid`.
- `GET /api/v1/agent/results/{result_id}` now exposes stable `passage_id` values. `POST /api/v1/agent/results/{result_id}/preview` renders the same payload as export and returns bounded blocks (`limit` default 12, max 40). Export accepts the same `edits` object. The exporter honours `_preserve_passage_order` only when the user reorders.
- Added backend regressions for removal, restore/reorder preview, summary override in preview and DOCX, unknown ids, owner isolation, and export-scoped edits. Mobile tests cover edited export bodies and the preview endpoint.
- Local verification: `python -m compileall -q backend/app backend/tests` and `git diff --check` passed. Backend pytest remains unavailable locally (no pytest/pydantic), so backend execution is CI-only. Mobile: 114 tests passed across 13 files, typecheck passed, lint has only the existing unused `commandFromLocalIntent` warning.
- GitHub Actions on `e2885d3`: Backend tests `44368118513` and Mobile checks `44368118536` both passed.
- Preview APK build `f594eeb1-8e65-40e2-affc-f35e2c579863` finished from `e2885d3`, versionCode 20: `/storage/emulated/0/Download/Study-Assistant-preview-v20-e2885d3.apk`, 43,303,436 bytes, SHA-256 `3646b45ff4b34debb375a3d318fdec6908beb11cfe9fdcec529d25bc91734a8f`. ZIP integrity check passed. Production `/health` reports commit `e2885d3`.
- Not verified: real photo/list OCR accuracy with multiple photos, Gemini table reading, deployed XLSX/DOCX export, and Android UI behavior for editing, previewing, and restoring passages. No Android device/ADB is available here.
- Next: install the v20 APK, exercise a real photo/table through edit and preview, then run an agent result through summary/passage edits, preview, and export.

## Task 11 — upload speed, Groq output limits, honest agent errors

- Branch `feature/verbatim-groq-pipeline`; commits `7606829` (`perf: detect PDF tables only when requested`), `ec30c60` (`fix: bound agent output and classify provider failures`), `7411e96` (`docs: document agent output limits and deployment restarts`), `057eef5` (`test: update schema version for table detection state`), owner docs `7cc7442`/`b8487ed`, and `2ec4ee1` (`fix: split agent output caps and report truncation`). All source commits are pushed.
- Part A: PDF upload no longer calls `find_tables()` on every page. Schema v14 adds `document_pages.tables_checked`; PDF pages start unchecked, DOCX/default pages start checked. `storage.ensure_page_tables()` detects only requested pages under a lock, stores rows and the checked flag, reuses them after reload, and topic continuation/export/agent paths detect on demand. Existing non-empty table rows are marked checked during migration. Upload logs now include `table_ms` and `tables_found` (0/0 because table work is deferred).
- Part B: `AGENT_REASONING_EFFORT=low` by default (blank omits it), `AGENT_MAX_OUTPUT_TOKENS=500` for calls without tools, `AGENT_TOOL_MAX_OUTPUT_TOKENS=800` when tools are offered, and optional `AGENT_OUTPUT_TOKENS_PER_MINUTE`. OpenAI-compatible calls send the applicable `max_completion_tokens`; Gemini sends `maxOutputTokens`; local OTPM reservations use the same call-specific cap. Startup logs provider order, agent/Groq hosts and models, and reasoning effort without keys.
- C1: the agent system prompt is built at request time from both active output caps and the reasoning setting. It tells the model to keep answers short, treats tool arguments as output, and lets backend tools return passages, exports, and summaries. OpenAI `finish_reason=length` and Gemini `MAX_TOKENS` become `status=agent_output_truncated`; the partial text is kept, no blind retry/provider fallback occurs, and the app adds its own cut-off warning.
- Part C: provider failures are classified as `agent_rate_limited` (429, with `retry_after_s`/rate-limit details), `agent_model_not_found`, `agent_provider_auth_failed`, `agent_provider_unavailable`, `agent_provider_error`, or `agent_timed_out` (504). The loop stops on 429/401/404/timeout, retries transient 5xx once, and returns one neutral warning instead of blaming request wording. Mobile maps codes to its own text, shows a retry wait when known, and deduplicates warnings.
- C0: every agent call is reserved against a process-local output-token/minute tracker using `AGENT_OUTPUT_TOKENS_PER_MINUTE`; response `completion_tokens`/`candidatesTokenCount` settle the reservation. A missing usage field releases the reservation rather than falsely consuming the budget. The tracker is not shared across workers, so it is a per-process guard, not a strict organization-wide cap.
- Part D documented in `docs/architecture.md`: Render redeploys on every push, including docs-only pushes, and restarts in-flight uploads/ephemeral storage; exclude `docs/**` with a build filter. The `.env.example` and agent/API docs now cover both caps, truncation status, Groq `qwen/qwen3.8-27b`, the Groq base URL, reasoning effort, and failure codes. Older “do not set max_tokens” notes are marked superseded.
- Verification: Backend Actions `44224970278` failed only six stale schema-version assertions; `057eef5` fixed them and `44225453225` passed. After C1, Backend Actions `44255678452` and Mobile Actions `44255678390` passed on `2ec4ee1`. Local mobile: 98 tests passed, typecheck passed, lint has only the existing unused `commandFromLocalIntent` warning. Local backend syntax checks and `git diff --check` passed; backend pytest remains unavailable locally.
- Measured upload: the CI synthetic 53-page PDF now uploads with `parse_ms=13.0`, `total_ms=14.7`, and `table_ms=0.0` (pre-change evidence: the owner's real 53-page PDF had `parse_ms=353769.7`). The real owner PDF is not in this checkout, so its post-change table/page detection timing is not measured. The fixed C1 agent prompt plus tool schemas is about 3,673 JSON characters, roughly 919 estimated tokens (761-character system prompt, 2,912-character tool schemas).
- Not verified: live Groq tool calling/truncation, real 53-page PDF, phone behavior, background starvation under load, and cross-worker OTPM enforcement.
- APK: preview build `fb27a27f-a3e6-4944-9420-e6f359bd9de4` finished from `2ec4ee1`, versionCode 18: `/storage/emulated/0/Download/Study-Assistant-preview-v18-2ec4ee1.apk`, 43,247,360 bytes, SHA-256 `08c7da1d72464d9bd937dde41023ef23be725f7261527ea8d2e00f1bb0599bda`. The first download was truncated by a timeout (43,024,384 bytes) and could not install; it was re-downloaded to local storage and verified before copying to Download. This supersedes v17 from `057eef5`. No Android device/ADB is available here.

## Task 10 — legacy workspace UI on the agent route

- Implemented on `feature/verbatim-groq-pipeline` and pushed as `613c359` (`feat: restore legacy workspace UI on agent route`). The agent screen is now the default `/workspace`; the legacy screen is used only for saved sessions or after the backend returns `agent_disabled`.
- Restored the legacy header, serif title, Settings link, empty state, source bar, single composer, “Add to your study” sheet with document/photo rows, and history drawer. Preset chips and `ACTIONS` are gone; the app now sends the user's text unchanged.
- Removed `EXPO_PUBLIC_AGENT` from the preview build and deleted `welcomeActions.ts` plus its test. Added tests for unchanged user text, the agent API body, and the absence of welcome/preset exports from the services used by the screen.
- Mobile checks `44214190459` passed on `613c359`. Local checks: 97 tests passed, typecheck passed, lint passed with only the existing unused `commandFromLocalIntent` warning.
- Preview APK build `d8309154-c6ac-4f43-8fa5-185959f31a13` finished from `613c359`, versionCode 16: `/storage/emulated/0/Download/Study-Assistant-preview-v16-613c359.apk`, 42 MB, SHA-256 `a6a93d7c1f03162da729748b6c111309d91dd9bd033de374b82a41e04ce6417a`. Production `/health` reports commit `613c359`.
- No Android device/ADB was available here, so the legacy header, empty state, attach sheet, composer, and history drawer are code/CI verified only, not device verified. Next: owner installs the APK and checks the old-interface elements and an agent reply.

## Task 6 — mobile agent chat (fixes from owner audit)

- Started from `4ed9f6e` on `feature/verbatim-groq-pipeline`; the worktree was clean. The implementation plan's Task 6 scope is active per the owner's request.
- Added a separate `AgentWorkspaceScreen`, selected only when `EXPO_PUBLIC_AGENT=1`; the existing workspace remains the off-flag fallback and `chatOrchestrator.ts`/`parseChatIntent` remain intact.
- The new screen supports PDF/DOCX uploads with ingest progress, independent image attachments, neutral action controls, server-issued conversation ids, one agent chat request per submitted message, and passages/summary/file/question blocks. Passage results and authenticated page thumbnails/files use the existing owner-scoped API routes.
- Owner audit fixes: expired conversations reset their id and explain recovery; API/client guards enforce the 5,000-character and 20-attachment limits; summary blocks display their sampled-source basis; downloads wake Render and retry once after stale identity; the screen moved under `mobile/components/` so Expo Router does not expose it as a separate route.
- Added API and screen-workflow tests for document/image attachment, result/page-image loading, conversation reset, file sharing, request limits, and authenticated download retry. Local mobile checks: 97 tests passed, TypeScript passed; lint has only the existing `commandFromLocalIntent` warning. No dependencies were installed.
- Task 6 audit fixes were pushed in `46a66ff` as part of the fast-forward branch; handoff commit `1ab4d4a` records verification. Mobile checks `44185624786` passed. Fresh preview APK build `95a9b512-da80-456e-bda1-51fc463e4851` finished from `1ab4d4a` (versionCode 14): https://expo.dev/artifacts/eas/RPwacRYnKrxGx-ezT6AFhoTuuf_CK4bPOgOnGq5_XwM.apk.
- The remote feature branch's Task 8 export/docs commits were fetched and merged without rewriting history. Merge commit `d31ae36` includes remote tip `1a61653`; the worktree is clean and five commits ahead of the remote before this handoff update.
- Historical baseline: GitHub Mobile checks `44111545705` passed on `b3d8d1e`, and APK build `6cafc925-a169-4357-ba6f-dc24ea60da4e` finished from `bbe6ca3` (versionCode 13); both predate the audit fixes and are superseded by the checks/build recorded above.
- Owner confirms audit finding 2 (production agent prerequisites and verification) is complete. No backend source changed for the Task 6 mobile audit fixes. The pre-existing Task 5 note below records the state before that owner confirmation.
- Final local checks on the merged tree: `npm test -- --run` passed (97 tests / 12 files), `npm run typecheck` passed, and `npm run lint` passed with the existing unused `commandFromLocalIntent` warning. No dependencies were installed. Backend tests remain delegated to GitHub Actions.
- Task 8 Part B is implemented and pushed: PDF table rows/cells are stored separately from verbatim page text (schema v13), DOCX exports use bordered tables with repeated headers and unsplit rows, and PDF exports draw wrapped grids. Backend CI passed on extraction/storage (`44188100121`), DOCX export (`44189405844`), and final PDF renderer (`44192129931`). Two intermediate PDF CI runs failed while the renderer was being corrected (`44190024335`, `44190607511`, `44191349254`); the final run passed.
- Task 8 preview APK build `43856103-053c-445e-9e67-174f7a5b773b` finished from `fdb2420`, versionCode 15: `/storage/emulated/0/Download/Study-Assistant-preview-v15-fdb2420.apk`, 42 MB, SHA-256 `59b33647e85812afda717e66eb739390bb144786e647514773cb4b31c7a35170`. Production `/health` reports commit `fdb2420`, so the backend table extraction and export code is deployed for testing with this APK.
- Task 8 Part A is implemented in `ce19c54`: agent passage matching and topic-source review extend a matched topic across adjacent tables using repeated headers, sequential row numbers, or matching top-of-page table geometry, stopping before a new heading. The synthetic multipage table regression passed in backend CI `44204033487`; the local syntax-only compile and `git diff --check` also passed. Backend tests were run only in GitHub Actions.
- An additional synthetic end-to-end regression carries a detected table from PDF upload/storage through both DOCX and PDF exports. GitHub Actions backend run `44205567214` passed on `b20ff70`.
- The owner's original 5-page stains PDF is not present in this checkout. The synthetic test verifies pages 1–3 are returned and page 4 stops at an unrelated heading, but the real-file table shape and export still need checking when the PDF is supplied.
- Next: run the owner's stains PDF through extraction, continuation, DOCX export, and PDF export when available. The fresh APK is from `1ab4d4a`; subsequent Task 8 changes are backend-only. Phone testing of the APK remains for the owner; keep legacy routing until the pilot and 90% target are met.

## Current batch — Task 5 generic agent endpoint, fixtures, and green CI

- Branch `feature/verbatim-groq-pipeline`; pushed through `ad6f80c`. Task 4d/5 commits are `5498299`, `fb1cff7`, `da20769`, `6cb5586`, `17a2f76`, `a99d7d0`, `c35ed64`, `062e3e6`, and `ad6f80c`.
- Task 5 removes phrase routing from the agent. The model chooses bounded tools from the request and metadata; document contents are sampled only inside the backend. Tool failures are not exposed as source. Summary-only export, page-image/OCR handling, and source-inclusion controls remain deterministic.
- The agent uses a generic OpenAI-compatible endpoint, not a hardcoded provider name: `AGENT_API_KEY`, `AGENT_MODEL`, `AGENT_BASE_URL`, and `AGENT_PROVIDER_ORDER=agent`. Swap those values to change providers. Groq/Gemini remain configured through `AI_PROVIDER_ORDER` for summaries and existing commands.
- Added `backend/tests/agent_eval/scenarios.json` (11 fixture scenarios), `backend/tests/agent_eval/live_runner.py`, and `backend/tests/test_agent_scenarios.py`. The live runner generates varied wording from goals and is not part of CI.
- CI: Backend tests `44089673339` passed all 518 tests on `ad6f80c`; Mobile checks `44084623834` passed on `c35ed64`, and no mobile source changed afterwards. The earlier backend run `44084623856` failed three assertions; `062e3e6` fixed the inaccessible-result expectation and `ad6f80c` opted export tests into the intended single-call/multi-part summary paths.
- Local verification: `python -m compileall -q app tests` and `git diff --check` passed. Backend pytest is unavailable locally. At this handoff the live-model runner had not run because the checkout had no backend virtualenv, provider credentials, or installed runtime dependencies; the owner later confirmed audit finding 2 was verified.
- Preview build `7360facb-eb2c-447d-a621-f2ccde5aac97` finished from `ad6f80c`, versionCode 12. APK: `/storage/emulated/0/Download/Study-Assistant-preview-v12-ad6f80c.apk`, 42 MB, SHA-256 `e65d291f2cd1ed4c42800e4a7aa5642a9b0015618f6e4d316bedb7da15baf0d5`. Next: install and verify on the phone; run the live runner when a backend environment and provider key are available.

## Earlier batch — Task 4c export, app wording, build number and command parser

- Branch `feature/verbatim-groq-pipeline`; code/test commits are `288c7fe`, `83965ae`, `84169d9`, `bfff83c`, `1ecafd2`, `04f6989`, `ea78349`, and `42c70e5`. All are pushed without force.
- 4c-1: compact 1-based page/paragraph reference labels, whole-document detection, wrapped PDF metadata/source lines, compact topic headings. Backend `44014354849` passed.
- 4c-2: PDF/DOCX body 13.5 pt, source/meta at least 11 pt, larger headings, and `summaries_only` exports omit the empty-passage line. Backend `44014538850` exposed a test that read a run without an explicit size; fixed in `04f6989` by checking the paragraph style, and backend `44021144679` passed.
- 4c-3: backend defaults center title/headings and left body/meta; mobile alignment controls are collapsed under “Alignment options”. Backend `44014707889`, Mobile `44014707848` passed.
- 4c-4: empty state now uses neutral action labels (`Original passages`, `Summary`, `Both`, `Page range`) with an inline blank topic/pages form; sample topics and example chips were removed from mobile UI text. Mobile `44019465811` failed typecheck because the regression imported `node:fs`/`node:url`; fixed in `ea78349` by testing the exported action labels without Node APIs, and Mobile `44021144786` passed.
- 4c-5: `expo-application ~57.0.3` supplies `Application.nativeBuildVersion`, with Expo Constants fallbacks. EAS build 11 was produced; device display still needs owner verification after installing the APK.
- 4c-6: `groq_command` now extracts JSON from prose, fences, wrappers (`command`/`result`/`output`), list content, and `reasoning`/`reasoning_content`; schema stays strict and invalid attempts log only a location/category, never message text. Fake-response tests cover each shape.
- All final checks passed: Backend tests `44028747367` on rerun (initial run hit the known unrelated flaky `test_concurrent_topic_and_transcript_updates_merge`; rerun passed), and Mobile checks `44021144786`. Local mobile: 84 tests, typecheck and lint pass; lint retains the existing unused `commandFromLocalIntent` warning. Local backend pytest is unavailable; `py_compile` passed.
- Production `/health` reports commit `42c70e5`.
- EAS preview build `39683455-a87f-42f1-9209-a74a657722d1` finished from `42c70e5`, versionCode 11. APK: `/storage/emulated/0/Download/Study-Assistant-preview-v11-42c70e5.apk`, 42 MB, SHA-256 `5de2f37adcc6947e09cbad9a387858bbd70a714c002ed7343aef6ae2ea17d64a`.
- Next: owner installs the APK and verifies Settings shows “11”, the larger/wrapped exports, neutral empty state and usable chat commands; OCR defects #5–#7 remain parked.

## Earlier batch — 4b-2 summary cost UX and 4b-3 export alignment

- Branch `feature/verbatim-groq-pipeline`; last pushed/deployed commit is `33dfe49`.
- 4b-2 is now complete locally: `mobile/services/summaryEstimate.ts` estimates single-call vs chunked summary requests; the whole-summary pending state shows “about N AI request(s)”; summary-only cards and the results screen collapse per-part summaries under a Details control; “Summarise <topic> only” now strips `only` and routes to the topic path.
- 4b-3 is complete locally: extraction, study-job and topic-result export endpoints accept `text_align` (`left|center|right|justify`, default `left`) and `heading_align` (`left|center`, default `left`). `backend/app/services/exporter.py` applies them to DOCX paragraphs and PDF text boxes while preserving page flow. The mobile export surfaces include an Alignment picker and send the selected values.
- Added contractor-requested regressions: summary estimate tests, topic-only parser tests, export alignment tests for DOCX XML/DOCX alignment and PDF x positions, endpoint alignment/validation, and mobile export URL query tests.
- Verification: mobile `npm test` 82 passed; `npm run typecheck` passed; `npm run lint` has 0 errors and the existing unused `commandFromLocalIntent` warning; `python -m compileall -q backend/app backend/tests` and `git diff --check` passed. GitHub Actions on `4cdcc5a`: Backend tests `44004014606` and Mobile checks `44004014701` passed.
- Pushed `4cdcc5a` to `origin/feature/verbatim-groq-pipeline`; production `/health` reports commit `4cdcc5a`.
- EAS preview build `764c8200-a2f2-421d-b515-f94fc4ffb6f7` finished from `4cdcc5a` with `versionCode` 10. APK: `/storage/emulated/0/Download/Study-Assistant-preview-v10-4cdcc5a.apk` (42 MB, SHA-256 `23ec3d2828cba6bc75994b8e6091fdb1d08787cc84e568120b40481c1f601df3`).
- Defects #5–#7 remain untouched; device inspection of this build is next.

## Earlier batch — chat OCR completeness and vector pages

- Branch `feature/verbatim-groq-pipeline`; started from clean `8de7fc7` after fast-forwarding from `35297b1`.
- This batch fixes the first two current OCR defects only:
  - `a36da72` `fix: prepare selected PDF OCR before chat extraction`: mobile range extraction now prepares PDF OCR before calling `/extract`. Page selectors prepare only the selected pages; whole/chapter/items/paragraphs prepare pending pages. The API contract note records that `/extract` itself runs at most one OCR batch.
  - `e2505cb` `fix: mark sparse vector-only PDF pages for OCR`: sparse 15–119-character PDF pages now count large vector-drawing coverage as an OCR signal, not only raster image coverage. Added `graphic_ocr_candidates` and `vector_ocr_candidates` diagnostics and parser regressions; the small-illustration non-OCR case is retained.
- Verification: mobile `npm test` 74 passed; `npm run typecheck` passed; `npm run lint` has 0 errors and the existing unused `commandFromLocalIntent` warning. `python -m compileall -q backend/app backend/tests` and `git diff --check` passed. GitHub Actions backend run [43922172365](https://github.com/Orth0myxo/study-assistant/actions/runs/43922172365) passed, and mobile run [43922172415](https://github.com/Orth0myxo/study-assistant/actions/runs/43922172415) passed. Local backend pytest/PyMuPDF execution is still unavailable.
- First-batch scope stops here. Defects #3–#7 remain untouched, per the owner's instruction to inspect/test this batch before approving the next two.
- Deployment: after the push, production `/health` reported commit `aa0a4af`, so this batch is live on Render. The real 53-page `Test 1.pdf`, Render OCR logs, and device export remain unavailable here; device-level inspection is still needed.

## Earlier handoff — WIP Task 4b and Task 5

- Branch `feature/verbatim-groq-pipeline`; base before this WIP is `450c4e1`. Remote fetch completed and the branch was even with origin (0 ahead / 0 behind). This handoff and code changes are being committed together; CI may fail because Task 4b-2 and Task 5 edits are unfinished and backend tests were not run locally.

### Task 4b-1 — filename normalization

- Done locally in `mobile/services/apiClient.ts`: normalize nested `filename` fields once at API JSON boundaries, including upload and study-job creation. Existing `decodeFilename` safely leaves malformed escapes unchanged. Removed repeat decoding in `mobile/app/workspace.tsx`, `photo-notes.tsx`, `photo-results.tsx`, `photo-topics.tsx`, `results.tsx`, and `upload.tsx`.
- Regression exists in `mobile/tests/apiClient.test.ts` for `%20` and malformed `%` names. Existing `mobile/tests/displayName.test.ts` also covers those cases.
- Ran focused mobile tests: 22 passed. Ran full mobile suite: 73 passed. TypeScript passed. Lint passed with 0 errors and the existing unused `commandFromLocalIntent` warning. `git diff --check` passed.

### Task 4b-2 — summary cost / accounting (partial)

- Implemented so far: `backend/app/config.py` adds `summary_single_call_chars=12000`; `backend/app/services/summary_cache.py` counts cache-miss `ai_calls`; `backend/app/services/study_processor.py` has a <=threshold single-summary path for `summaries_only`, diagnostics, and the `study done mode=… ai_calls=… cache_hits=… seconds=…` log; `backend/app/services/topic_study.py` reports counts; `backend/app/api/topic_sources.py` logs topic-study counts; `backend/tests/test_study.py` changes the small-document assertion and forces the overview test onto the large path.
- Stops here: no pre-job mobile estimate / confirmation, no collapsed Details UI for per-part summaries, no added large-document call-count regression, and no verification that “Summarise <topic> only” routes correctly end-to-end. Existing parser coverage remains in `mobile/tests/chatOrchestrator.test.ts`, but the exact pilot phrase has not been added as a regression.
- Backend tests for these changes were never run locally and no GitHub Actions result exists for this WIP. Check for runtime/schema/presentation regressions and add/fix tests before treating this item as complete.

### Task 4b-3 — export alignment (not started)

- No alignment code or alignment tests added. `backend/app/services/exporter.py`, the three export routes, API docs, and DOCX/PDF alignment checks remain to do. No alignment tests were run.

### Task 4b-4 — previous-result follow-ups (not started)

- No follow-up scope state or resolver changes made. `mobile/services/chatOrchestrator.ts`, `mobile/app/workspace.tsx`, and phrase regression tests remain to do. Existing chat parser tests are unrelated; no follow-up tests were added or run.

### Task 5 — agent backend follow-up (partial, pre-existing local edits preserved)

- `backend/app/services/agent_loop.py` adds shared model-call budgeting; `backend/app/services/agent_service.py` constrains summary length, budgets outline/per-topic model calls, samples less source text, and supports per-topic summaries; `backend/tests/test_agent_loop.py` adds shared-budget coverage.
- Stops here: these edits are not reviewed as complete and have no test result. The added test exists but was never run locally or in GitHub Actions. Existing base Task 5 commit `450c4e1` had backend Actions run `43866523857` pass; that result does not cover these uncommitted changes.

### Task 6 — mobile agent screen (not started)

- No Task 6 source or tests changed. A proposed screen/API patch was rejected by automatic review because Task 6 had been explicitly deferred. No Task 6 tests exist or were run. Continue to defer until requested.

### Next action

- Fetch before any further commit; keep all unfinished code. Run backend verification through GitHub Actions, fix failures without discarding work, then finish Task 4b-2, 4b-3, and 4b-4 one piece at a time. Keep Task 5 changes separate when practical and Task 6 deferred. Do not amend or force-push.

## Active handoff — Task 3 complete

- Branch `feature/verbatim-groq-pipeline`, current pushed commit before Task 3d:
  `c799c19` (`fix: report empty OCR pages in extraction`). The worktree was clean at start
  and tracked `origin/feature/verbatim-groq-pipeline`.
- Task 2b is complete and pushed in `431fd8e`, `287337c`, `7d1be7c`, and
  `754b0be`. Task 3b is complete and pushed in `ac256da`; backend Actions run
  `43838438205` passed.
- Task 3c is complete in `c799c19` (`fix: report empty OCR pages in extraction`);
  backend Actions run `43840760309` passed. Extraction represents terminal
  empty-OCR pages with
  `No text could be read from this page`, `ocr_empty: true`, and the stored image
  flag. Export rendering treats this as a muted status note, not source text, and
  retains the slide image. Added API/export regressions and updated the API
  contract. Local `compileall` and `git diff --check` passed; pytest was not run
  locally because dependencies are unavailable and must not be installed here.
- Task 3d is complete in `d07448e` (`fix: reject exports without source text`).
  PDF exports now have a shared guard to refuse selections
  with zero source-text passages using `409 ocr_incomplete`, naming terminal
  empty-OCR pages. The gate is applied to extraction, study-job, and topic-result
  exports. Added a regression verifying the range export names page 1 and refuses
  a status-only file. GitHub Actions run `43841762295` passed on rerun: 468 passed
  in 11.18 seconds. Its first attempt had one failure in the existing
  `test_concurrent_topic_and_transcript_updates_merge` (467 passed, 1 failed);
  the rerun passed.
- Next: deploy and have the owner verify the cytology DOCX/PDF and image-only page
  behavior on the phone. Optional Task 3e OCR prioritization was not implemented.
- Known verification limit: local DOCX/PDF inspection and backend tests are not
  available in this Termux environment; the real owner PDF is not in the checkout.

## Task 2b baseline inspection (before changes)

- The requested synthetic PDF/DOCX fixture could not be generated or rendered in this Termux environment: `pymupdf`, `python-docx`, `pytest`, and FastAPI are not installed. Per the environment restrictions, no dependencies were installed. The owner's 2026-11-16 phone test remains the measured baseline: one exported PDF page was about 6 MB; no local before/after measurement is available.
- Static inspection of the current export shows it embeds the 144-DPI cached RGB PNG. `_PdfWriter.image` scales only to the full-page content box, so an image that does not fit the remaining space moves to a new page at nearly full-page height. The OCR label is emitted before passage text. Current topic headers use `Topics requested: ...` and the match/material status line includes `source passages only`.
- Proceed in Task 2b's order: export-only JPEG renderer, PDF image flow layout, then header/label wording. GitHub Actions is the backend test authority; local export generation remains unverified here.

### Task 2b-1 — compact export images (complete)

- Commit `431fd8e` added a separate cached JPEG renderer for exports at 110 DPI and quality 70, with a bounded 90-DPI fallback under the existing per-image byte and pixel limits. The authenticated page-image route remains on its original PNG renderer/cache.
- Added a synthetic photo-like raster regression comparing export JPEG bytes with the route PNG and checking that PDF and DOCX each embed one image. GitHub Actions backend run `43831101723` passed. The regression confirms the JPEG is less than half the PNG size; the owner's `Test 1.pdf` before/after file size was not measured in this environment.

### Task 2b-2 — PDF image flow layout (complete)

- Commit `287337c` makes PDF images use remaining page height when they retain at least 55% of their full content width; otherwise they begin on the next page at full content width. Aspect ratio is kept. DOCX keeps source-text-then-image order at its text-column width.
- Added PDF regressions for a short passage sharing a page with its image and a long passage whose image follows the last text page. GitHub Actions backend run `43831833914` passed. Local PDF rendering remains unavailable because PyMuPDF/python-docx are not installed.

### Task 2b-3 — export wording (complete)

- Commit `7d1be7c` formats a single page as `Page N`, contiguous ranges as `Pages N-M`, and replaces the confusing match/material status line with `Original text from the source` when passages are present.
- Added coverage for one page, a page range, and multiple page topics. The first Actions attempt had one unrelated failure in `test_concurrent_topic_and_transcript_updates_merge` (460 passed, 1 failed); rerun of backend workflow `43832479189` passed the complete suite (461 tests). Local runtime tests remain unavailable.
- The Task 2b code is complete and pushed in `431fd8e`, `287337c`, `7d1be7c`, and the cache-retention follow-up `754b0be`. Backend Actions runs `43831101723`, `43831833914`, rerun `43832479189`, and `43837786521` passed. Owner's `Test 1.pdf` before/after export size and phone rendering remain unmeasured here.

## Task 3a — OCR failure diagnostics (complete)

- Commit `32c93c4` added structured per-page OCR diagnostics for image width/height, DPI, returned character count, exception type, Tesseract return code, and a one-line stderr excerpt capped at 200 characters. OCR output and source filenames are not logged or returned to clients. Backend Actions run `43822128701` passed.
- `OcrFailed` now retains Tesseract's return code and stderr internally for server logs; existing API warnings remain unchanged.
- Added fake-runner/API regression coverage and a runner-level test for return-code/stderr capture. Backend Actions run `43822128701` passed; local backend pytest is unavailable under the environment restrictions.
- The owner supplied the requested Render findings in `bd4526d`, which replaces Task 3b's original wait-for-logs instruction. Task 3b says not to lower DPI or add retries; it treats blank OCR output as a terminal empty result.

## Task 3b — terminal empty OCR (complete)

- Commit `ac256da` adds persisted `ocr_empty` page state (schema v11). Empty/whitespace OCR results finish as `done`, remain out of retry/failure lists, and are reported by page number as `Picture page(s), no text could be read`.
- Added fake-runner tests for empty and whitespace output, repeat-request caching, and topic-review reporting; updated schema migration assertions and the OCR API contract. Backend Actions run `43838438205` passed; local pytest remains unavailable.
- Task 3c/3d follow as separate commits.

## Task 2 — organized exports with slide images

- Implementation commit: `8f10d2e` (`feat: organize exports with cached slide images`), pushed to `feature/verbatim-groq-pipeline`.
- Moved bounded PNG rendering/cache behavior into `backend/app/services/page_images.py`; both the authenticated page-image route and export rendering use it.
- Rebuilt topic/study exports around document/date/requested-topic headers, ordered page headings, full stored passage text, explicit empty-topic messages, OCR-origin labels and pending/failed notes, and clearly labelled AI summaries. PDF and DOCX writers embed each selected page image once and enforce 30-image/25-MB limits with omitted-page warnings.
- Added backward-compatible `include_page_images` query options to extraction, study-job, and topic-result export endpoints; documented the option and limits in `docs/api-contract.md`.
- Added DOCX/PDF fixture regressions for image counts/deduplication, disabled images, order and headings, OCR labels/notes, empty topics, and both image limits.

### Verification and remaining limits

- GitHub Actions [backend run 43811620752](https://github.com/Orth0myxo/study-assistant/actions/runs/43811620752) on `8f10d2e`: passed; the Python 3.12 job ran the complete backend test suite.
- Local `python3 -m compileall -q backend/app backend/tests` and `git diff --check`: passed. Local pytest and generated-export inspection were not run because PyMuPDF/python-docx are unavailable and the environment instructions prohibit installing backend dependencies for local reproduction.
- The required before-change DOCX/PDF visual baseline could not be generated in this checkout. Static baseline findings are recorded below. Owner should inspect the exported cytology DOCX/PDF after Render deploys; no production `Test 1.pdf` is present here.
- Next step: after this handoff commit deploys, export the cytology topic in DOCX and PDF on the phone and confirm slide-image readability, image ordering, and file size.

## Task 2 baseline inspection (before implementation)

- The requested realistic DOCX/PDF fixture could not be generated or visually inspected in this Termux checkout: neither available Python has PyMuPDF, python-docx, FastAPI, or pytest, and no PDF page renderer is installed. The environment instructions prohibit installing these dependencies just to reproduce Actions coverage. No fixture output was fabricated.
- Static inspection of the existing exporter found that topic passages prefer the 320-character `excerpt` over the full `passage`; passages are shown under a generic source heading with a page metadata line, not a `Page N` subheading; empty topics have no explicit message; and there is no title date/topics block, OCR-origin label, page-image support, or page-image omission warning. AI material is not labelled `AI summary, not source text`.
- The page PNG renderer still lives inside `get_document_page_image`. The API export options only expose `format`. Existing route tests cover PNG content type/signature, ownership, and invalid page numbers.
- Baseline visual DOCX/PDF inspection remains a limitation; use the export regression tests and GitHub Actions after implementation, then inspect the owner's device export after deployment.

## Current task and Git state

- Branch: `feature/verbatim-groq-pipeline`, tracking `origin/feature/verbatim-groq-pipeline`.
- Base is `2053d73`; the reliability work is committed as `ae41173`, `ec0e477`, `74da433`, `6d63e59`, and `0359ce2` and pushed to the tracked branch.
- The review fixes are `7363e3f` (OCR retry/schema v10), `a818464` (mobile export recovery, docs, dead-handler removal), `f86483c` (define the attempt constant), and `bbc68cd` (update the topic export regression), on top of pushed `0359ce2`.
- `origin/main` is `4db2b2e` and is an ancestor; this remains a feature branch, not a merge.
- The real `Test 1.pdf` is not present in this checkout, so no device or production-scale OCR measurement was made.

## Task 1 implementation

- PDF ingest extracts native text and stores per-page native character count, image coverage, `needs_ocr`, `ocr_status`, and `has_page_image`. It does not run OCR. Piece A's candidate rule is retained: under the native-text minimum, or 15–119 compact characters with at least 20% image coverage.
- OCR runs only for pending pages selected by topic matches, page/range extraction, whole-document study, or export. Results are persisted in the page record and `Section.raw_text`; subsequent requests reuse them. Each request has both a page cap and time budget. Warnings list pending pages, and failed pages retain native text with `ocr_status=failed`.
- Failed pages now persist `ocr_attempts` and are retried by later OCR requests up to `OCR_MAX_ATTEMPTS` (one initial attempt plus two retries). Retryable failures appear in `pending_pages`; only exhausted failures appear in `failed_pages` and block study/export. Schema is v10.
- Added authenticated `GET /api/v1/documents/{id}/pages/{n}/image`, cached under document retention, with pixel/byte bounds. Topic passages expose `has_page_image`.
- `OCR_DPI` remains 200. The API docs recommend trying 150 on Render.
- A synthetic 53-page upload timing check requires under 10 seconds and prints `Server-Timing`; Actions measured 14.7 ms total. This is not the owner's actual `Test 1.pdf` measurement.

## Follow-up reliability changes

- Added owner-scoped `GET/POST /api/v1/documents/{id}/ocr` for resumable OCR progress and bounded batches. Progress is derived from persisted page state; no eager OCR was added.
- Whole-document study, range study, and PDF/DOCX/topic exports now return `409 ocr_incomplete` with pending/failed page details instead of silently using partially OCR'd text.
- Topic review reads pending OCR pages before declaring a document empty or returning no match, including fully scanned and mixed PDFs.
- Document editing now uses the configured Groq/Gemini provider chain with fallback; exact-span and review validation are unchanged.
- Mobile prepares scanned pages in batches before whole-document study, topic matching, and export retries. Study, topic, and range export downloads recover from a `409` by preparing OCR and retrying once. Edit previews no longer auto-export; PDF/Word buttons require explicit confirmation.
- Mobile chat routes outline/image requests to the existing photo-notes outline-review flow.
- Removed the dead `runRangeStudy` handler; `runRange` is the live range path.
- Documented the OCR wait map and two-phase matching, result-scoped export, and background-worker proposals in `docs/pilot-test-findings-2026-11-15.md`; the proposals are not implemented.
- Documented the single-worker Render requirement in `docs/architecture.md` and the Dockerfile: the OCR lock is process-local.
- Mobile `ApiError` now preserves server `details`, so pending/failed OCR pages reach the retry logic.
- Added backend regression tests for OCR progress/resume, partial-study blocking, scanned/full topic recovery, and edit-provider fallback. Added mobile tests for OCR error details and batch-resume behavior.
- Updated `docs/api-contract.md` for the OCR endpoints, `ocr_incomplete`, topic-review OCR fallback, and editing provider/confirmation behavior.

## Verification

- `git diff --check`: passed.
- `python -m compileall -q backend/app backend/tests`: passed after the follow-up changes.
- Local full pytest suite: not run. This Termux Python still has no `pytest`, FastAPI, or PyMuPDF modules; no local dependency installation was attempted.
- GitHub Actions [backend run 43794082441](https://github.com/Orth0myxo/study-assistant/actions/runs/43794082441) on `bbc68cd`: passed; 446 tests. Earlier runs on this pass failed on the undefined attempt constant and one old failed-page expectation; both are fixed.
- Mobile `npm run typecheck`: passed after the review fixes.
- Mobile `npm test`: 68 tests passed after the review fixes.
- Mobile `npm run lint`: 0 errors, 1 existing unused-code warning (`commandFromLocalIntent`).
- GitHub Actions [mobile run 43791474102](https://github.com/Orth0myxo/study-assistant/actions/runs/43791474102) on `a818464`: passed. Later commits are backend-only.
- The workflow has one existing Actions deprecation annotation for Node.js 20 action runtimes; it is unrelated to the tests.
- Upload duration for the actual 53-page `Test 1.pdf`: not measured because the PDF is absent.

## Known limits and next step

- First-time OCR still costs CPU for selected pages. Default cap is 5 pages and 20 seconds per request; the new progress/batch endpoints make the remaining pages resumable.
- Failed OCR pages are retried twice automatically. If all attempts fail, they block study/export until the user supplies a clearer scan or re-uploads the document; they are not silently treated as empty.
- Backend tests were executed in GitHub Actions, not locally. The editing endpoint tests pin the Gemini test client so a local Groq key cannot make them hit a real provider.
- After deployment, re-upload `Test 1.pdf`, request `cytology`, and confirm complete OCR progress, repeat-request caching, and Render page-level OCR logs.
- Background ingest now logs one line when it finishes: `ingest done document_id=... format=... pages=... kb=... parse_s=... store_s=...` (ids and numbers only). Search Render logs for `ingest done` to see how long the "extracting" step really takes. Test: `test_ingest.py::test_ingest_logs_timing_without_the_file_name`.
- Added `.github/workflows/mobile-tests.yml` (Node 22: `npm ci`, typecheck, lint, tests) so mobile changes are checked in CI, not only locally. First run result is the verification; fix the workflow, not the app, if it fails for environment reasons.
- Logging fix: `LOG_LEVEL` existed but was never applied, so every `app.*` info line (`ingest done`, `ai call`) was dropped and only warnings showed in Render. `_configure_logging` in `main.py` now applies it (default info). Tests: `test_logging_config.py`.
- Also fixed from the audit: combined overview is capped as a whole; `BEYOND_MAX_WORDS` (100) matches its prompt; rate-limit message grammar uses unique providers; failed ingests log `ingest failed ... code=... seconds=...`; `GIT_COMMIT` fallback test.
- Still open: `docs/agent-architecture-spec.md` gaps (result_id/file_id/state storage, outline confidence, sampled-document summarize path, BYO-key header security/CORS).
