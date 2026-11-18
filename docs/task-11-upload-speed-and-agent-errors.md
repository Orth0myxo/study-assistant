# Task 11: PDF upload speed regression, agent provider setup and honest agent errors

Rules as before: one piece at a time (test, fix, retest), no built-in wording, report what was not verified.
Do not push docs-only commits during the owner's test sessions (Render redeploys on every push and cuts uploads short).

## Evidence (Render log, 2026-11-18)

- PDF, 1.28 MB, `page_count=53`: `upload completed ... read_ms=0.9 parse_ms=353769.7 store_ms=3.8 total_ms=353976.1`.
  Parsing used to take about 1 s. The new cost is `page.find_tables()` called for every page in
  `document_parser.py` (`_pdf_tables`, called at about line 463 of the PDF loop). Not yet measured on its own.
- An 18 KB DOCX parsed in 7.8 s while the PDF was still parsing (shared 0.1 CPU).
- Image topic-source creation took 15.7 s and 28.2 s (OCR) while the PDF parse was running.
- Agent calls failed: `ai call provider=agent model=gemini-3.5-flash-lite ... outcome=http_404`. The model name and
  base URL did not belong to the same provider. The owner is switching to Groq `qwen/qwen3.8-27b`.

## A. Make upload fast again (most important)

1. Measure first: add `table_ms` (total time in `_pdf_tables`) and `tables_found` to the `upload completed` log line.
2. Do not run `find_tables()` for every page at upload. Options, in order of preference:
   - detect tables lazily the first time a page is needed (topic match, export, agent `find_passages`), then store the result;
   - or at upload only run it on pages that structurally look tabular (many aligned short text blocks / ruling lines
     from the PDF drawings), decided by geometry, never by wording;
   - cap per-upload table work (e.g. a time budget like the OCR caps) and mark the remaining pages as "tables not checked yet".
3. Keep Task 8 behaviour: pages that were checked keep their stored rows; unchecked pages are checked when first needed.
4. Target: the owner's 53-page, 1.28 MB PDF uploads in a few seconds on the free tier, like before Task 8.
5. Tests: unchanged table tests still pass; add one that proves upload does not call table detection for pages that are
   not requested, and one that proves a lazily detected table is stored and reused.
6. Also check that ingest in the background does not starve small requests: `/health` was 2-3 s and DOCX parse 7.8 s
   during the PDF parse. If cheap, lower the priority of background work or process pages in small slices.

## B. Agent provider setup (Groq, Qwen 3.8 27B)

- Document in `.env.example` and the agent docs: `AGENT_MODEL=qwen/qwen3.8-27b`, `AGENT_BASE_URL` = Groq OpenAI-compatible
  address, `AGENT_API_KEY` = Groq key. Groq lists the model as preview, tool use supported, 131,072 context, 16,384 max output.
- Add an optional `AGENT_REASONING_EFFORT` setting (values accepted by Groq for this model: `default`, `low`, `medium`,
  `high`, `none`) sent only when set. Default recommendation: `low`. Never hard-code it per provider by name.
- Log the HTTP status and a short error class for agent failures (already `outcome=http_404`); also log the base URL host
  (not the key) once at startup so a mismatched provider is obvious.

## C. Honest agent errors on the phone

- When the model call itself fails (provider error, 404, timeout, rate limit), do not say "Please try a more specific
  instruction". That blames the user's wording. Return a clear failure that names the real cause class
  (provider unavailable, model not found, timed out, rate limited) without exposing keys or URLs.
- The same warning currently appears twice in the response (`agent_models.py` lines 118/238 and `agent_loop.py` line 87).
  Return it once.
- The "try a more specific instruction" text in `agent_loop.py` line 181 is built-in wording; keep it only for the case
  where the model answered but no tool result or answer could be produced, or replace it with a neutral status code the
  app turns into its own text. The owner's rule: no built-in guidance phrases.
- Add a backend test for each failure class and a mobile test that the message renders once.

## D. Deployment hygiene (owner action, document only)

- Render redeploys on every push to the branch. Note in `docs/` that docs-only pushes restart the server and drop any
  in-flight upload and the ephemeral storage. Suggest the owner sets a build filter / ignored paths for `docs/**` in
  the Render service settings.

## Report

Commit hashes, CI results, measured `table_ms` before and after on a real 50+ page PDF (or a synthetic one if no real
file is available), and what was not verified (live Groq call, device behaviour). Tell the owner whether a new APK is
needed (only part C if the message text lives in the app).
