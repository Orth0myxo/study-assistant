# Task 17A: agent fixes before the friends test

Owner decision (2026-11-20): fix these first, review them, then do Task 17B. One APK is built after both.
Source: the owner's phone tests and Render logs of 2026-11-20 (real PDFs: a 3-page exported PDF, a 5-page and a 76-page image-heavy
PDF, a 16-page text PDF, the attendance-photo converter test).

Rules (same as always): follow `AGENTS.md`; one piece at a time (test, fix, retest); no built-in wording, phrase lists, keyword or
regex intent detection in the app; verbatim text comes only from stored text, AI output is labelled; report the commands you ran
and their real results; list anything you did not verify; update `SESSION.md`. Do not push. Check every pointer below against
the current code first; line numbers are from commit 461ad3b and may have moved.

Suggested order: A1, A2, A3, A5, A4, A7, A6, A8, A9.

## A1. Converter images must not become study materials

Evidence: the leaked tool text in the chat used `source_id=59c14ae0…`. Render logged `topic source created … source_id=59c14ae0…
images=1 bytes=1796196 status=no_text` at 13:43:49 UTC, 30 s before the `/api/v1/tables` call of the converter test. So the converter's
photo was stored as an "Outline images" topic source, and the agent kept reaching for it ("I found an Outline images source").
`read_outline_image` then read the attendance sheet and said "No readable outline topics were found".

- Pointers: `mobile/app/table-converter.tsx` (~line 200, `createTopicSource(null, assets…, '', true)`), `backend/app/services/agent_service.py`
  `_list_materials` (~lines 766-775 list every `topic_sources.list_for_owner` row as kind `outline`), `backend/app/services/topic_sources.py`.
- Fix: a converter upload is marked as such (a flag or purpose stored with the source) and never appears in `list_materials`, in the
  active-source logic, or in the chat history list of study sources. Use the existing fourth argument if it already means this; check.
- Old rows already stored (no flag): do not delete user data. Exclude outline sources that have no topics and no study use from
  `list_materials`, or another safe rule you can justify. Say what you chose.
- The owner could not see or remove this source in the app. Make sure nothing the agent can see is hidden from the user without a way to remove it.
- Test: upload a photo through the converter, then ask the agent for the topic outline. `list_materials` must not contain it.

## A2. No material selected: no guessing

Evidence: with nothing uploaded, "give me the Gram stain procedure" produced "I'll check a microbiology textbook" and an OCR warning
for something the user never attached.

- The agent may only read materials that belong to the current chat or that the user selected. Check how conversations attach
  sources (`conversation.last_source_id`) and whether `list_materials` returns older documents from other chats; if so, restrict it.
- With nothing attached, the agent says plainly that a file is needed and how to attach it. It never claims to look in a textbook,
  a library or the user's phone, and never reads another stored document silently.
- Add this to the instructions in plain rules, not as a list of example phrases.
- Test: new chat, no file, ask for a topic; then attach a file, ask again.

## A3. Leaked tool-call text

Evidence: twice the reply was `<tool_call><function=get_readable_text>…` and `<function=read_outline_image><parameter=source_id>…`.
`get_readable_text` is not a tool. The final model call is smaller (about 730-930 request tokens vs 1,400-1,580) which suggests the
tool list is not sent on that call, so the model writes the call as text. The server answered in under a second, yet the owner
waited about 30 s because the reply looked like a tool in progress.

- Never show tool-call markup to the user. Detect it structurally (the markers the model emits: `<tool_call>`, `<function=`,
  `<parameter=`), in `agent_loop.py` / `agent_models.py`, before the reply is returned. This is protocol cleanup, not intent detection.
- When detected: log `agent_tool_markup_stripped` with the tool name only; retry once (with tools offered if the turn allows them);
  if it repeats, return one plain sentence that the request could not be completed and can be retried.
- Test with the exact leaked strings from the screenshots as fixtures, including an unknown tool name.

## A5. One name per ID

Evidence: the model mixed `source_id`, `document_id` and `result_id` (`document_not_found`, `source_not_found`, `source_required`).

- `list_materials` returns a plain `id`. Return explicit fields by kind (`document_id` for documents, `source_id` for image sources)
  and say in each tool description which id it takes.
- Tool errors say which kind of id was given and which tool takes it. Example: `read_outline_image` called with a document id
  returns "that is a document; use get_pages or find_passages", not `source_not_found`.
- Test: call each tool with the other kind of id; the error must name the right tool.

## A4. Outline tool only for topic-list images

Evidence: "give me the topic outline in this PDF" made the agent call `read_outline_image` several times (wrong ids), then answer
"Upload a clearer image or type the topics" on a clean text PDF. Upload logged `sections=16` (one per page), so there are no
headings to list yet; the headings tool comes in 17B.

- Tool description and instructions: `read_outline_image` is only for an image the user attached as a topic list. For "topics in
  this PDF" use the document's pages.
- Interim until 17B: for a text PDF, read the first pages (bounded) and list the headings the text really shows, labelled as the
  agent's reading and not as a stored outline; do not claim a complete outline.
- Never end with "upload a clearer image" for a PDF. Say what was found and offer the next step.
- Test: 16-page text PDF with bold headings, ask for its topics.

## A7. One plain OCR warning

Evidence: "OCR could not read page(s) 3." and "OCR failed on page(s) 3; a retry will run on the next request. Native text was
returned where available." appeared together. Log: `page_ocr … page=3 status=failed ms=19895.7 exception_type=OcrTimeout`.

- Show one sentence, plain words, no jargon ("OCR"), naming the pages, and only for pages the user asked about.
- Keep the real classification in the logs (timeout vs failed).
- Test: force an `OcrTimeout` on one requested page and one unrequested page.

## A6. Summary length (the 80-word cut)

Evidence: a "concise breakdown" of a 76-page lecture came back in about 120 words. Cause is not the 500-token cap.
`agent_service.py` `_SummaryArgs.length` defaults to `short`; `limit_words(summary, 80 if short else 150)` (~lines 1309-1337);
the instructions say "Keep answers short and self-contained" (~line 1626); a whole-document summary uses a bounded, evenly
spaced sample. 44 of 76 pages were image pages with no text read (`ocr_candidates=44`, `chars=10084`).

- Add `length: detailed` (keep short and medium). Detailed has no 80/150-word cut; it is limited only by the provider's output
  limit and the model-call budget (`agent_max_model_calls`).
- Whole-document summaries run in chunks (page ranges or sections), each grounded in its own text, and the result states coverage
  ("6 of 8 parts summarized; pages 41-52 not covered"). Never present a sample as the whole document.
- The agent's final message must not re-shorten a summary the tool already returned. Check what the final turn does with it.
- If many pages are images with no text, say so in the summary note (the reader that handles them is 17B).
- Respect Groq limits (OTPM 1000, TPM 8000 on the free tier) and Gemini retries (one 503 then OK took 19.9 s); cap chunks per request.
- Test: the 76-page deck and the 16-page text PDF with "concise" and "detailed".

## A8. Log characters per page at upload

Evidence: a PDF the owner says is all images logged `native_text_pages=5 ocr_candidates=0 chars=4416`. Nothing was OCR-checked.

- In `document_parser.py` (~lines 494-533, `page_metrics`) log a bounded list per page: native character count, image coverage,
  `needs_ocr`; plus the thresholds used (`min_pdf_chars_per_page`, `_PDF_IMAGE_OCR_MAX_NATIVE_CHARS`, `_PDF_IMAGE_OCR_MIN_COVERAGE`).
  Counts and ratios only, never text. Cap the list (for example the first 100 pages).
- Do not change the thresholds in this task. Report what the numbers show for the owner's PDFs when he re-uploads them.

## A9. A request made before a file is attached

Evidence: the 14 Nov build said "I'll look for that topic as soon as your file is ready", then after the upload only asked "Could you
name a topic". That screenshot is an older build.

- Test the current build: ask for a topic with no file, attach a file, see whether the request runs.
- If it is dropped: either run the remembered request once after the file is ready, or remove the promise from the reply.
  Pick the smaller change and say which.

## Logging (applies to every item)

Every fix writes a Render log line so a problem can be traced to the change. Same style as the existing `app.api.documents`,
`app.services.page_ocr` and `app.services.agent_loop` lines: one INFO line per event with ids, counts, timings and an `outcome=`
field (WARNING for failures). Examples: A1 `converter_source excluded_from_materials count=…`; A2 `agent no_material_selected`;
A3 `agent_tool_markup_stripped tool=… retried=true|false`; A4/A5 the tool error codes already logged, now with the id kind
(`id_kind=document|source`); A6 `summary length=… chunks=… covered=… of …`; A7 `ocr_warning pages=… kinds=timeout|failed`;
A8 the per-page counts; A9 `pending_request outcome=ran|dropped|promise_removed`. Never log document text, filenames, snippets,
image contents or keys; counts and ids only.

## Not in this task

Headings tool, the consent-based AI reader, pause and resume, table-aware reading (17B). File Converter layout and incomplete
tables, privacy notice, app icon, hosting, the 2.4 MB upload at 14:35:15 UTC that logged "received" but never "completed".

## Done when

Each item has a test, the full backend and mobile suites are run with the results reported, and `SESSION.md` lists what was verified
and what was not. The owner then retests on a phone, one piece at a time, before 17B starts.
