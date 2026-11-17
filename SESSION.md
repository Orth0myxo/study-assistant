# Development session handoff

Updated: 2026-11-16

## Current batch — 4b-2 summary cost UX and 4b-3 export alignment

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
