# Task 19c — Keep partial detailed summaries when a part fails

Branch: create `task-19c-wip` from the latest of `task-19b-wip` (or `task-19-wip` if 19b is not done). Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. Commit per item, update `SESSION.md` at the end.

## Findings (from review of Task 18 T14)

In the detailed document summary loop in `agent_service.py` (`ai.summarize(...)` once per part, up to `agent_summary_model_calls`):

1. **One failed part loses all finished parts.** `ai.summarize` raises `AiAllProvidersFailed` (an `AiError`) when every configured provider fails, for example a rate limit. The only handler is the outer `except AiError`, which raises `summary_unavailable`. Parts 1 to N-1 that already succeeded in this call are thrown away, and the user gets an error instead of a partial summary with the Continue button. On a limited provider (7,000 input tokens per minute) the second or third part is likely to hit a limit, so this is the normal failure, not an edge case.
2. **Part accounting assumes no empty part.** `covered_end = start_part - 1 + len(text_parts)` counts only non-empty summaries. If one part returns empty text, the covered count, `next_part` and the covered pages go out of step with what was actually attempted, so the next call can skip or repeat a part.
3. **The deadline is checked but not enforced on the call.** The loop stops starting parts when `_remaining() < 2` (seconds), but `ai.summarize` gets no deadline. A part started with a few seconds left can run past the request deadline.

## Fix

1. Wrap each `ai.summarize` call in the loop so a provider failure ends the loop instead of the whole tool call:
   - If at least one part succeeded (in this call), stop the loop, keep those parts, and return the partial summary with `has_more: true`, `next_part` set to the first part not completed, and the existing `continue_prompt`. Add `stopped_reason: "provider_unavailable"` (use `"rate_limited"` when the error is `AiRateLimited` or the chain's errors are all rate limits) and, when the provider gave a retry time, `retry_after_seconds`.
   - If no part succeeded in this call, raise the existing `summary_unavailable` error as today, with the same rate-limit wording if every error was a rate limit.
2. Track the part number actually completed. `covered_end` must be the last part that produced text, in order. Stop at the first part that returns empty text instead of skipping it, report it as not covered, and make `next_part` point at it.
3. Do not start a part unless the time left covers a normal part call. Use a conservative minimum (for example the longest part time seen so far in this call, or a setting `agent_summary_part_min_seconds` defaulting to 15). Log the decision.
4. Logs, one line per stop: `agent_summary_partial parts_done=%d total=%d reason=%s next_part=%s outcome=partial|failed`. No document text.
5. The mobile Continue button already exists; confirm it still appears for `stopped_reason` partials, and that the block says honestly that the summary stopped early and why. No new wording lists.

## Tests (`backend/tests/`)

- Part 3 of 5 raises a provider failure after parts 1 and 2 succeed: the result keeps parts 1 and 2, `next_part` is 3, `has_more` is true, `stopped_reason` is set.
- The first part fails: the tool raises `summary_unavailable`.
- A part returns empty text: the loop stops there and `next_part` points at it.
- All provider errors are rate limits: the reason says rate limited.
- Not enough time left: the loop stops before starting a part and returns a partial summary with a continue prompt.
- Existing summary tests, including the five-part budget test, pass unchanged.

## Rules

- No built-in wording or phrase lists. No new dependencies. Do not rename tools or change parameter types. No provider changes and no changes to provider defaults. Logs carry ids, counts and `outcome=` only.

## Acceptance

1. Actions (Backend and Mobile) pass on `task-19c-wip`; report run ids.
2. The tests above exist and pass.
3. `SESSION.md` updated. Nothing pushed to `feature/verbatim-groq-pipeline`.

Owner device check: ask for a detailed summary of a long document on a rate-limited provider. Expect a partial summary with a Continue button and an honest note, not a failure message. Read `agent_summary_partial` in the Render log.
