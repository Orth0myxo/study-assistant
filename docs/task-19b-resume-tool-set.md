# Task 19b — Keep the same tool set when an approved turn resumes

Branch: create `task-19b-wip` from `task-19-wip` (at `6106e60` or later). Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. Commit per item, update `SESSION.md` at the end.

## Finding (from review of Task 19 T1)

`run_agent_turn` builds the offered tool list from every attachment the app sends plus the active source (`material_kinds`). `resume_agent_turn`, which continues a turn after the user approves a pending call, has no attachments. It rebuilds the list from `conversation.last_source_id` / `last_source_kind` only, one source, one kind.

So a resumed turn can offer a different, narrower tool list than the turn that was paused:

- Chat with a PDF and a topic-list image, active source is the image. The first turn offers document and image tools. The user approves AI reading of the image. The resume offers only `list_materials`, `read_outline_image` and possibly `build_export`/`summarize`. After the outline is read, the model cannot call `find_passages` or any other document tool; the call returns `tool_not_offered`. This is the main use case (photo of a topic list plus the lecturer's PDF).
- Active source is a Word or PDF file while the pending call is `read_outline_image` for another attachment in the chat. The approved call itself fails with `tool_not_offered`.
- The resumed model also still has the original system prompt (it is stored in `pending.messages`), which describes tools that are no longer offered.

No test covers resume with more than one attachment kind. Task 19's "the list is stable within one turn" is not true across an approval.

The same single-source rebuild also affects `list_materials`: `resume_agent_turn` passes `resume_materials` (only the last source) to `AgentTools`, so after an approval `list_materials` returns one material, or none, instead of every attached file. Requests such as "all my materials" then give wrong counts after an approval.

## Fix

1. Store the offered tool names and the chat's attachment list (`id` and `kind` only) on the pending record when it is saved (`AgentPendingTurn`: add `tool_names: list[str]` and `attachments: list[dict[str, str]]`). Use the same values the turn used (`tools_for_turn`, `material_attachments`). Add the storage column or JSON field with an in-place migration, the same way schema v21 added `memory_json`. Old pending rows without the fields are treated as missing (see 3). Store no filenames or text.
2. In `resume_agent_turn`, rebuild `tools_for_turn` by filtering `AGENT_TOOLS` to the stored names, and rebuild `chat_materials` with `_chat_materials(owner_id, stored_attachments)`. Do not derive either from `last_source_kind` any more. Keep the `offered_names` check in `AgentTools.execute`.
3. If a pending row has no stored names (created before this change), fall back to the current behaviour and log `agent_resume_tools source=fallback outcome=legacy_pending`. Pending rows expire quickly, so this is a short transition, not a permanent path.
4. Make sure the pending call's own tool name is always in the resumed list. If it is not, log `agent_resume_tools outcome=pending_tool_missing` and return the existing `agent_pending_expired` response instead of failing inside the loop.
5. Log once per resume: `agent_resume_tools count=%d source=stored|fallback outcome=ready`. No document text.

## Tests (`backend/tests/`)

- A chat with a PDF attachment and an image attachment, active source the image. Pause on `read_outline_image` approval, resume, and assert the resumed tool list equals the original, and that `find_passages` can be called after the outline is read.
- Active source a Word file, pending call `read_outline_image` from another attachment. Resume runs the approved call without `tool_not_offered`.
- Legacy pending row without `tool_names` takes the fallback path and still resumes.
- Pending tool missing from the stored names returns `agent_pending_expired`.
- A normal single-attachment resume still offers the same tools as before.
- With two documents and an image attached, `list_materials` after a resume returns all three, with one marked active.

## Rules

- No built-in wording or phrase lists. No new dependencies. Do not rename tools or change parameter types. No provider changes. Logs carry ids, counts and `outcome=` only.
- Existing tests, including Task 19's filtering and token-budget tests, must pass unchanged.

## Acceptance

1. Actions (Backend and Mobile) pass on `task-19b-wip`; report run ids.
2. The five tests above exist and pass.
3. `SESSION.md` updated with commits, run ids and the next action. Nothing pushed to `feature/verbatim-groq-pipeline`.

Owner device check after review: attach a PDF and a photo of a topic list, ask for those topics verbatim, approve AI reading of the image. Expect the passages result, not a "tool not available" message. Read `agent_resume_tools` in the Render log and confirm `source=stored`.
