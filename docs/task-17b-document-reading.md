# Task 17B: reading scanned pages, headings and approval (after 17A is reviewed)

Owner decisions (2026-11-20): AI never reads a page image without the user's approval; approval can be remembered and turned off;
Gemini stays in the provider chain for now (the owner is testing personally), so everything below is provider-agnostic and applies to
whichever provider is configured. The owner is testing Qwen vision in the Groq playground separately; do not switch providers here.

Same rules as 17A (AGENTS.md; one piece at a time; no built-in wording or phrase lists; verbatim only from stored text and AI output
labelled; report real test results; do not push; update SESSION.md).

## B1. Headings tool (no AI)

- New agent tool that lists a document's headings with page numbers. Order of sources: PDF bookmarks/outline, then lines that are
  bold or larger than body text (font flags and sizes from PyMuPDF spans), then DOCX heading styles for Word files.
- Stored with the document at upload or computed on demand; say which. Never invent headings; if none are found, say so and offer
  page titles or the page images.
- "Topics in this PDF" uses this tool. `read_outline_image` stays for photos of topic lists only.
- Test: 16-page text PDF with bold headings; a PDF with bookmarks; a DOCX with heading styles; a PDF with none.

## B2. Consent-based AI reader for scanned pages and slides

Why: a 76-page lecture deck had 44 image pages with no text read; a scanned table PDF read as scattered lines; poor handwriting
defeats Tesseract. A vision model reads these far better but the page image leaves the server, so it needs the user's approval.

- Prompt text names the provider and says the page image is sent. It must not promise the provider keeps nothing. Choices:
  Allow this time, Allow and remember my choice, Don't allow.
- One setting, "AI reading of scanned pages": Ask every time (default), Always allow, Never. "Allow and remember" sets Always allow.
  The File Converter's "Read with AI" uses the same setting.
- The server enforces it. The model can never trigger an image read by itself; without consent the tool returns `needs_approval`.
  "Never" blocks every provider, including fallbacks.
- AI-read text is labelled as AI-read, kept next to the page image so the user can check and edit it, and stored once per page so
  later search, quotes and exports use the stored text.
- Local reading stays the default: try Tesseract first; offer the AI reader when local reading times out, returns little, or the user asks.
- Free-tier limits: small page batches per request, respect OTPM/TPM, report progress per batch.

## B3. Pause and resume for approval

- When a tool needs approval, the agent turn pauses: the backend stores the pending turn (conversation state and the pending tool
  call) with an expiry, returns a `needs_approval` state, and the app shows the prompt.
- Allow: the app calls a resume endpoint with the pending id; the backend runs the read and continues the same turn, giving one
  final reply. Don't allow: it resumes with "the user declined" and the agent answers once without images.
- No model call runs while waiting. If the pending turn is lost (restart or expiry), the app says so and the user resends.
- Test: allow, decline, expiry, server restart, remembered choice (no prompt), Never.

## B4. Table-aware reading in the study chat

- Reuse the File Converter's geometry-based table reader (`table_reader.py`) for scanned table pages in the study path so rows and
  columns stay together, instead of flat OCR lines. Render with the Task 15 B table support in chat and exports.
- Test with the owner's all-image table PDF: rows and columns in order, numbers intact.

## B5. Extraction by heading (no AI)

Why: the owner's school materials are often informal notes (topic, subtopic, subtopic) with no page structure, so page ranges are a
poor way to ask for text. Headings are the unit he thinks in.

- A section runs from a heading to the next heading of the same or higher level; its text is the stored text, verbatim.
- When the user asks for a word or topic, group the hits by the heading each one falls under (nearest preceding heading, shown
  with its parent headings as a path, plus page numbers). The user picks one or more headings and the app extracts and exports
  those sections' stored text. No AI is involved.
- Materials with no detectable headings: list the hits with page and surrounding lines and let the user choose; never invent headings.
- Pages whose headings cannot be detected because they are scans or slides as pictures go through B2 first.
- Extract the whole section, never just the matched line. The matched word is only used to find the section. The extracted text is
  everything from the heading down to the line before the next heading of the same or higher level, including sub-headings and
  their text. The previous build exported a single line; this must not happen again.
- Test: notes with bold subtopics, a DOCX with heading styles, a slide deck (slide titles as headings), a PDF with no headings.
  Also check that the exported PDF/DOCX contains the full section text, not only the line with the word.

## B6. One confirmation per request

- A request is confirmed at most once. The list grouped by heading is the confirmation: choosing from it runs the extraction and
  the export directly, with no second "are you sure".
- If image pages need the AI reader, say so in that same step (one prompt). With "remember my choice" there is no prompt.
- Test: request, pick headings, receive the file, with exactly one decision point.

## Logging (applies to every item above)

Every new behaviour writes a Render log line so a problem can be traced to the implementation. Same style as the existing
`app.api.documents`, `app.services.page_ocr` and `app.services.agent_loop` lines: one INFO line per event with ids, counts, timings
and an `outcome=` field (WARNING for failures). Examples: headings found (`source=bookmarks|font|styles count=… pages=…`),
section extraction (`headings_selected=… sections=… chars=…`), consent decision (`ai_read consent=ask|always|never decision=allow|deny
remembered=true|false pages=… provider=…`), pause and resume (`pending_id` short hash, `state=paused|resumed|expired|declined`,
`wait_ms`), AI page read (`pages=… ms=… outcome=…`), table-aware read (`tables=… rows=… ms=…`).
Never log document text, filenames, snippets, image contents or keys; counts and ids only.

## Not in this task

Tuning local OCR for handwriting (the owner meets handwritten notes about twice a semester; the consent-based AI reader covers it).
Question generation (planned as a later task), switching the default provider to Qwen vision (owner is still testing), self-hosting a model, privacy notice, hosting.
