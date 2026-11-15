# Development session handoff

Updated: 2026-11-15

## Current branch and testing phase

- Branch: `feature/verbatim-groq-pipeline`; remote: `origin` (`https://github.com/Orth0myxo/study-assistant.git`). Piece A is being added on top of fetched remote commit `0ce4740`.
- Read [`docs/pilot-test-findings-2026-11-15.md`](docs/pilot-test-findings-2026-11-15.md) for the owner's full pilot results, deployment facts, acceptance checks, and one-piece-at-a-time order.
- The owner confirmed page 2 of `Test 1.pdf` contains images. The PDF itself is not present in this checkout.
- Piece A implementation is ready locally: PDF pages with 15–119 compact native characters are checked for image coverage. At 20% coverage or more, the existing OCR path runs despite a text-layer heading. Existing OCR page limits still apply.
- Synthetic image-heavy and mostly-native PDF regressions pass. The complete backend suite on the fetched branch plus Piece A passed: 406 tests. `git diff --check` passed; the final staged diff has been reviewed.
- Piece B, first part (Claude session, 2026-11-15): hard summary word caps. `limit_words` in `ai_client.py` trims section/topic/beyond-source summaries to `SUMMARY_MAX_WORDS` (150) and overviews to `OVERVIEW_MAX_WORDS` (100); prompts also state word limits. Tests in `backend/tests/test_summary_limits.py`. The topic path was already one call per topic; the per-section plus overview structure is the whole-document path and is unchanged. Details in the findings doc, piece B.
- `/health` now also returns `commit` (short deployed commit from `RENDER_GIT_COMMIT`) and `ai.providers` / `ai.models` (which providers are active and their models), so APK/backend/provider state can be checked in one page load. Tests in `backend/tests/test_health.py`; contract in `docs/api-contract.md`.
- Provider-chain errors (`AiAllProvidersFailed` in `ai_chain.py`) now name the providers that failed: quota and rate-limit messages list them, a repeated identical error appends `(provider: groq, gemini)`, and mixed failures list `groq: ai_timeout, gemini: ...`. Error codes are unchanged. `HEAD /health` has its own handler hidden from OpenAPI (no duplicate operation id). Tests: `test_provider_error_messages.py`, `test_health.py`.
- Proposal only (not built): `docs/agent-architecture-spec.md` describes the tool-calling agent (one chat endpoint, tools wrapping existing services, verbatim never written by the model, an evaluation phrase set, and a staged migration). Build it only after the lazy-OCR task has been tested on a phone.
- Existing local stashes remain as backups. The newest (`stash@{0}`) backs up Piece A; older stashes preserve previous synchronization and vision work. Do not pop a stash without checking its contents first.

## Latest pushed implementation

- The branch contains `bf214bf` and follow-up fixes for Gemini vision interpretation of lecturer outlines, editable topic confirmation before matching, and safe document editing with verified source spans/counts and separate PDF/DOCX exports.
- The backend workflow `.github/workflows/backend-tests.yml` passed 390 tests at commit `bf214bf` (GitHub Actions run 43591977225). Later timing and health changes were compile-checked in the preceding agent session; this checkout plus Piece A passed 406 tests locally.
- Timing instrumentation and `Server-Timing` were merged in `dfa6be3`. The health endpoint accepts `HEAD` for uptime monitoring (`bcf9f24`). Render auto-deploys this branch; deployment wipes temporary uploads/SQLite, so re-upload the PDF after deploy.
- Render had `GEMINI_MODEL=gemini-3.5-flash-lite`; `GROQ_API_KEY` was unset at the last recorded check, so Gemini was the active provider. Verify current environment before diagnosing provider behavior.

## Piece A details and limitations

- Implementation: `backend/app/services/document_parser.py`; tests: `backend/tests/test_documents.py`.
- It checks image coverage only on pages with 15–119 compact native characters, uses a 20% page-area threshold, and preserves `ocr_page_limit`. Pages with less text already follow the existing OCR candidate path. Image metadata failures do not fail PDF parsing.
- OCRing additional image-heavy pages may increase upload time. The synthetic tests passed, but the owner's actual PDF/device extraction and exported passage content still need confirmation after deployment.

## Remaining pilot issues

- **B — summaries:** requests can produce multiple section summaries plus an overall summary. Reduce topic-scoped output to one short grounded summary and avoid redundant overview calls; test call count, topic scope, and output length.
- **C — latency:** uploads and OCR are slow, including small files. Use the new timing logs and `Server-Timing` after deployment to isolate read, parse/OCR, storage, and provider time before optimizing.
- **D — chat reference to outline image:** the separate photo-topics workflow exists, but plain chat does not yet route “topics in the image” requests through it to select passages from the PDF. Do not misreport this as an OCR error.
- **E — study-mode chips:** the owner reports the mode choices should be available before the first answer. Verify the current mobile behavior and fix if they remain hidden until a response.
- **Parked:** displaying the uploaded image itself rather than its text is frontend work and remains parked by the owner.
- The photo-topic results do not yet use the standard saved study-job history/export flow (see `docs/architecture-audit/architecture-gaps.md`).

## Verification and next step

- `backend/.venv/Scripts/python.exe -m pytest --basetemp=.tmp/pytest`: 406 passed, with existing Starlette/httpx deprecation, duplicate health operation ID, and Windows pytest-cache warnings. Mobile files are unchanged by Piece A.
- The reviewed staged files contain no secrets or generated artifacts. Push normally; the owner will deploy and verify `Test 1.pdf` on device. The Render deploy wipes temporary uploads/SQLite, so re-upload the PDF after deploy. Continue with B only after recording that result, following the owner's one-piece-at-a-time rule.
