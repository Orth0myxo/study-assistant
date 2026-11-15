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

**Observed:** a request for "a summary of only cytology" produced a long summary with
several sub-headed "AI summary" blocks plus an overall AI summary. It is unclear whether it
covered only cytology or the whole PDF.

**Cause (from code reading, verify):** `process_document` in `study_processor.py`
summarises per section and then adds an overall summary, so a short request costs many
provider calls and produces long output.

**Fix:** for a topic or whole-material summary request, make one summary call over the
matched source text, with an explicit length cap (for example a maximum word count in the
prompt and a `max_tokens` bound), no per-section headings and no second overall pass.
Confirm a topic-scoped request only sends that topic's passages. Keep summaries grounded
in extracted source and never label AI text as verbatim.

**Acceptance:** "summary of cytology" returns one short summary (target a few short
paragraphs or bullets), makes one provider call, and the `ai call` log shows it.

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
