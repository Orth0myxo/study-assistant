# Pilot test findings — 2026-11-15

Handoff for the coding agent (Codex / OpenCode). Written after the owner tested the
APK against the deployed backend with `Test 1.pdf` (53 pages, lecturer slides) and a
photo of the handwritten PATH 503 course outline. Work one piece at a time, test on
the device, then fix the next. Do not batch.

Status labels: **Observed** (seen in screenshots/logs), **Hypothesis** (not yet
verified; verify before coding), **Done** (committed).

## Deployment facts (checked 2026-11-15)

- Render deploys `feature/verbatim-groq-pipeline` (service `study-assistant-api`,
  `autoDeploy: true`; every push redeploys and wipes `/tmp` uploads and SQLite).
- The APK talks to `https://study-assistant-api-5owo.onrender.com` (hardcoded in
  `mobile/eas.json`, `preview` profile).
- UptimeRobot monitors `/health` on that host every 5 minutes. It uses `HEAD`.
- Render environment had `GEMINI_MODEL` set to the invalid value `3.5 flash`; the owner
  corrected it to `gemini-3.5-flash-lite` (confirmed via `/health`). `GROQ_API_KEY` is
  **not set**, so Groq is inactive and Gemini does everything. Gemini's free tier has low
  limits; adding a valid Groq key (or a paid provider) is pending the owner's decision.
- `/health` still reports version `0.1.0` and no commit. EAS uses
  `appVersionSource: remote`, so `app.json` `version` is probably ignored; every APK
  shows `0.1.0`. See "Open items".

## Done in this session

| Commit | Change |
| --- | --- |
| `bcf9f24` | `/health` now accepts `HEAD` as well as `GET` (UptimeRobot got 405 and reported the service down). Tests added in `test_health.py` and `test_access_control.py`. |
| `dfa6be3` | Merged `perf/latency` into the deployed branch: per-provider-call `ai call ...` log lines, `Server-Timing` header (also keeps the upload route's own breakdown), 8 s chat-command timeout (`GROQ_COMMAND_TIMEOUT_S`), `slow request` log (`SLOW_REQUEST_LOG_MS`). Merge was conflict-free and compile-checked only; **CI has not yet confirmed it**. |

Earlier (already on the branch): bare topic names fall back to topic search
(`e05194f`); summaries wait out short rate limits and stop on exhausted quota
(`9b00d36`).

## Pieces to implement, in order

### A. Verbatim passages come back as headings only; exports are empty (core feature)

**Observed:** topic search for "cytology" returns 100%-confidence matches whose text is
only a slide title (`CYTOLOGY BRUSHES`, `VAGINAL CYTOLOGY`, "Page 19 liquid-based
cytology requires plastic sampler."). A range request for "Page 2" returns only
`VAGINAL CYTOLOGY`. PDF and DOCX exports contain only those headings.

**Hypothesis (verify first):** in `document_parser.py` (`parse_pdf`, around lines 294-305)
a page counts as native text once `len(_compact_text(raw_text)) >= settings.min_pdf_chars_per_page`
(default 15, whitespace not counted) and is then **never OCR'd**. `VAGINAL CYTOLOGY` and
`CYTOLOGY BRUSHES` are each exactly 15 compact characters. If the slide bodies are
pictures of text, the parser sees the title, treats the page as fine and skips OCR, so the
body is never read.

**How to verify:** the owner should confirm whether the body of page 2 is selectable
text or an image, or provide the PDF. Alternatively, run the parser on the PDF locally and
print per-page `source` (`native` / `ocr` / `none`) and character counts.

**If confirmed, fix:** also OCR a page when it has little native text *and* contains images
covering a meaningful share of the page (PyMuPDF `page.get_image_info()` / `page.get_images()`),
not only when the text is under 15 characters. Keep `OCR_PAGE_LIMIT` in mind (default 30; the
PDF has 53 pages) and the Render free CPU cost (Tesseract at 200 dpi, 30 s timeout per page).
Consider raising the limit and OCRing the highest-image-coverage pages first. Add a
regression test with a synthetic PDF page that has a short title plus a large image of text.
Also check why the passages are single lines: sections look heading-sized, which suggests
the splitter is cutting at slide titles with no body underneath.

**Acceptance:** requesting "cytology" returns passages that contain the slide body text;
PDF/DOCX export of those passages contains body text, is titled, ordered by page and
readable as a presentable document.

### B. Summaries are too long and structured wastefully

**Status: partly done (word caps); the whole-document structure is unchanged.**

**Observed:** a request for "a summary of only cytology" produced a long summary with
several sub-headed "AI summary" blocks plus an overall AI summary. It is unclear whether it
covered only cytology or the whole PDF.

**Correction to an earlier note:** the *topic* path (`topic_study.py`) already makes one
summary call per topic. The per-section summaries plus an overall summary come from the
*whole-document / range* path (`process_document` in `study_processor.py`: one call per
~4,000-character unit, then an overview). The sub-headed output in the test most likely came
from that path, which suggests the app routed the request as a whole-material summary
instead of a topic. Confirm which path the app calls for "summary of <topic>" (see piece G).

**Done:** hard word caps. Prompts now ask for at most 120 / 150 / 100 / 90 words
(section / topic / beyond-source / overview) and `limit_words` (`ai_client.py`) trims every
result to `SUMMARY_MAX_WORDS` (150) or `OVERVIEW_MAX_WORDS` (100), keeping whole bullets or
sentences, at all four call sites. Tests: `backend/tests/test_summary_limits.py`.
No `max_tokens` cap was added on purpose: reasoning models count thinking tokens toward it
and could return an empty answer. **Superseded for the agent by Task 11:** every agent call
must now send `max_completion_tokens` (default 500), use `reasoning_effort=low` or `none`,
and guard the provider's output-token/minute allowance. The summary path keeps its existing
word caps.

**Still to do (agent):** a "short summary" mode that makes one call over the matched
passages (no per-section pass, no overall pass) for use as the agent's `summarize` tool;
and make sure a topic-scoped summary request only sends that topic's passages.

**Acceptance:** "summary of cytology" returns one short summary (a few bullets or one
paragraph, under about 150 words) and the `ai call` log shows a single call.

### C. Slow uploads (50 KB to 10 MB) and slow OCR

**Observed:** all uploads feel slow, including 50 KB files. **Not measured.**

**Now possible:** the merged `dfa6be3` logs each provider call (`ai call provider=... seconds=...`)
and sets `Server-Timing` (`read`, `parse`, `store`, `ocr`, `total`, plus providers) on the
upload response; requests over `SLOW_REQUEST_LOG_MS` (2000) log `slow request`. After
deployment, upload the same file and read the Render logs and the response header before
changing anything.

**Likely suspects to check:** Tesseract on Render's free CPU inside the upload request;
Render cold start (UptimeRobot should now prevent it); the mobile `ensureBackendAwake` wait
(it has a bug: the awake flag never expires); network latency to the Oregon region.

### D. "Topics in the image uploaded" does not match

**Observed:** typing "I need the original passage from the pdf of the topics in the image
uploaded" returns "I could not match 'topics in the image uploaded' to a passage". The
command step treats that phrase as a literal topic to search for. This is not an OCR error.

**Context:** `SESSION.md` says the lecturer-outline flow (Gemini vision reads the outline
image, editable review, then matching against the PDF) exists as a separate photo-topics
screen (`mobile/app/photo-topics.tsx`, commit `bf214bf`). It is not reachable from plain chat.

**Fix:** route chat requests that refer to "the image/photo/outline/topics in the image" to
that flow (or run it directly with the most recent uploaded outline image), then return the
verbatim passages, verbatim plus summary, or summary only according to the request. It
requires `GEMINI_MODEL` to be valid (now fixed). Keep verbatim text produced by the backend
only. Re-test that handwriting is actually read by the vision model; Tesseract cannot read
cursive.

### E. Study-mode chips only appear after a response

**Observed:** the "Topic summary / Combined study / Original passages" chips under "STUDY MODE"
appear only after a request has been answered.

**Intended:** the chips are a template of what can be requested, available before typing:
choose a mode, then name a topic. The AI produces summaries; the backend supplies the verbatim
passages.

**Fix:** mobile change in `mobile/app/workspace.tsx` and related components so the modes show
from the start (when material is attached) and set the request mode. Needs a mobile typecheck
and tests, which could not be run in the sandbox used for this session.

### Parked by the owner

- The uploaded photo is shown as text instead of the real image (frontend).

## Open items and notes

- **App version shows `0.1.0` on every build.** `appVersionSource` is `remote` in
  `mobile/eas.json`, and `autoIncrement` only raises the build number. Options: set the
  version with `eas build:version:set`, or switch to local versioning. Also show the app
  version and build commit in Settings, and add the deployed commit to `/health`
  (Render exposes `RENDER_GIT_COMMIT`) so APK and backend can be compared in one look.
- **`MAX_PAGES` default is 50 in `config.py` but a 53-page PDF uploaded successfully.**
  Check the Render environment value so the documented limit matches.
- The agent's new Gemini image call (`_generate_multimodal`) logs timing with purpose
  `summary`; label it `vision` or similar so logs are readable.
- Provider choice: Groq's free tier has low shared limits; DeepSeek or paid Gemini are being
  considered. No decision yet. Do not commit keys; set them only in Render.
- Pushes to the deployed branch redeploy Render and wipe uploaded files. Ask the owner to
  re-upload the PDF after each deploy when testing.
- Git: shallow clones in the Claude session cause false "unpushed commits" warnings.
  Pre-existing commits by the owner and other tools are intentionally left unamended.

## Round 2 — after piece A was deployed (2026-11-15 afternoon)

Owner re-tested with `589f97e` deployed and Groq active (`/health` now shows the Groq
model only). Two new observations.

### F. Upload plus OCR takes about 3 to 5 minutes

**Observed:** the owner waits roughly 3 to 5 minutes for the 53-page `Test 1.pdf` to be ready.
**Not yet measured:** read the upload response `Server-Timing` header (`ocr`, `parse`,
`store`) and the document stats (`ocr_attempts`, `image_ocr_candidates`,
`ocr_duration_ms`) to confirm.

**Likely cause:** `_parse_pdf` OCRs candidate pages one after another with local Tesseract
at `OCR_DPI=200` (up to `OCR_PAGE_LIMIT=30` pages, 30 s timeout each) on Render's free
shared CPU. About 5 to 10 s per page times about 30 pages is 3 to 5 minutes. Piece A made
more pages OCR candidates, so it increased this cost. Parallel OCR will not help on a
shared free CPU.

**Options, cheapest first:**
1. Config only, no code: set `OCR_DPI=150` (or 130) in Render. OCR time scales roughly with
   pixel count, so this should be about 1.5 to 2 times faster. Check that body text is still
   read correctly.
2. Real fix, backend only: lazy OCR. Parse native text immediately (seconds), record which
   pages are image-heavy, and OCR only the pages a request actually needs (pages matched by a
   topic heading, or an explicit page range), then cache the OCR text on the page in the
   database. First request for a topic pays a few pages of OCR; later requests are instant.
   Keep verbatim passages sourced from stored `Section.raw_text`.
3. Vision-model OCR (Gemini or Groq vision) per page: network-bound, parallelisable and can
   read handwriting, but it uses free-tier quota, sends document pages to a third party, and
   at Gemini's free 15 requests per minute 30 pages still take about 2 minutes.
4. A paid Render instance has far more CPU than the free one and would speed Tesseract up
   without code changes (cost decision for the owner).

**Acceptance:** a 53-page PDF is usable for questions within seconds of upload, and asking
for a topic returns its body text without re-OCRing pages already read.

### G. "Summary of the passage of the topic vaginal smear" returned long verbatim text

**Observed:** a topic request phrased as a summary returned the verbatim passage, and it
was long.

**Likely cause (frontend, verify):** for topic requests the app runs the passage search and
shows passages first. A summary is generated only when `parseChatIntent` sets
`intent.generate` (`mobile/app/workspace.tsx`, `commandFromLocalIntent`, lines about 338 to
356; `runTopic(plan.topic, doc, plan.generateMode ? 'ai_study_material' : null)` around line
1115). The phrase "summary of the passage of the topic ..." probably does not set
`generate`, so the passage result is shown and the study-mode chips only appear afterwards.
This is the same root issue as piece E.

**This needs a mobile change, so a new APK is required after the fix.** Backend-only fixes
(pieces A, B, C, F) take effect on Render without rebuilding the APK.

**Fix:** when the user asks for a summary of a topic, go straight to the summary flow for
that topic (one capped AI call over only the matched passages) and do not display the full
passage unless asked ("verbatim", "original", "both"). Combine with piece B (length cap)
and piece E (modes visible up front). Add Vitest cases for phrases such as "summary of the
topic X", "summarise X", "summary of the passage on X", "X summary only".

## Round 3 — OCR wait map and scope proposals (2026-11-16)

This section records the wait behavior and possible next steps only. The current build keeps
eager document OCR, the existing `409 ocr_incomplete` contract, and the current MVP scope.
No speed change is included in the reliability review fixes.

### OCR wait map

| Action | OCR work before the result | When OCR is still missing |
| --- | --- | --- |
| Whole-document study | Every OCR-pending page in the document | `409 ocr_incomplete`; the app prepares OCR, then retries the study start |
| Page range or chapter study | Only the selected pages | `409 ocr_incomplete`; the app prepares the selected pages, then retries |
| Topic matching | First up to 5 pending pages needed to find matches | Partial matches with an OCR warning; no hard `409` |
| Topic study output | Every page in the matched topic result | Warning and retry from review; the result is not complete yet |
| Study or topic export | Every page in the source document | `409 ocr_incomplete`; the app prepares OCR, then retries the download |
| Explicit OCR POST | Pending pages, now including failed pages with retries remaining; capped by page/time budget | `200` with pending/failed page lists; never a hard `409` |
| Upload | No OCR; pages are marked pending | Not applicable |

A failed page is persisted with `ocr_attempts`. It is retried on the next OCR request up to
`OCR_MAX_ATTEMPTS` (initial attempt plus two retries). Only an exhausted page appears in
`failed_pages`; the API then returns a clear "failed after 3 attempts" warning/message.

### Proposals (not implemented)

1. **Two-phase topic matching.** In phase one, match against the first few pending pages to
   avoid OCRing an entire long PDF for a weak topic. In phase two, OCR only the pages needed
   for the matched result. Keep passages sourced from stored `Section.raw_text`.
2. **Result-scoped export gating.** Today study and topic export preflight the whole source
   document. Scope the preflight and export to the pages/range that the result actually uses,
   so an unrelated pending page does not block a completed range or topic export. This is an
   API/contract change and should be built with the exporter work, not as an ad hoc mobile
   workaround.
3. **Background OCR worker.** Move OCR out of the request path, persist a job, and show
   progress in the app. This is the real fix for upload-to-ready latency, but it adds a
   worker/queue and job-status UX and is outside the current MVP scope; it needs an explicit
   scope decision before implementation.
