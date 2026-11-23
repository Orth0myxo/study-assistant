# Task 19 — Token budget, tool filtering, retry settings

Branch: create `task-19-wip` from `task-18-wip` (at `74cd7ca` or later). Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. One item at a time, commit per item, update `SESSION.md` at the end.

## Why

The agent sends a lot of fixed text on every model call, before any user message, memory or tool result:

- `AGENT_TOOLS`: 12 tools, 6,932 characters of JSON.
- `_agent_system_prompt`: about 2,260 characters.
- Per-call system messages (context JSON, attachment metadata) and up to `agent_memory_tokens` (600) of memory.

By a chars/4 estimate that is about 2,500–3,000 tokens per call. A question that needs 3 model calls is about 8,000–9,000 input tokens. Groq's free tier allows 7,000 input tokens per minute per organization, and a paid tier is not available to the owner right now. Whatever provider is used later, smaller calls are cheaper and faster. These numbers are estimates; the real ones are in the Render logs (`agent_request_size`, `agent_turn tokens_in~`). Report the measured before/after numbers in `SESSION.md`.

Rate-limit retry (Task 18 T1) already exists. It does not help when the wait needed (about 45 s seen in logs) is longer than `agent_rate_limit_wait_s` (20 s) or `agent_request_timeout_s` (45 s). So this task makes each call smaller and aligns the timing settings.

## Rules (unchanged, they apply to every item)

- No built-in wording, phrase lists or keyword matching for user intent. Filtering uses facts about the chat (attachment kinds, conversation state, configuration), never the words of the message.
- Verbatim text comes only from stored text. AI output stays labelled.
- Every new behaviour writes a Render log line with ids, counts, timings and `outcome=`. No document text, no message text, no keys in logs.
- No new dependencies. No provider changes. Do not change provider defaults (see "Out of scope").
- Tool behaviour must not change. Only what is offered, and how it is described, changes.

## T1 — Offer only the tools the chat can use

Build the tool list per turn from facts already available in `run_agent_turn` (`material_attachments`, `_chat_materials`, `conversation.last_source_kind`, `conversation.last_result_id`, settings). Inspect the code and confirm each rule below before relying on it; report any rule you could not confirm.

Proposed rules (adjust only with a reason recorded in `SESSION.md`):

- No material attached: offer `list_materials` only (or no tools if the loop supports `allow_tools=False` and the prompt already says to ask for an attachment). Do not offer document or image tools.
- Image or outline source attached: offer `read_outline_image`.
- PDF or Word document attached: offer `list_headings`, `find_heading_sections`, `find_section`, `extract_heading_sections`, `find_passages`, `get_pages`, `summarize`.
- PDF only (a PDF is attached): also offer `read_page_table`. Offer `read_scanned_pages` only if its vision provider is configured and the document is a PDF.
- `build_export`: offer only when `conversation.last_result_id` exists or a tool offered this turn can create a result. Confirm which tools create results.
- `summarize`: offer when a document is attached or a result exists.
- Always keep `list_materials` when any material exists.

Also:

- If the model calls a tool that was not offered, `AgentTools.execute` must return a clear error result (`outcome=tool_not_offered`, tool name only) instead of running it. The error must not consume extra model calls beyond the existing limits.
- A filtered tool list must be stable within one turn (the same list for every model call in that turn, so provider-side prompt caching can work).
- Log per turn: `agent_tools_offered count=%d names=%s tools_chars=%d outcome=ready`.

Tests: one test per rule above (attachment kinds and conversation state in, tool names out), plus the not-offered error path.

## T2 — System prompt follows the tool list

The system prompt names tools (`list_headings`, `read_outline_image`, `read_scanned_pages`, and others). If a tool is not offered, its instructions must not appear, or the model will try to call it.

- Split `_agent_system_prompt` into a short base part plus one short snippet per tool group, assembled from the same list T1 produces.
- Keep every safety rule that exists today: untrusted source text, never call AI text verbatim, the `needs_approval` stop, never invent headings or page numbers, one confirmation only for section extraction.
- Remove duplicated sentences and anything a tool description already says.
- Do not add new wording rules for detecting intent.

Tests: for each tool, the prompt contains its snippet only when the tool is offered; the base rules are always present.

## T3 — Shorter tool descriptions and a smaller memory default

- Shorten every tool description and parameter description. Keep the information the model needs to choose between tools and to fill arguments. Remove examples and repeated rules.
- `agent_memory_tokens` default 600 → 300 and `agent_memory_turns` default 4 → 2. Confirm memory still carries the ids and short replies it needs for follow-ups (existing `test_agent_memory.py`).
- Do not rename tools or parameters, and do not change parameter types.

Size targets, measured as tools JSON characters plus system prompt characters (today about 9,200). If a target cannot be met without breaking behaviour, report the measured number instead of forcing it:

| Chat | Target (chars) |
|---|---|
| No material attached | ≤ 1,500 |
| Word document only | ≤ 4,500 |
| PDF (no images) | ≤ 5,500 |
| PDF plus image/outline | ≤ 6,500 |

Run the existing scenario and eval tests (`test_agent_scenarios.py`, `agent_eval/`) unchanged. They must still pass; do not edit them to make them pass. Tool choice quality is checked on the owner's device afterwards.

## T4 — Retry and timeout settings that fit each other

- `agent_rate_limit_wait_s` default 20 → 50.
- `agent_request_timeout_s` default 45 → 100 (it must be larger than the wait plus a normal call).
- Check every other timeout in the chain and report the values you find: the mobile app's request timeout in `mobile/services/`, and any server or proxy limit that is configured in the repo. If the mobile timeout is shorter than the backend one, raise it or show the existing honest wait message; do not add a spinner that hides a long wait.
- If the wait needed is longer than the remaining time, return the existing honest rate-limit message immediately and do not sleep.
- Log `agent_rate_limit wait_s=%.1f cap_s=%.1f remaining_s=%.1f outcome=waited|skipped_too_long`.
- Check how `agent_token_budget` (7,000) is used in the loop, and whether it is a per-turn local guard or a rate-limit estimate. Explain it in `SESSION.md`. Do not change it unless it is clearly wrong.

Tests: wait shorter than the cap retries; wait longer than the cap returns immediately; wait longer than the remaining time returns immediately.

## T5 — Token-budget test in CI

Add `backend/tests/test_agent_token_budget.py`.

- Build the system prompt and tool list for the four chat types in the T3 table, using the same code path as `run_agent_turn` (do not duplicate the lists).
- Assert tools JSON chars plus system prompt chars is at or below a ceiling for each chat type. Set each ceiling at the measured value plus 10%, and write the measured values in a comment.
- Assert that the default memory budget plus these fixed parts stays under 6,000 characters-per-call equivalent for the PDF case, using a conservative chars/3 estimate (no tokenizer, no new dependency).
- The failure message must name the chat type and the numbers, so a future change that bloats the prompt fails CI with a clear reason.

## T6 — Log lines and handoff

- Extend `agent_request_size` with `tools_offered=%d` and `fixed_chars=%d` (instructions + tools).
- Confirm no log line added by this task contains document text, message text or keys.
- Update `SESSION.md`: branch and commit per item, measured before/after sizes, test outcomes from Actions, known limits, and the next action. Include the exact Render lines the owner should read after the device test.

## Out of scope — owner decisions

- Choosing or changing the agent provider, model or endpoint.
- Changing the default of `SUMMARY_PROVIDER_ORDER` (currently `gemini,groq`). The owner does not want document text going to Gemini. This is a Render variable today; changing the code default is a separate, explicit decision.
- Changing `agent_max_model_calls`, `agent_max_tool_calls` or any tool's behaviour.

## Acceptance

1. GitHub Actions (Backend and Mobile) pass on `task-19-wip`; report run ids.
2. The T5 test exists, passes, and states measured sizes for all four chat types.
3. Size targets in T3 met, or measured numbers and the reason reported.
4. Existing scenario and eval tests pass unchanged.
5. `SESSION.md` updated. Nothing pushed to `feature/verbatim-groq-pipeline`.

Owner device check (after review): a Word-only chat, a PDF chat, and a chat with no material. For each, ask one verbatim request and one summary request. Read `agent_request_size` and `agent_turn tokens_in~` in the Render log and compare with the sizes in `SESSION.md`.
