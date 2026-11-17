# Task 4c — export layout, wording-free app, build number, command parser

Source: owner's test of 2026-11-17 on preview build 10 (`HERPES VIRIDAE FAMILY.docx`, summary-only export to PDF and DOCX, Render logs). Read `SESSION.md` first. One piece at a time: implement, test, push, then the next. One commit per piece, pushed without force; do not re-author published commits. CI (backend-tests, mobile-tests) is the check; say what could not be verified.

Already fixed by the reviewer (do not redo): the AI summary printing twice in summary-only exports (`9f12d5f`, tests in `tests/test_export.py`).

## Owner's rule for the whole app
The app must not have its own choice of words. Phrases seen in the owner's screenshots, notes or tests are test data only. They must never appear in the app as suggestions, hints, sample topics or matching rules. This applies to UI text, placeholders, empty states and any phrase list.

## 4c-1 Export: compact references and wrapped text (backend, no APK)
Evidence: the exported heading read "paragraph 0 · paragraph 1 · … · paragraph 70", the "Topics requested:" and "Source:" lines ran off the page edge, and the numbering starts at 0 in some labels and 1 in others.
- `_ref_label` / `_refs_label` in `backend/app/services/exporter.py` print every reference. Collapse runs: pages "Pages 2-4", paragraphs "Paragraphs 1-71", a selection covering the whole document "Whole document". Use one numbering base everywhere (1-based, as the range selector does; check `range_extraction.py`, `study_processor.py` and `topic_study.py` `_ref_label` too, which also print `paragraph {n}`).
- The PDF writer's `meta()` and `source()` call `_line()`, which draws one unwrapped line with `insert_text`. Wrap them with the same wrapping the body uses (`_wrap_lines`), keeping their colour and size.
- A topic heading made from the reference list must use the compact label.
- Tests: a 71-paragraph payload exports a heading and meta line under a set length, all text inside the page width (compare the span x1 values with the page width minus margin), consistent numbering.

## 4c-2 Export: readable sizes and no misleading lines (backend, no APK)
- Larger text for older readers. Targets: PDF body 13-14 pt, grey/meta/source lines at least 11 pt, headings scaled up in proportion (title about 24-26, h2 about 17, h3 about 14); DOCX body 13-14 pt, meta/source at least 11 pt. Make the sizes constants at the top of `exporter.py`. Check the page-image layout from Task 2b still fits.
- "No matching passage found" must not appear in `summaries_only` exports, where there is no source text by design. Keep it for source-passage modes.
- Tests: span sizes in a PDF are at or above the targets; the empty-passage text is absent in summary-only and present in source modes.

## 4c-3 Export: default alignment chosen by the app (backend plus small mobile change)
The owner does not want to pick alignment. The app decides by element:
- Document title and section headings: centered.
- Body paragraphs and bullet lists: left (justify only for long continuous paragraphs, if it reads well in tests).
- Meta lines (date, topics, coverage, source): left, small.
- Page images: centered.
- Numbers/figures or table-like content: left.
`text_align` and `heading_align` stay as optional overrides on the export endpoints, but the mobile export sheet no longer shows the Body/Headings alignment buttons by default (an "Alignment options" control may reveal them). Defaults must apply when the parameters are absent.
Tests: default output (no parameters) has centered title/headings and left body in both DOCX XML and PDF span positions; an override still works.

## 4c-4 App: remove built-in wording and suggestions (mobile, APK)
In `mobile/app/workspace.tsx`: the empty-state example chips ("Summarize the whole material", "Show me pages 2-4", near line 2160) and the sample topics in the empty-state copy (near lines 1655 and 1663: "sickle cell anaemia", "cell membrane transport"). Replace them with neutral action buttons that name the action only (for example Original passages, Summary, Both, Page range). A button opens a small form with an empty topic box or pages box; no sample words. Keep the study-mode shortcut idea: it shows what can be requested without typing. Do not add new phrase lists. Search the mobile and backend code for other hard-coded example words and report them.
Tests: render test or unit test that the empty state contains no sample topic text.

## 4c-5 App: build number shows "Unavailable" (mobile, APK)
`mobile/app/settings.tsx` line 48: `Constants.nativeBuildVersion ?? 'Unavailable'` is empty in the installed preview build. Find out why (check the installed value, `expo-application`'s `nativeBuildVersion`, and the EAS `autoIncrement` setting) and show the real build number. Verify in the next build and say how you verified.

## 4c-6 Chat command step always falls back (backend)
Evidence: every Render log shows `app.services.chat_commands: chat command fallback code=ai_invalid_response` right after `ai call provider=groq … purpose=command … outcome=http_200`. The call succeeds but the answer is rejected, so chat always uses the fixed-rule fallback. Find why the answer is invalid for `groq_command_model` (qwen/qwen3.8-27b): thinking/reasoning text before the JSON, markdown fences, extra keys, or a schema mismatch. Log (without message text) which validation failed. Fix by parsing/prompting for the model's real output, not by loosening validation silently. Tests with captured-shape fake responses for each failure mode.
Note: this is the step the agent route (Task 5) is meant to replace, so keep the fix small.

## 4c-7 Defect log items (read-only for now)
- Page OCR on dense full-page scans times out at about 19 s per page on the free Render CPU. Parked until the owner has a paid instance. Do not change OCR settings in this task.
- Three separate Gemini summary calls appeared within 40 seconds because two documents were tested. No defect.
