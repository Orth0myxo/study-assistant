# Task 15: saved chats on the device, tables in replies and exports, small chat fixes

Same rules: one piece at a time (test, fix, retest), no built-in wording or phrase lists, report what was not verified.
Do not push docs-only commits during the owner's test sessions (Render restarts on every push).

## A. Saved chats on the device (mobile, needs a new APK)

Today the phone keeps one current chat in `agent-chat-history.json` (`services/agentChatHistory.ts`). The "Your studies" menu
reads server jobs and results (`listStudyJobs`, `listTopicResults`), so it is empty after Render sleeps or redeploys and the
agent chats never appear in it.

- Store several chats on the device: an index file (id, title, created/updated time) plus one file per chat. Title from the
  first user message (shortened), editable later.
- The menu lists these chats (newest first). "New Chat" starts a fresh one and keeps the old one. Tapping a chat reopens its text,
  results and export controls. Delete one chat and delete all, with a confirmation.
- Render sleeping must not remove chat text. Only server-side things (uploaded files, images, stored results) can be gone. When
  an item has expired on the server, show it as expired and let the user attach the file again to continue the same chat; the agent
  must handle "not found" calmly (Task 12 C already requires this).
- A redeploy also loses server files, so the same expired-item handling applies.
- Keep the existing limits (messages per chat, text length) and the safe parsing. Migrate the existing single snapshot into
  the first saved chat.
- Tests: save, list, reopen, delete, migrate the old snapshot, expired-item state.

## B. Tables in replies and in exported files

The owner asked the agent for a summary in a tabular format. The chat showed it, but as raw `| # | Topic | Summary |` lines, and the exported
file had no table.

- Render markdown tables in chat bubbles (`services/chatMarkdown.ts` and the bubble renderer): header row, aligned cells, horizontal scroll if wide.
- Exports: when a summary (or any AI text) contains a markdown table, build a real table in DOCX (python-docx table) and PDF
  (drawn grid with wrapping cells, rows not split across pages), and show it in the preview. Reuse the table drawing from Task 8 / Task 13
  (`table_exporter.py`, exporter table blocks). Plain text paragraphs around the table stay as paragraphs.
- Do not guess tables from ordinary text; only convert text that is a valid markdown table.
- Tests: a summary with a table produces a DOCX table with the right rows/columns and a PDF whose text keeps the cell order; text without a table is unchanged.

## C. Small chat fixes

- "Review these detected outline topics, then confirm or edit them." appears twice in one message (message text and the card). Show it once.
- When a user says the PDF and not the image (or the reverse), the agent confused the two sources. Make the active source explicit to the
  agent (document vs photo source, by id and kind, from conversation state) and let the app's source chips set the active source.
  No phrase matching.
- Check that error text from tools never leaks into chat as raw field names; show the app's own wording.

## D. Converter entry (already changed by the maintainer, verify on a device)

The strip "File Converter, Coming soon" was disabled in the agent screen. It now opens `/table-converter` (labelled "Photo to table" with
"Excel, Word, CSV"). The converter screen reads locally by default and asks before using AI. Verify on a device that it opens, that a photo
reads, and that CSV, XLSX and DOCX export.

## E. Deferred by the owner (do later, evidence kept here)

Render free tier (512 MB) restarted the service at 22:58:30 UTC on 2026-11-18 (owner got the memory-limit email), at the same minute
a preview request failed with 502. The next preview request took 70.7 s (`POST /api/v1/agent/results/{id}/preview ms=70738`), and page
image requests take 2.3-2.6 s each. Suspects: page images rendered for preview/export, 200 dpi OCR renders (1700x2200), and several heavy
requests at once. Later: limit heavy work to one at a time, lower render sizes, free images promptly, cache small preview images, and
measure memory before and after.

## Before giving the app to testers (owner decision)

Show a short notice before first use that material and photos are sent to AI providers when AI features are used, and that
the local reading path keeps images on the server. See `docs/task-9-privacy.md`.

## APK

Parts A, B (chat rendering) and C change the phone app. Build one APK after they are done and the converter strip is verified.
