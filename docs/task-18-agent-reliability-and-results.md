# Task 18: agent reliability, chat memory, results and materials

Owner decision (2026-11-22): do this together with the finished Task 17B, then build one APK and retest everything.
Source: the owner's phone tests and Render logs of 2026-11-21 (7 separate images, a 98-section DOCX, Qwen and gpt-oss-120b as
agent models, Groq free-tier 429s).

Base: branch `task-17b-wip` (head 08a2d65 when this was written). Create `task-18-wip` from it and push only there. Do not push
`feature/verbatim-groq-pipeline` until the owner has reviewed.

Rules (same as always): follow `AGENTS.md`; one piece at a time (test, fix, retest); CI only unless the owner asks; no built-in
wording, phrase lists, keyword or regex intent detection for what the user wants; verbatim text only from stored text, AI output
labelled; report real results and what is unverified; update `SESSION.md`; every new behaviour writes a Render log line (ids,
counts, timings, `outcome=`, no document text, filenames or keys). Line numbers below are from 08a2d65 and may have moved.

Order: T1, T2, T11, T12, T13, T3 (these affect every message), then T8, T9, T10, T14, T4, T7, T5, T6, T15.

## T1. Rate limits (Groq free tier)

Evidence: Groq limits this model to 7,000 input tokens per minute for the whole organization (log: `Limit 7000, Used 4993,
Requested 3102`). One question with tools is three model calls of about 1,666 + 2,455 + 2,600 input tokens, so one question fills
the minute. On a 429 the app asked the user to retry; the retry re-ran the whole turn (searches included) and failed again. The
server said "try again in 10 s" while the token budget needed about 43 s (`x-ratelimit-reset-tokens`). The first call of a turn grew
from about 1,150-1,200 tokens before 17A to about 1,570-1,670 after; find out why.

- Pointers: `agent_models.py` `_http_failure` (~158-182, rate limits are classified `agent_rate_limited`, not retryable),
  `agent_loop.py`, `api/agent.py` (error map ~41, retry_after ~105 and ~142).
- If a model call gets a 429 and the wait is within a configurable limit and the request time budget, wait and retry that same call
  with the same history and the tool results already gathered. Never run completed tools again. Otherwise return the error with an
  honest wait (use the larger of `retry-after` and the token reset header).
- Cut input tokens per call: measure the size of the instructions and the tool schemas, shorten them, and give the model only what
  it needs from tool results (ids, short snippets). Verbatim text goes to the user in the result block, not back through the model.
- Skip the final model call when the tool result is itself the answer (verbatim, export, summary block), or make it a minimal call.
  Say which you chose and log the tokens saved.
- Log one line per turn: `agent_turn calls=… tool_calls=… tokens_in=… retries=… outcome=`.
- App: do not show "try again in N seconds" as the only option; show waiting state when the server retries, and block sending the
  same message again while a retry is in progress.
- Test: simulate a 429 on the third call; assert one retry of that call, tools executed once, and one final answer.

## T2. Chat memory

Evidence: the model receives only the instructions, metadata (`Server-owned prior result references (metadata only)`, ~line 2925)
and the new message. It never sees its earlier replies, so "that page" or "the second point" cannot be resolved ("The previous
result doesn't reference any specific page number").

- Send the last few turns (user message and the assistant reply), bounded by a configurable token budget, with long replies
  trimmed to their start. Keep full results on the server by `result_id`; the model gets the id and a short excerpt.
- Store recent turns with the server-side conversation record (or send them from the app's on-device history; choose one and say why).
- The memory is part of what goes to the AI provider; keep it bounded and log its size (`memory_turns=… memory_tokens=…`).
- Test: ask for a summary, then "give more detail on the second point"; the model's request must contain the earlier turn. Test the bound.

## T11. `summarize` defaults to the active document

Evidence: with gpt-oss the model called `summarize` with no source and got `result_required` (twice), then asked the user which
part to summarise. The server already knows the active document.

- Pointer: `_summary_context` (~2168, error at ~2231). With no `result_id` or `document_id`: use the chat's active document
  (`conversation.last_document_id`) with scope document; if there is a last result and the request is about it, use that.
- Tool descriptions say this plainly. Test: `summarize` with no arguments on a chat with one document.

## T12. `get_pages` on Word files returns sections

Evidence: `get_pages … outcome=pages_not_supported` on the DOCX; a Word file stores no page numbers (`pages=None`), only sections.

- Pointer: `_get_pages` (~1742). For a DOCX return the requested sections (by index or heading) in the same shape, with a short note
  that Word files have sections, not pages. Do not invent page numbers.
- "Sections" here means the blocks (paragraphs and headings) the app splits the text into when the file is uploaded; it does not
  mean numbered sections such as 8.1. Numbered headings are handled in T8.
- Test: DOCX with 98 sections, request a range.

## T13. What you see is what you export

Evidence: the in-app summary (detailed, `chunks=2 covered=2 of 5 parts`) and the exported PDF (eight short bullets) were different
texts. The export took 0.29 s with no AI call, so it used an already stored summary.

- Pointers: `_build_export` (~2534), `agent_loop.py` (17A `display_value` vs the value sent to the model), `agent_exports.py`.
- Every result block carries its `result_id`; Export and `build_export` use exactly that id, and the stored payload equals the
  displayed text (not a truncated or older result). Find which of the two causes applies and fix it.
- Test: generate a summary, export it, and assert the exported text equals the displayed text; repeat with two summaries in one chat.

## T3. Narration and cut-off replies

- One short line before tool calls is fine; no multiple paragraphs of narration (they used up the 500-token final cap).
- "The reply was cut off" (`agent_output_truncated`): long answers should come from tools and result blocks, not the final chat
  turn. If a final reply is still cut, offer a Continue action that uses T2 memory.
- Test: a "give me detail" request must not end cut off at the default caps.

## T8. Verbatim requests: pinpoint, never dump

Evidence: asking for verbatim text produced a very long "Document sample" list of the whole material (`"topic": "Document
sample"`, ~line 2495) with Up/Down/Remove buttons; asking for "section 8.4" returned something else.

- Resolve a typed section number or heading text (for example "8.4", "What is a genetic abnormality?") against the stored headings:
  exact match first, then the closest match. Return that whole section (heading down to the next heading of the same or higher
  level), never a whole-document sample and never just one line. Plain text matching on stored headings, no AI. This is 17B B5; reuse it.
- Section numbers: if the numbers are typed in the heading text (for example "8.4 Prevalence"), match them as text. If the Word
  file uses automatic numbering, the number is not in the stored text; check how python-docx exposes it, and either rebuild the
  numbering from the file's list definitions or fall back to matching the heading title. Say which you did and test with both kinds
  of file.
- Closest match: show it with a short note of which heading it matched. Nothing found: say so and offer a short list of the nearest headings.
- Remove the Up, Down and Remove controls and the "Removed from export" area from the verbatim block (`AgentWorkspaceScreen.tsx`
  ~1509-1640). Keep Export.
- Measure: build a small test set (the owner will supply 10-20 requests from his own documents: section numbers, heading names,
  partial names, typos) and report the hit rate. The owner's aim is about 90% close; report the real number, do not assume it.

## T9. Result blocks are read-only

- Summaries and verbatim text in chat are plain, selectable, copyable text. Remove the editable text box from summary blocks.
- Keep editing only where accuracy needs it: File Converter tables and AI-read text corrections (`aiReadDraft`, ~1124 and ~1455).
  Locate the summary edit box (`TextInput` around ~1050) and confirm which one it is before removing.
- Test: render a summary and a verbatim block; no text input is present.

## T10. Dead-end notices

- Replace "No readable outline topics were found. Upload a clearer image or type the topics." (`agent_service.py` ~1610) and
  "No readable topics were detected. Add topics manually or try a clearer image." (`topic_sources.py` ~275) with one helpful
  sentence built from facts (what was read, how much, what to do next).
- If the image is not a topic list, offer to read it with the AI reader (17B consent) instead of "upload a clearer image".
- Test: attendance-sheet photo and an ordinary photo through the outline path.

## T14. Whole-document summaries cover every part

Evidence: `summary length=detailed chunks=2 covered=2 of 5 parts`. The limit was the per-question model-call budget
(`agent_max_model_calls` = 4, shared; ~2354, ~2370, ~2423, ~2456), while each Gemini chunk takes about 1 s.

- Give summary chunks their own budget (a setting, default about 8) separate from the agent loop calls; run them on the summary
  provider chain. Respect provider limits and fall back as the chain already does.
- Keep the coverage line honest. If a document is still only partly covered, offer a Continue action that resumes from the next part.
- Check the request time budget (`agent_request_timeout_s`). Test: a 5-part document is fully covered; a limit forces a partial result with Continue.

## T4. Several images in one upload

Evidence: seven images became seven separate sources ("1 image(s)" chips). `agentWorkflow.ts` (~178) calls
`createTopicSource(null, files)` once per upload; the picker helper (`services/imagePicker.ts`) already allows multiple selection.

- Group the selected images into one source (the backend accepts several images per source), up to a sensible limit (say 10).
- The agent can read all images of a source (17B reader, with the consent flow, in small batches). Test: select 7 images, one chip.

## T7. One definition of "what is in this chat"

Evidence: "Based on the metadata… 7 images" and then "Only one item is available to me". After 17A the agent could only read the
active source.

- Define the chat's materials as everything attached to this chat (documents and image sources). `list_materials` returns them
  all with an `active` flag; counting, reading and "summarise all images" use that same set and iterate over it.
- Test: seven images attached, ask to count and to summarise all; the answers agree.

## T5. Remove images and documents

- `deleteDocument` and `deleteTopicSource` exist in `apiClient.ts` (~1161, ~1367) but the agent screen has no control. Add a remove
  action to each material chip with a confirmation, update the on-device chat history that refers to it, and log the deletion.
- Test: remove an image and a document; they leave `list_materials`.

## T6. Word files without headings or pages

- Use sections for Word files (T12) and the 17B headings tool where headings exist. Plain text with no headings: show the matching
  paragraphs with a few before and after and let the user extend above or below. Optional, only if cheap: approximate page labels
  clearly marked as approximate.
- Test: a DOCX with headings and one without.

## T15. Server restart message

Evidence: changing a Render variable restarted the service and free-tier storage was wiped; the app still showed the document and
image chips and only failed later with "This result has expired" and "A source in this chat is no longer available. Reattach the
file and try again." (`agentWorkflow.ts` ~104-106, `AgentWorkspaceScreen.tsx` ~1503). The owner only learned of the restart from
that error.

Owner decision (2026-11-22): the notice is a pop-up (toast or banner), never a chat message, and the chips of materials that no
longer exist must disappear.

- Detect the restart: the server exposes a boot identifier (for example in `/health` next to the commit it already reports) or the
  app re-checks its materials against the server when it opens, returns to the foreground, and before sending a message. A changed
  boot id, or materials missing on the server, means storage was cleared.
- Show one pop-up that says the server restarted, the files were cleared, and offers to reattach. Not added to the chat transcript.
- Remove the chips (and any pending use) of materials the server no longer has. If the app still holds the picked file locally,
  offer one-tap re-upload from the pop-up.
- Results already shown in the chat history stay readable on the device; only their server-side actions (export, more detail)
  report that the source is gone.
- Log when stored materials for a user are found missing (`outcome=storage_missing`).
- Test: simulate a changed boot id and a missing document; the pop-up appears, no chat message is added, and the chips are removed.

## Not in this task

File Converter cleanup (doubled AI-summary button, layout, incomplete tables), question generation, privacy notice, app icon,
interface polish, hosting and persistent storage, switching models (the owner is testing models through Render variables).

## Owner verification after the next APK

Combined with 17B: two quick questions in a row (T1), a follow-up that refers to the last answer (T2), summary exported equals
summary shown (T13), whole 5-part DOCX summary (T14), "section 8.4" style verbatim request (T8), seven images in one upload (T4),
remove a material (T5), and the 17B items still open: a real vision-provider read, the 76-page deck, and device approval/resume.
