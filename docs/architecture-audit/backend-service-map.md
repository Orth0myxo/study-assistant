# Backend service map

## Application startup and common request path

`backend/app/main.py` constructs the FastAPI app and includes routers for health, users, documents, study jobs and topic sources. `lifespan()` configures the SQLite connection, document store, topic source store, OCR concurrency and job retention; hydrates document/job state; reconciles orphan/missing files; performs startup sweeps; and starts `_cleanup_loop()` for periodic sweeps. The container runs a single Uvicorn worker.

Request path: request-ID middleware accepts only safe IDs or makes a UUID-based one; CORS is configured from `CORS_ORIGINS`; `require_access_token` applies optional shared gate and mandatory per-install user identity on `/api/v1/*` (except POST registration); routes then validate and call stores/services. Error handlers in `errors.py` format `ApiError`, FastAPI validation errors and unexpected exceptions into a stable JSON envelope. Middleware ordering is controlled by Starlette/FastAPI's middleware stack construction; `add_request_id()` is the last declared app middleware and wraps the downstream call in the configured stack.

## API route handlers

### Health — `api/health.py`

`GET /health` reports app version, upload/retention limits and whether Gemini settings are configured. Configuration status is not a live provider reachability check; health schema allows `reachable` to be null. This route is exempt from bearer identity and app gate.

### Anonymous users — `api/users.py`

`POST /api/v1/users` calls `services/users.py:UserStore.create()`, which generates a random user ID and URL-safe token, stores the SHA-256 token hash, and returns the raw token once in `UserRegistrationResponse`. It is intentionally exempt from the data-route identity requirement and optional app gate.

### Document upload/delete — `api/documents.py`

`upload_document()` reads at most `MAX_UPLOAD_MB + 1` bytes; rejects overflow; calls `parse_document()` in a thread pool; serializes each parsed section's raw/normalized text and anchor; and saves document metadata/bytes with request owner. It returns extraction status, counts, warnings and timing headers. It logs timing and parser diagnostics, not source text. `delete_document()` deletes only when store ownership matches, returning the same 404 for absent and non-owned IDs.

### Legacy study jobs — `api/study.py`

`create_study_job()` checks that the document exists for the caller, is available, has usable extracted text, and that AI is configured for AI modes. At/below `SYNC_PROCESSING_MAX_SECTIONS`, it runs `process_document()` in a thread pool and persists the result. Above threshold it persists a `queued` job, starts an in-process `asyncio` task, runs the processor via `asyncio.to_thread`, then persists its final status. This task is not a durable external queue and would be lost on process restart.

`list_study_jobs()` returns owner-scoped recent summaries and current source-file availability. `get_study_job()` returns a full owner-scoped payload. `export_study_job()` allows `completed` or `partial`, renders the stored payload in a thread pool, and returns PDF/DOCX bytes. Other job states return 409; unknown/non-owned/expired jobs are 404.

### Topic sources — `api/topic_sources.py`

- `POST /topic-sources`: accepts multipart `document_id`, `jottings_text`, and zero or more image files. Checks total request payload constraints, per-image count and limits, validates image bytes/dimensions, runs OCR using `run_in_threadpool`, validates linked document ownership, then saves images, transcripts, warnings and metadata via `TopicSourceStore`. Returns metadata and transcripts (not photo bytes).
- `GET /topic-sources/{id}`: owner-scoped lookup and `_response()`; joins linked document availability from `storage` and serializes source image metadata/transcripts.
- `PATCH /topic-sources/{id}`: validates and updates topics, typed jottings and/or transcript edits; store recalculates source status and invalidates saved topics when text changes.
- `DELETE /topic-sources/{id}`: owner-scoped delete of topic source row, image rows and source image directory. It does not delete the linked document.
- `POST /topic-sources/{id}/topic-review`: loads source, chooses provided/saved/segmented topics, identifies input labels, obtains usable sections from the linked owner document, runs deterministic matching, builds response with source refs and warnings.
- `POST /topic-sources/{id}/study-output`: resolves current topics, rebuilds review/matches, calls `build_study_output()` and maps result status/material/AI accounting/warnings into response. It is synchronous and persists the mapped payload in `topic_study_results` with a `result_id`.
- `GET /topic-sources` and `GET /topic-sources/{id}/images/{index}`: owner-scoped recent-source listing and inline note-image streaming from the managed source directory.
- `GET/DELETE /topic-results` and `/topic-results/{id}`: owner-scoped saved-output history, reopen, and delete. `GET /topic-results/{id}/export?format=pdf|docx` renders the stored payload through `services/exporter.py`; no matching or AI generation is repeated.

## Processing services

### Document extraction — `services/document_parser.py`

`parse_document(filename, data, settings, ocr_runner)` validates extension, nonempty input and format signature, then selects PDF or DOCX parser. PDF handling uses PyMuPDF, rejects encrypted documents, walks pages, extracts native text, and uses page text density to select OCR candidates. Each usable page becomes a section with `pdf_page` anchor containing one-based `page` and zero-based `page_index`. It reports repaired, partial, no-text and OCR warnings/diagnostics.

DOCX handling first inspects ZIP uncompressed size to guard against archive expansion. `python-docx` block traversal preserves paragraph/table order, tracks heading labels, extracts paragraph and table text, and OCRs embedded image parts with duplicate-image suppression. DOCX anchors use section label and paragraph index; it does not claim exact printed page numbers. `parse_document()` records parse timing/size in diagnostics.

### Tesseract — `services/ocr.py`

`run_tesseract()` is the common subprocess boundary: configured CLI path/language, timeout and typed exceptions (`OcrUnavailable`, `OcrTimeout`, `OcrFailed`). The parser passes rendered PDF page PNG or extracted DOCX image data. Topic photos go through a separate validation/concurrency wrapper (`topic_images.py`) and then this runner. OCR can be disabled with `OCR_PAGE_LIMIT=0`.

### Study processing — `services/study_processor.py`

`process_document()` returns raw extracted sections directly for `extracted_text_only` and never constructs an AI client in that mode. AI modes build bounded units via `_build_units()` and `_split_text()`, preserve source anchors, and call `AiClient.summarize()` once per unit. `text_and_summaries` returns both original source unit text and summaries; `summaries_only` omits source text. Successful unit summaries feed an overall document summary: one unit reuses its summary; multiple units use `_summarize_overview()`, which batches summary text within the character limit and reports partial reduce failures. Unit failures retain successful results and mark the outcome partial; if all fail, status is failed.

### Gemini — `services/ai_client.py`

`GeminiClient.summarize()` selects section, overview, topic-grounded, or beyond-source prompt and an appropriate system prompt. `_generate()` sends `generateContent` HTTP requests using `httpx`, configured timeout, output-token limit, retry/backoff and API key on server side. Provider HTTP/network/timeouts and malformed/empty response paths map to typed `AiError` subclasses/status codes. `normalize_summary()` strips unsupported formatting/empty values before results are accepted. Topic grounded generation asks the model to use only passages; optional beyond-source uses a separate system prompt and output object so it is not represented as source-derived.

### Topic image validation — `services/topic_images.py`

`validate_image()` checks PNG/JPEG magic bytes, byte size, header dimensions and decoded PyMuPDF pixel dimensions before accepting. Filenames are normalized to a basename. `ocr_image()` acquires a process-local bounded semaphore configured on startup, calls the shared Tesseract runner, and returns `ok`, `empty`, `timeout`, `unavailable`, or `failed` status with user-safe warnings. Good OCR output is truncated to configured transcript size and warned as fallible. This is not dedicated handwriting recognition.

### Topic segmentation and matching

- `topic_segmentation.py:segment_transcript()` normalizes newlines/hyphen line wraps, strips list labels/noise/page labels, splits list delimiters/numbered patterns, de-duplicates normalized identical topics and enforces configured count/character limits with warnings.
- `prepare_provided_topics()` validates manually reviewed topic names and de-duplicates normalized identical strings.
- `topic_matching.py:normalize_for_match()` normalizes Unicode/case/punctuation. `_matches_for_topic()` compares topic content terms against each whole extracted section, calculates term coverage and exact-phrase score, rejects scores under threshold, classifies near-tied top results as ambiguous, and returns up to three original whole-section texts and anchors. It is deterministic lexical matching, not semantic search. Passage offsets are section-level (`0..len(raw_text)`), not fine-grained quote offsets.
- API helper `_labels_for_topic()` identifies typed jottings/photo transcript labels using normalized phrase containment. This is distinct from document source matching.

### Topic study output — `services/topic_study.py`

`build_study_output()` assembles topic output items. Passage-only mode returns match passages with no AI call. AI modes process only topics whose match status is `matched` and that have matches; ambiguous/unmatched items get skipped statuses. For each eligible topic, `AiClient` receives matched passage text and source references for grounded material; optional beyond output is a separate follow-up request and block. Per-topic AI failures generate warnings while other topics continue; aggregate result is completed, partial, or failed. Calls are sequential in the request path. The output dataclasses are mapped by `api/topic_sources.py` and are not written to `study_jobs`.

### Persistence — `services/db.py`, `storage.py`, `job_store.py`, `topic_sources.py`

`Database` owns one thread-safe SQLite connection, enables WAL, normal synchronous mode and foreign keys, serializes calls with `RLock`, and migrates via `PRAGMA user_version` through schema v5. `DocumentStore` persists parsed data and files, hydrates records, reconciles orphan/missing files, enforces owner-scoped lookups/deletion, and sweeps TTL. `JobStore` persists JSON result data and owner, hydrates and sweeps TTL. `TopicSourceStore` persists image transcript/topic metadata and image path, atomically enforces per-user active-source quota, uses optimistic version checks on updates, and sweeps TTL/file folders.

The stores retain in-memory fallback behavior if `db` is not configured, primarily useful for tests. Production startup configures SQLite.

### Export — `services/exporter.py`

`build_blocks()` maps persisted legacy job payloads to mode-aware blocks: source-only, source plus summary, or summary-only. `render_pdf()` uses PyMuPDF and vendored DejaVu fonts for Unicode; `render_docx()` uses python-docx. `render()` selects format; `export_filename()` sanitizes names. There is no equivalent topic-result export path.

## Configuration and limits

`config.py:Settings` reads environment variables with `pydantic-settings`, defaulting to `.env`; it includes provider/model/timeouts/retries, input limits, TTL/database/temp paths, OCR parameters, topic limits, CORS and optional app gate. See `.env.example` for documented variables. `get_settings()` is cached. `render.yaml` overrides database/temp roots to `/tmp` and leaves app gate optional. The Dockerfile installs Tesseract and runs one worker.

## Dependency direction

```text
api handlers -> schemas + config + services
services -> config, schemas/domain types, db wrapper, external CLI/HTTP libraries
stores -> db wrapper + filesystem
main -> routers + stores + lifecycle/configuration
```

Services are plain modules/classes rather than a DI container. Tests replace settings, clients, OCR runners or stores through fixtures/monkeypatching. A route can use FastAPI threadpool helpers to avoid blocking the event loop for CPU-bound parsing/rendering, but not every path is asynchronous or backgrounded.
