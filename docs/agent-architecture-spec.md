# Agent architecture spec (proposal)

Status: proposal for the owner and the coding agent. Not built. The build order, server-side state, ambiguity rules, outline confidence, document-summary sampling, OCR handling and BYOK security decisions are in `docs/agent-implementation-plan.md`, which overrides this file where they differ (notably: conversation state lives on the server, not in the request `state`). Build only after Task 1
(lazy OCR) has been tested on a phone. Read `AGENTS.md`, `SESSION.md` and
`docs/pilot-test-findings-2026-11-15.md` first.

## 1. What the owner wants

One chat box and an attach button. The user attaches materials (a photo of a lecturer's course
outline, a PDF or DOCX, notes) and says in plain words what they want, for example:

- "These are my course topics in the photo. Give me the matching passages from the PDF exactly
  as written, as a Word file."
- "Summary only of cytology."
- "Verbatim plus a short summary of the first three topics."

The user never learns modes, never rewords a request, and is never asked to re-explain. If a
request is truly ambiguous the app asks one short question. The result is a clean on-screen
answer and, when asked, a well-organised PDF or DOCX.

Business model note: free with the user's own AI key, paid for users without a key.

## 2. Principles (do not break these)

1. **Verbatim is never written by the model.** Exact passages come from stored
   `Section.raw_text` (or a stored page image). Tools return passage ids; the app and the
   exporter render the stored text. The model never retypes source text.
2. **No phrase lists for intent.** The model chooses tools. The existing regex layers
   (`chat_commands.py` fallback, `chatOrchestrator.ts`, `LOCAL_*` patterns, `parseChatIntent`)
   are retired only after the agent passes the evaluation set (section 7).
3. **Bounded.** Hard limits on model calls, tool calls, tokens and time per request.
4. **Document text is data, not instructions.** It reaches a model only inside the summarize
   tool's prompt, never as part of the agent's own instructions.
5. **Grounded summaries.** AI text is always labelled as AI-generated and uses the existing
   word caps (`limit_words`).
6. **Small tool results.** Tools return ids, counts and short snippets, not full passages, so
   each model call stays small (free tiers allow only about 8,000 tokens per minute).

## 3. Overview

```
app  --POST /api/v1/agent/chat-->  backend agent loop
        message + attachment list     |
                                      |  1. model call: system rules + tool schemas + attachments
                                      |  2. model returns tool calls
                                      |  3. backend validates and runs tools (existing services)
                                      |  4. short tool results go back to the model
                                      |  5. model returns a final short reply (or a question)
        <-- reply + blocks -----------+
```

Typical request: 2 model calls plus 1 vision call (when an outline photo is read).

## 4. API

`POST /api/v1/agent/chat` (same auth and owner scoping as other routes; enabled by
`AGENT_ENABLED=true`).

Request:

```json
{
  "message": "get the topics in the image from the pdf as written, in word",
  "attachments": [
    {"id": "doc_1", "kind": "pdf", "name": "Test 1.pdf", "pages": 53},
    {"id": "img_1", "kind": "image", "name": "outline.jpg"}
  ],
  "state": {"last_result_ids": []}
}
```

Response:

```json
{
  "reply": "Found 8 topics; 7 matched. Your Word file is ready.",
  "blocks": [
    {"type": "passages", "result_id": "res_1"},
    {"type": "file", "file_id": "file_1", "format": "docx", "name": "PATH503-topics.docx"}
  ],
  "warnings": [],
  "usage": {"model_calls": 2, "tool_calls": 3}
}
```

`blocks` reference stored results by id. The app fetches passage text from the backend.
A `question` block (`{"type":"question","text":"...","options":[...]}`) ends the turn when the
model needs one clarification.

## 5. Tools (thin wrappers over existing services)

Every tool takes validated Pydantic arguments, checks that the caller owns every id, and
returns a small JSON result. Unknown tools or bad arguments return an error to the model once,
then the request fails with a clear message.

| Tool | Arguments | Returns | Wraps |
| --- | --- | --- | --- |
| `list_materials` | none | attachments with ids, kinds, pages, whether image-heavy | storage / `db.py` |
| `read_outline_image` | `image_id` | `topics[]` with confidence; flags unreadable lines | outline vision flow in `topic_sources.py` / `topic_images.py` (bf214bf) |
| `find_passages` | `document_id`, `topics[]` | `result_id`, per-topic match count and confidence, short snippets (at most 120 characters) | `topic_matching.py`, `topic_results.py`, lazy OCR from Task 1 |
| `get_pages` | `document_id`, `pages` or `range` | `result_id`, page list | `range_extraction.py` |
| `summarize` | `result_id`, `length` (short or medium), `per_topic` bool | AI summaries, labelled, capped | `topic_study.py`, `limit_words` |
| `build_export` | `result_id`, `format` (docx or pdf), `include` (verbatim, summary, page_images) | `file_id`, name | `exporter.py` (rebuilt in Task 2) |

The model replies with prose only at the end; the answer content lives in `blocks`.

## 6. The loop

- Cap: at most 4 model calls and 6 tool calls per request, a per-request token budget, and a
  wall-clock limit (about 45 s).
- Tool results are truncated to a fixed size before going back to the model.
- Call the provider chain with tool calling through a generic OpenAI-compatible agent
  endpoint; Groq and Gemini remain summary/command providers. Add
  `AGENT_PROVIDER_ORDER` separate from the summary order and verify tool calling per model.
- Final reply is requested with no tools; if the model fails twice, return the tool results
  that already exist plus a plain message (never an empty error).
- Log each model and tool call (name, seconds, outcome, never message or document text) via
  the existing `log_provider_call`.
- Reasoning models spend thinking tokens: do not set a small `max_tokens`; bound with the
  call and time caps instead.

Bring-your-own-key: accept `X-AI-Provider` and `X-AI-Key` headers per request, use them for
that request only, never store or log them. The mobile app keeps the key in secure storage.

## 7. Evaluation (superseded)

The former phrase table and `backend/tests/agent_eval/cases.json` are removed. Task 5 uses
the owner's no-phrase rule and the behaviour scenarios in
`docs/agent-implementation-plan.md` section 5.8: scripted mechanics tests, fixture-based
outcome checks, and an on-demand live-model run with generated wording. No natural-language
request sentence is stored as an expected input.

Still test: tool argument errors, step cap reached, provider timeout, ownership violation,
and document text containing instructions that must not change behaviour.

## 8. Migration (small steps, each tested on a phone)

1. Backend only: tool registry, loop, route behind `AGENT_ENABLED`, fixture behaviour
   scenarios, scripted-model tests. No app change.
2. Mobile (needs a new APK): one call to `/agent/chat`, one block renderer (passages, summary,
   file, question), and the legacy workspace layout on the agent route. The agent route is the
   default; the old path is used only for saved sessions or when the backend reports
   `agent_disabled`.
3. After a pilot week and the evaluation target, remove the retired pattern layers.
4. Later: bring-your-own-key UI, accounts and payments, persistent storage.

## 9. Open decisions for the owner

- Which provider and plan for the agent (free tiers are too small for several testers).
- Whether a handwritten outline must always be confirmed by the user before matching (recommended
  when confidence is low).
- Maximum pages and files per request for the paid and free paths.
