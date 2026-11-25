# Task 20 — Clean replies, honest labels, and note-image reading

Branch: create `task-20-wip` from `feature/verbatim-groq-pipeline` (at `2c5ba07` or later). Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. One item at a time, commit per item, update `SESSION.md` at the end.

These come from the owner's device test of build `2c5ba07` and a code review. Summaries, Continue and verbatim pages worked. This task fixes the visible problems around them.

## Rules (apply to every item)

- No built-in wording or phrase lists for intent. Detection and filtering use structure and facts (tool schemas, attachment kinds, configuration), never the words of the user's message.
- Verbatim text only from stored text. AI output stays labelled as AI output.
- Every new behaviour writes a Render log line with ids, counts, timings and `outcome=`. No document text, message text, image content or keys in logs.
- No new dependencies. Do not rename tools or change existing parameter types. No provider changes and no changes to provider defaults.
- Existing tests, including the Task 19 filtering and token-budget tests, pass unchanged unless an item below says a test changes.

## N1 — Never show the model's narration as the reply

Finding: in the terminal-tool path of `agent_loop.py` (`if terminal_candidate and terminal_tools:`), the reply is `turn.text.strip()` whenever it is 240 characters or shorter. The model often writes planning text next to a tool call ("The user wants a summary… Let me check the available materials first…"). That text was shown above the summary block on the owner's phone. It also costs output tokens.

Do:

- When a terminal result is returned, always use the fixed reply from `_TERMINAL_REPLIES` (add an entry for any terminal tool that lacks one; keep the default `"The result is ready."`). Never use `turn.text` there.
- Text that arrives together with tool calls (any step that returns tool calls) is never shown to the user. It stays in the model history only.
- Do not change non-terminal final replies (the model's answer after tools) except through N2 and N3.
- Log `agent_reply_source source=fixed|model outcome=ok` once per turn.

Tests: a fake model turn with narration text plus a terminal tool call returns the fixed reply and the result block; narration longer and shorter than 240 characters both give the fixed reply; a plain text turn with no tool call still returns the model text.

## N2 — Catch every pseudo tool-call format

Finding: the guard `_TOOL_MARKUP_RE` only matches `<tool_call>`, `<function=`, `<parameter=` styles. On the owner's phone the model wrote `[BEGIN] [TOOL] [PARAMS: {"source_id":"…","start_image":1}] [END]` as its reply and it was shown to the user.

Do:

- Keep the existing regex. Add a second, structural check built from the offered tool schemas, not from a list of formats: a reply is treated as a pseudo tool call when it contains (a) an offered tool name or (b) a quoted JSON key that equals a parameter name of an offered tool, in a JSON-object context (`{"key":`), together with any bracket or angle-tag delimiter around it. Compute the parameter-name set from `tools_for_turn` at the start of each turn.
- A reply matching either check follows the existing path: log `agent_tool_markup_stripped`, retry once with the existing system note, then return the existing "could not be completed" reply. Never show the raw text.
- Do not flag replies that merely mention a tool name in a sentence with no JSON object or delimiter.

Tests: the exact bracket format from the device test is caught and retried; the angle-tag formats still are; a normal sentence that names `list_materials` is not flagged; a valid JSON example inside an explanation without delimiters is not flagged.

## N3 — Label answers that are not from the uploaded material

Owner decision: answering from general knowledge is allowed, but it must be clearly marked as not coming from any uploaded material.

Do:

- Add one rule to the base system prompt: when the reply is not based on stored material or a tool result, the first line must be exactly `Not from your uploaded material:` followed by the answer. When the reply is based on a tool result, it must not use that line.
- Define the label string once in a constant, shared by the prompt and the tests. The mobile app shows replies as plain text, so no UI change is needed; if the app already styles a label, reuse it.
- Keep the Task 19 prompt size ceilings. If the prompt ceiling fails, shorten other wording; do not raise the ceiling.
- This is a prompt rule, so the model can still forget it. Log `agent_turn_origin tool_calls=%d label_present=%s outcome=ok` for replies that follow zero tool calls, where `label_present` is whether the reply starts with the constant. This tells the owner how often the rule is followed. It does not block anything.

Tests: the prompt contains the constant; the log line is written for no-tool replies with and without the label.

## N4 — Read note photos into text, and summarize them

Owner decision: images in chat must support both jobs: topic-list reading (exists) and note-photo reading (new).

Finding: the only image tool, `read_outline_image`, is described as reading ordered topics from a topic-list image and returns topics only. For "summarize my images" the model correctly decides it does not apply, and never calls a tool, so the AI-read consent pop-up never appears.

Do:

- New tool `read_image_text`: takes `source_id` (an image source owned by the user, as in `read_outline_image`) and `start_image` (continue reading). Up to two images per call, the same continuation fields as `read_outline_image` (`has_more`, `next_image`, `continue_prompt`).
- Use the existing consent flow exactly (`check_ai_read_consent`, `needs_approval`, the per-user "remember my choice" and the Settings toggle, `ai_read_never_allowed`). Add an image-text method to the image reader that returns transcribed text for a photo (the same provider, selected by `IMAGE_READING_PROVIDER`: `groq` with `GROQ_VISION_MODEL`, or `gemini`). If no vision provider is configured, fall back to the existing local OCR for these images if the code has it, otherwise return a clear `not_configured` error and do not offer the tool (see offering rules).
- Store the text as a result, kind `image_text`, with a result id, the image count, and a label that the text is AI-read from a photo and may contain mistakes. It is not source text and must never be presented as verbatim. Keep read text out of logs; log only counts.
- Make the stored text summarizable with the existing `summarize` tool through `result_id`, using the same chunking, partial results and continuation as document summaries. Summary blocks keep the existing "AI summary, not source text" label, and the basis line says it is based on AI-read photo text.
- Exports of an `image_text` result keep the AI-read label.
- Offering rules for the Task 19 tool filter: offer `read_image_text` when the chat has an image source and a vision provider is configured (or local OCR is available). Keep `read_outline_image` for topic lists. Both tools may be offered together. Update the system prompt snippets so the model picks by what the user asked, with no wording lists: one snippet says "topic list or outline images: read_outline_image", the other "photos of notes or pages to read or summarize: read_image_text". Keep both descriptions short and respect the token ceilings.
- Resume: the tool set stored with a pending approval (Task 19b) must include `read_image_text` when it was offered, and resuming an approved read must continue the same call.
- The model must not claim it cannot read images when `read_image_text` is offered. Add this to the image snippet in one sentence.
- Logs: `agent_image_text source_id=%s images=%d provider=%s outcome=ok|needs_approval|blocked|failed`, no content.

Tests: tool offered only with an image source and a provider; not offered for Word or PDF-only chats; consent states (ask, always, never, remembered) behave as for `read_outline_image`; resume after approval; a summary of an `image_text` result produces a labelled summary block; the not-configured case.

Owner device check after review: attach several photos of notes, ask for a summary, approve the pop-up once, and expect a labelled summary. Then repeat with "remember my choice" on and off. Also read topics from a topic-list photo to confirm that still works.

## N5 — Handoff

- Update `SESSION.md`: branch, commits, CI run ids, the measured prompt and tool sizes after N3 and N4, and the exact Render log lines to read after the device test (`agent_reply_source`, `agent_tool_markup_stripped`, `agent_turn_origin`, `agent_image_text`).
- Update the Task 19 token-budget test ceilings only if N4 adds a tool to an existing chat type; report the new measured values in `SESSION.md` and keep the 10% headroom rule.

## Not in this task

- Extra summary or extra pages appended after a verbatim request. The owner is collecting the Render log lines for it. Do not change `get_pages`, `find_passages` or the terminal tool set until that diagnosis is attached to a later task.
- The 260-second rate-limit wait, provider choices, the summary provider order, and web build work.

## Acceptance

1. GitHub Actions (Backend and Mobile) pass on `task-20-wip`; report run ids.
2. Tests for N1 to N4 exist and pass; existing tests pass unchanged except as stated.
3. `SESSION.md` updated. Nothing pushed to `feature/verbatim-groq-pipeline`.
