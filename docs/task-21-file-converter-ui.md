# Task 21 — File Converter screen cleanup and a clearer entry button

Branch: create `task-21-wip` from the latest `task-20-wip`, only after Task 20 is green on its final commit. Do not push `feature/verbatim-groq-pipeline`. Follow `AGENTS.md`. Owner restriction (from `SESSION.md`): do not install dependencies or run test suites locally; verification is GitHub Actions. One item at a time, commit per item, update `SESSION.md` at the end.

Scope: the File Converter screen (`mobile/app/file-converter.tsx`), the conversion list labels (`backend/app/services/converter.py`), and the converter entry button in the chat (`mobile/components/AgentWorkspaceScreen.tsx`, and the same button in `mobile/app/workspace.tsx`). Converter behaviour is out of scope: do not change conversion handlers, job endpoints, limits, the AI notice flow, the table editor, or exports.

## Findings (from the owner's screenshots and a code review)

1. **Duplicate "Summary table" cards.** `_CONVERSIONS` builds summary conversions for each of `text`, `pdf` and `docx` to each of `xlsx` and `docx`, and gives all of them the same label ("Summary table") and the same description. So a PDF, Word or pasted-text input shows two identical cards. Nothing tells the user which one gives Excel and which gives Word. The other labels have the same weakness: "Word document", "Word table", "Excel workbook" and "PDF" describe the output, but the screen does not say so.
2. **Input chips are cut off.** Step 1 is a horizontal row that runs off the right edge ("Pasted text, CSV, Excel workbook, W…"). Users cannot see all the inputs without knowing to scroll sideways.
3. **The first screen is mostly header.** A large two-line headline and a long sentence sit under the app bar, which already says "File Converter". Steps 1 and 2 start below the fold.
4. **The Convert button is far down and disabled with no reason.** It is grey with no explanation of what is missing. The limits ("Up to 25 MB, 5,000 rows, 200 columns, and 100 pages per file") and the AI notice sit under it in small text.
5. **The chat entry button is tiny.** `converterButton` is a 28 px tab with a 10 px label and a 9 px hint, sitting above the composer. It is easy to miss.

## C1 — Make every option unique and say what it produces

Backend (`converter.py`):

- Give the summary conversions distinct labels by output: `Summary table (Excel)` and `Summary table (Word)`. Keep `conversion_id`s unchanged so existing clients and tests keep working.
- Make labels unique within each input kind in general (no two options for one input may share a label). Use the output name in the label wherever two options of one input could be confused.
- Add `output_label` to each option in `conversion_formats()` (for example `Excel`, `Word`, `PDF`, `CSV`) so the app can show a badge without parsing the label. Add the field in the response schema with a default so older app builds still work.

Tests (`backend/tests/`): labels are unique per input kind for every input; `output_label` is present for every option; the existing converter tests pass unchanged except for any that assert the old summary label, which should be updated to the new labels.

## C2 — Rework the converter screen layout

Use existing theme tokens (`palette`, `spacing`, `radii`, the current fonts). No new dependencies. Keep every existing behaviour and state. Target widths 360 px and up, and test with large system font scale.

Layout, top to bottom:

1. **Compact header.** Remove the large headline and the long sentence. Keep one short line under the app bar, for example "Convert a file or pasted text. Files are temporary." Keep the "Photo or pasted text to table" entry as a compact full-width card (title, one line, chevron), now directly under that line.
2. **Step 1: From.** Show all input kinds at once as wrapped chips, no horizontal scroll (two or three per row, wrapping as needed). The selected chip is clearly highlighted.
3. **File or text area.** Same picker and text box as now. Put the limits for the chosen input in one small line directly under it (for example "PDF · up to 25 MB, 100 pages"). Move the general limits line here and remove it from the bottom.
4. **Step 2: To.** Show the outputs as compact selectable rows: output badge on the left (Excel, Word, PDF, CSV), label, a one-line description, and the existing "AI" tag where `needs_ai` is true. Rows are about half the height of today's cards. Sort non-AI conversions first, AI conversions after, and put a thin divider or small heading "Uses AI" before the AI group. When only one option exists for an input, preselect it.
5. **Sticky Convert bar.** Pin the Convert button to the bottom of the screen, above the system navigation area, so it is always visible. When it is disabled, show the reason on one line next to or above it: "Choose a file", "Paste some text", or "Choose an output", whichever is missing first. For an AI conversion, show the existing AI notice and confirmation exactly as today, and keep the "Confirm the AI notice" rule.
6. **Result area unchanged** (preview table, warnings, export), except: when a job finishes, scroll the result into view, and keep the sticky bar out of the way of the export button (hide or collapse the Convert bar while a result is shown, with a "New conversion" action to reset).

Accessibility: every control keeps an `accessibilityLabel` and selected state; touch targets at least 44 px high; no text smaller than 12 px; contrast follows the existing palette.

Tests: extract the pure logic into a small helper module with Vitest tests: the ordering and grouping of options, the disabled-reason selection, and the preselection rule. Typecheck and lint must pass. Do not add screenshot tests.

## C3 — A bigger, clearer converter button in the chat

Replace the 28 px tab with a slim action strip above the composer:

- Height about 44 px, full width inside the chat padding, rounded corners, a light accent tint (`palette` accent surface), a thin border, a small icon or glyph on the left, the label "File Converter" at 14 px semibold, a one-line hint at 12 px ("Excel, Word, PDF, CSV"), and a chevron on the right.
- It must not squeeze the chat: the strip replaces the current 30 px row, so the net height change is about 14 px. Hide it while the keyboard is visible (the composer and messages get the space back) and show it again when the keyboard closes.
- Same button on both `AgentWorkspaceScreen.tsx` and `workspace.tsx` through one shared small component so they cannot drift apart.
- Keep `accessibilityLabel="Open file converter"` and the existing route.
- Do not add badges, animation, or any new nagging behaviour.

## Rules

- No built-in wording or phrase lists for intent. No new dependencies. No provider changes. Do not change converter limits or any conversion logic.
- No new log lines are required (no new behaviour on the server); the existing converter log lines must not change.
- Existing tests, including the Task 19 and Task 20 tests, pass unchanged except as C1 says.

## Acceptance

1. GitHub Actions (Backend and Mobile) pass on `task-21-wip`; report run ids.
2. C1 tests and the C2 helper tests exist and pass. Typecheck and lint pass.
3. `SESSION.md` updated: branch, commits, run ids, and the list of files changed.
4. Nothing pushed to `feature/verbatim-groq-pipeline`.

Owner device check after review: open File Converter from the chat on a narrow phone and check (1) all five inputs are visible without scrolling sideways; (2) no duplicate options for PDF, Word and pasted text, and each says whether it gives Excel or Word; (3) Convert is always visible and says what is missing when disabled; (4) the chat strip is easy to notice, does not cover messages, and disappears while typing; (5) a normal conversion and a Summary table conversion still work end to end.
