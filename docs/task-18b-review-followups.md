# Task 18b — Review follow-ups (memory, section ambiguity, handoff docs)

Branch: create `task-18b-wip` from `task-18-wip`. Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. One item at a time, commit per item, update `SESSION.md` at the end.

These come from a code review of Task 18 (T1, T2, T8, T15). Task 19 is separate and unaffected; keep the two branches independent and merge order for the owner to decide.

## Rules (apply to every item)

- No built-in wording, phrase lists or keyword matching for user intent.
- Verbatim text only from stored text. AI output stays labelled.
- Every new behaviour writes a Render log line with ids, counts, timings and `outcome=`. No document text, message text or keys in logs.
- No new dependencies. Do not change tool names or parameter types.

## F1 — Do not save failed or incomplete turns to chat memory

Finding: `run_agent_turn` calls `_remember_turn(...)` unconditionally after the loop (and `resume_agent_turn` does the same). The loop can end with `agent_time_limit`, `agent_output_truncated`, `agent_tool_markup` or `needs_approval`. Those replies are saved as assistant memory and sent to the provider on later turns, where they can confuse the model and waste the memory budget. Check whether other failure paths (for example a rate-limit message) also reach this call, and report what you find.

Do:

- Save a turn to memory only when the turn finished normally. List the accepted statuses in one constant, derived from the statuses the loop actually returns; do not match on reply text.
- For a skipped turn, still keep the user message out of memory too, so memory never holds a user message without its reply.
- Never store two consecutive messages with the same role. If `_bounded_memory` drops an empty reply, drop its user message as well.
- Log `agent_memory_saved turn_saved=true|false status=%s memory_turns=%d outcome=ok|skipped_status`.

Tests: one test per skipped status (memory unchanged), one for a normal turn (memory saved), one for the paired-message rule.

## F2 — Say when a section match is ambiguous

Finding: `resolve_heading_section` returns the first match when several headings match, including exact heading text, a typed number and a contained title. Documents often repeat headings (Introduction, Summary) and numbers (one `1.1` per chapter). A wrong section returned as verbatim text is a correctness risk. A fuzzy "closest" match is stored with `confidence: 1.0`, which overstates it.

Do:

- In `resolve_heading_section`, collect all matches at the winning level (exact, then number, then contained title). If there is more than one, return no section and the candidate list with enough to tell them apart (heading text, heading path, first page). Add a `match_kind="ambiguous"` outcome.
- `find_section` then returns `found: false`, `ambiguous: true` and `candidates` (at most 8), and does not create a result. The model must ask the user which one; the system prompt already allows one focused question.
- For a `closest` fuzzy match, store `confidence` as the match ratio (0–1), set `low_confidence: true` below 0.8, and keep the existing note naming the matched heading.
- Candidates come from stored headings only. Do not rank by wording lists.
- Log `agent_find_section ... kind=ambiguous candidates=%d outcome=ambiguous`.

Tests: duplicate exact headings, duplicate numbers, one exact among several partial matches (exact wins, no ambiguity), a fuzzy match with a ratio below 0.8, and the single-match cases unchanged.

Note for the owner, not for this task: Word automatic numbering is still not in the stored text, so a bare number such as `8.4` on an auto-numbered Word file falls back to nearest headings. That is honest, but it is a known limit until a task builds numbers from the file's list definitions.

## F3 — Make the handoff docs match the code

- `AGENTS.md`: the architecture paragraph says model command interpretation receives no chat history. The agent now sends bounded recent turns (T2). Update the paragraph to say exactly what the agent sends: the system prompt, server-owned metadata, attachment metadata, up to `agent_memory_turns` recent user and assistant messages (each cut to 800 characters, `agent_memory_tokens` total), tool schemas and tool results. State that saved assistant replies may contain heading names or short quoted source text. Keep the wording factual; no marketing claims about privacy.
- Add one short privacy note in `SESSION.md` listing what is stored (`memory_json` in `agent_conversations`) and that it is wiped on a server restart on the current free hosting.
- Record the owner decisions still open: the agent provider, the summary provider order, and Groq zero data retention.

## F4 — Measure the per-send attachment check

Finding: before every send the app calls `getDocument` for each attached PDF or Word file (and `getTopicSource` for images) to detect a wiped server. `getDocument` may return a large record.

Do:

- Measure the response size of `GET` for a document with many sections, from the existing tests or fixtures; report the number in `SESSION.md`.
- If it is large, add a lightweight check (a `HEAD` or a minimal existence endpoint that returns 200 or 404 and no body) for existing materials, and use it in `findMissingAttachments`. Keep the 404-only rule: any other error means "not missing".
- If it is small, change nothing and say so.
- Log nothing new on the device; the existing `outcome=storage_missing` backend lines are enough.

Tests: if an endpoint is added, 200, 404 and owner-mismatch cases.

## Acceptance

1. GitHub Actions (Backend and Mobile) pass on `task-18b-wip`; report run ids.
2. F1 and F2 tests exist and pass; existing tests unchanged and passing.
3. `AGENTS.md` and `SESSION.md` updated as in F3.
4. F4 measurement reported, with a decision.
5. Nothing pushed to `feature/verbatim-groq-pipeline`.

Owner device check (after review): attach a Word or PDF file with repeated headings and ask for a section by its repeated name. Expect a question listing the candidates, not a section. Then cause a time-limit or approval stop and check that the next question does not echo the failed reply.
