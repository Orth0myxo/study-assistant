# Task 11: PDF upload speed regression, agent provider setup and honest agent errors

Rules as before: one piece at a time (test, fix, retest), no built-in wording, report what was not verified.
Do not push docs-only commits during the owner's test sessions (Render redeploys on every push and cuts uploads short).

## Evidence (Render log, 2026-11-18)

- PDF, 1.28 MB, `page_count=53`: `upload completed ... read_ms=0.9 parse_ms=353769.7 store_ms=3.8 total_ms=353976.1`.
  Parsing used to take about 1 s. The new cost is `page.find_tables()` called for every page in
  `document_parser.py` (`_pdf_tables`, called at about line 463 of the PDF loop). Not yet measured on its own.
- An 18 KB DOCX parsed in 7.8 s while the PDF was still parsing (shared 0.1 CPU).
- Image topic-source creation took 15.7 s and 28.2 s (OCR) while the PDF parse was running.
- Agent calls failed with `http_404` (wrong model for the base URL). After the owner switched to Groq the call now returns
  `http_429` (rate limited). Groq limits apply to the organization, not the key. Groq's page lists `qwen/qwen3.8-27b` at
  30 RPM, 1K RPD, 8K TPM, 200K TPD (plan not clearly stated); the owner's own limits page did not show Groq usage.
  Also the earlier failing call: `ai call provider=agent model=gemini-3.5-flash-lite ... outcome=http_404`. The model name and
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
- `reasoning_effort` is a field in the API request body, not an account or console setting (the Groq playground only has a
  control because it builds the request itself). The agent currently never sends it, so Qwen runs in full thinking mode.
- Add an optional `AGENT_REASONING_EFFORT` setting (values accepted by Groq for this model: `default`, `low`, `medium`,
  `high`, `none`) sent only when set. Default recommendation: `low`. Never hard-code it per provider by name.
- Log the HTTP status and a short error class for agent failures (already `outcome=http_404`); also log the base URL host
  (not the key) once at startup so a mismatched provider is obvious.

## C0. Rate limits (HTTP 429) and request size

**Diagnosed from the Groq console log (owner's screenshot):** `Request too large for model qwen/qwen3.8-27b ... on output
tokens per minute (OTPM): Limit 1000, Requested 2048. ... reduce max_tokens`. The owner's on_demand (free) tier allows 1,000
output tokens per minute for this model and the agent sends no `max_tokens`, so Groq reserves 2,048 and rejects the call before
running it (0 input tokens logged). The earlier 8K TPM figure from Groq's page is not this account's limit.

Required:
- Send `max_completion_tokens` on every agent model call, with two Render environment settings the owner tunes while testing:
  - `AGENT_MAX_OUTPUT_TOKENS` (default 500): calls made without tools (final answer / summary wrap-up).
  - `AGENT_TOOL_MAX_OUTPUT_TOKENS` (default 800): calls where tools are offered (tool arguments count as output).
  The code already knows which kind of call it makes (`allow_tools`). A call with tools offered may still end in a plain
  answer; it then keeps the tool cap. Both must stay below the provider's output-per-minute limit (the owner's free tier:
  1,000 OTPM, so two calls close together can still hit a 429; handle that as described below, do not retry).
  Read both values on every request so a changed Render variable takes effect after restart with no code change.
- Reasoning tokens count as output: default `AGENT_REASONING_EFFORT=low` (or `none`), see part B.
- A message can make up to 4 model calls; with a 1,000 OTPM allowance they can exceed it. Track output tokens used in the
  current minute (from the `usage` field of responses) and, if the next call could exceed the configured per-minute
  allowance (`AGENT_OUTPUT_TOKENS_PER_MINUTE`, optional), return the rate-limited result instead of calling.

- On 429, stop the loop at once. Do not call the model again in the same request. Read `retry-after` and the
  `x-ratelimit-*` headers and return a clear "provider is rate limited, try again in N seconds" result (status code the
  app turns into its own text; no built-in guidance phrases).
- Log, for every agent model call: HTTP status, `retry-after` if present, the provider error message from the response body
  (truncated to about 300 characters, never the key or request body), and an estimate of request size in tokens
  (system prompt + tool descriptions + messages).
- The owner saw the failure twice per message: the loop calls the model more than once. Make sure a failed first call is
  not retried when the cause is 429/401/404; only retry transient 5xx once with a short backoff.
- Measure the fixed cost of one request (system prompt + six tool schemas). If it is large against an 8K tokens-per-minute
  allowance, shrink the system prompt and tool descriptions without losing behaviour, and re-run the behaviour scenarios.
- Reasoning tokens count against limits too: send `reasoning_effort` as in part B, default `low`.

## C1. Tell the model its output budget, and detect truncation

Owner's Groq playground test (qwen/qwen3.8-27b, reasoning `low`, output cap about 500-1000 tokens): without being told the
limit the model wrote long answers and was cut off mid-sentence; once told the limit and reasoning setting it wrote a
complete, compact answer within the cap. Apply this to the agent. This is the free-tier / testing configuration; paid
users get a higher cap or a stronger model by changing settings only (no code change).

- Put the active output budget and reasoning setting into the agent's system instructions, generated from
  `AGENT_MAX_OUTPUT_TOKENS`, `AGENT_TOOL_MAX_OUTPUT_TOKENS` and `AGENT_REASONING_EFFORT` at request time (never hard-coded numbers). Wording is an internal
  instruction to the model, not app text shown to users; no sample user phrases.
- Instruct the model to keep answers short and self-contained, and to let tools return the real content: verbatim passages,
  exports and stored summaries come from the backend, never from model output, so a small cap does not shorten them.
- Check `finish_reason` on every response. If it is `length` (cut off), do not present the partial text as complete:
  mark the reply as cut off in the structured result (status code, app writes its own text), keep what was produced, and
  never retry blindly (a retry costs another output budget).
- Keep tool-call turns small: tool arguments count as output tokens.
- Test: a fake provider that returns `finish_reason=length` produces the cut-off status; the system prompt contains the
  configured numbers; changing the setting changes the prompt.
- Later (paid tier): raising `AGENT_MAX_OUTPUT_TOKENS`/`AGENT_TOOL_MAX_OUTPUT_TOKENS`, switching `AGENT_MODEL`, or changing provider must need only
  environment changes.

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
