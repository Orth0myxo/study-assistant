# Development session handoff

## Latest — 2026-11-15 (pilot testing, one piece at a time)

- Read [`docs/pilot-test-findings-2026-11-15.md`](docs/pilot-test-findings-2026-11-15.md) first. It lists the observed problems, hypotheses to verify, an ordered fix plan (A to E), acceptance checks and open items.
- Render deploys `feature/verbatim-groq-pipeline`. Last verified deploy before this section: `a1da9da`. New commits on top: `bcf9f24` (`/health` accepts HEAD so UptimeRobot stops getting 405) and `dfa6be3` (merged `perf/latency`: provider-call timing logs, `Server-Timing`, 8 s command timeout). These two were compile-checked only in the Claude session; confirm CI is green.
- Render environment: `GEMINI_MODEL` was invalid (`3.5 flash`) and is now `gemini-3.5-flash-lite`. `GROQ_API_KEY` is not set, so Groq is inactive.
- Next useful action: verify piece A's hypothesis (PDF pages with a short text-layer title and an image body are never OCR'd because the native-text threshold is 15 characters), then fix and re-test on device before starting B.
- The owner's rule for this phase: test, fix, retest; no all-at-once fixes.

## Previous checkout — 2026-11-14

- Branch: `feature/verbatim-groq-pipeline`, tracking `origin/feature/verbatim-groq-pipeline`.
- Implementation commit: `bf214bf` (`feat: add outline and safe document editing flows`). CI test correction: `4395728` (`test: correct document edit assertions`).
- The workflow `.github/workflows/backend-tests.yml` is committed and pushed. GitHub Actions installed the declared backend development requirements and passed the complete backend suite: 390 tests. [Run 43591977225](https://github.com/Orth0myxo/study-assistant/actions/runs/43591977225).
- Mobile verification: Vitest passed (63 tests across 6 files), TypeScript passed, and lint had zero errors with two unused-variable warnings in `mobile/app/workspace.tsx`.
- Local Termux pytest remains unavailable (`No module named pytest`); backend verification was performed in GitHub Actions. `compileall` and `git diff --check` passed before handoff.

## Current implementation

- Lecturer outline images are interpreted through the configured Gemini provider. Detected topics appear in the editable review flow before matching against the uploaded lecturer PDF. Missing images/topics are reported, and excessive or overlong detections produce usable partial results with warnings where possible.
- Document editing treats the uploaded document as its scope. Gemini proposes exact source spans with a positive high-confidence safety verdict and contextual justification for each deletion. A second Gemini review compares the original and edited text. The backend rejects absent/negative verdicts, reported issues, uncertainty, invalid or overlapping spans, and incorrect requested/final counts before creating a separate result.
- Semantic safety relies on contextual model reviews; deterministic span/count validation alone does not establish that meaning is preserved. The original upload remains unchanged. Chat displays the validated edit preview, and the existing export flow generates PDF and DOCX from that same result.
- Regression tests cover Gemini image interpretation, topic confirmation/matching/missing topics/limit warnings, exact ten-word editing, cross-subject protected contexts, refusal, review failures, and PDF/DOCX output.
- Existing natural-language summary routing and provider error classification/recovery remain in place. Summary work stops after exhausted quota/rate limits while preserving available source text. Groq command interpretation receives bounded user text and document metadata, not document contents or chat history.
- Photo-topic study results do not currently use the standard saved study-job export/history flow; see `docs/architecture-audit/architecture-gaps.md`.

## Repository details

- Remote: `origin` → `https://github.com/Orth0myxo/study-assistant.git`.
- `.gitignore` excludes environment files, Python virtual environments, pytest caches, `node_modules`, Expo output, temporary files, and build output. The tracked `.env.example` contains configuration names and sample defaults only; local `backend/.env` and `mobile/.env.local` are ignored.

Updated: 2026-11-15
