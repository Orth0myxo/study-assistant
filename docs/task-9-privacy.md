# Task 9: privacy review and fixes

Same working rules: one piece at a time (test, fix, retest), no built-in wording, report what was not verified.

## What the code does today (read from the repo, not yet audited line by line)

- Users are anonymous device identities (`POST /api/v1/users` returns a bearer token; only its hash is stored).
- Documents, results and exports are scoped by `owner_id`; there is a delete endpoint for a document.
- Uploaded files are removed after `retention_minutes` (default 10080 = 7 days) by a sweep.
- The shared application token ships inside the APK, so it is not a secret; the per-device bearer token is the real identity.
- AI providers receive document text for summaries and agent tools. This is the main privacy exposure.
- Agent conversation state is kept server-side (`last_document_id`, `last_result_id`, `last_topics`).

## Work items

1. **Data-flow map.** Write `docs/privacy-data-flow.md`: for every field (file, extracted text, page images, summaries,
   chat messages, topics, filenames, device token) say where it is stored, how long, who can read it, and which
   third party receives it. Verify from code, not memory.
2. **Provider disclosure.** The app must tell the user, before first upload, that text is sent to the configured AI
   provider. Wording is for the owner to write; add a settings/consent screen only, no built-in phrases elsewhere.
   Make "send to AI" a visible per-request fact in the UI (which provider, which mode).
3. **Logs.** Search every `logger.*` call for filenames, user text, passages, tokens and keys. Log ids, sizes, timings
   and error classes only. Add a test that logs from an upload + study run contain no document text or filename.
4. **Retention and deletion.** Confirm the sweep also deletes stored results, exports, cached page images, summary
   cache and agent conversation state, not just the uploaded file. Add "delete everything for this device" endpoint
   and a settings button. Test that a second device token cannot read or delete the first one's data.
5. **BYOK (Task 7) safety.** User keys: never logged, never persisted server-side unless the owner decides to,
   sent only per request or stored in device SecureStore, redacted from error messages and the agent trace.
6. **Prompt injection and exfiltration.** Document text is untrusted. Tools must only act on the caller's own
   documents; no tool may send data to a URL or another user. Add agent tests with hostile document text.
7. **Render free tier.** Storage is ephemeral; note in the data-flow doc what survives a restart. Before accepting
   other users, decide on persistent storage, backups and region.
8. **Secrets.** The old Groq key is still in git history (see `security-review.md`). Confirm it was rotated.
9. **Legal basics for the owner (not code):** privacy notice, data-processing terms of each AI provider, and the
   Nigeria Data Protection Act 2023 obligations if the app is offered to other people. Ask a qualified person.

Report with commit hashes, CI results and anything unverified.
