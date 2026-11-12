# Data Model

Version 1.0 · MVP · Status: **Stage 4 implemented**

Two storage domains:

1. **Server (persistent, SQLite)** — uploaded document metadata + extracted sections,
   and study-job state/results. Retained for `RETENTION_MINUTES` (default 7 days).
2. **Device** — the app reads history from the backend. Device-local (offline) storage
   via Expo SQLite is **deferred**; the server store is authoritative for review/export.

No accounts. `document_id`/`job_id` are unguessable, and there is no multi-user server
state in the MVP.

## 1. Entity overview

```
Document (server, SQLite)          StudyJob (server, SQLite)
   │ 1                                 │ 1
   │ parsed sections                   │ items[] + document_summary
   │ source file on disk               │ error/warnings/progress
   ▼                                   ▼
 study_jobs.document_id  ───────────▶  export (generated on demand, streamed)
```

`Document` and `StudyJob` are independent rows. Deleting a `Document` (source file +
row) does **not** delete its `StudyJob` rows, so completed results stay readable and
exportable; they just report `document_available: false`.

## 2. Server-side persistence (SQLite, stdlib `sqlite3`)

A single SQLite file (`DATABASE_PATH`, default `.tmp/study.db`, git-ignored) with
`PRAGMA user_version` migrations. The file lives beside the upload directory and is
**not** web-served.

### 2.1 `documents`

| Column              | Type     | Notes                                              |
| ------------------- | -------- | -------------------------------------------------- |
| `document_id`       | TEXT PK  | Unguessable id                                     |
| `filename`          | TEXT     | Original filename (sanitized)                      |
| `format`            | TEXT     | `pdf` \| `docx`                                    |
| `size_bytes`        | INTEGER  | Validated size                                     |
| `path`              | TEXT     | Managed source-file path (never exposed to client) |
| `created_at`        | REAL     | Unix seconds                                       |
| `expires_at`        | REAL     | `created_at + RETENTION_MINUTES`                   |
| `page_count`        | INTEGER  | PDF pages; null for DOCX                           |
| `extraction_status` | TEXT     | `ok` \| `no_text` \| `partial`                     |
| `text_extractable`  | INTEGER  | 0/1                                                |
| `section_count`     | INTEGER  | Logical sections                                   |
| `total_chars`       | INTEGER  | Extracted character count                          |
| `sections_json`     | TEXT     | JSON `[{ index, anchor, raw_text, normalized_text }]` |
| `warnings_json`     | TEXT     | JSON `[{ code, message }]`                         |
| `ingest_status`     | TEXT     | `processing` \| `ready` \| `failed` (schema v8)    |
| `ingest_progress_json` | TEXT  | JSON `{ processed_units, total_units, label }`      |
| `ingest_error_json` | TEXT     | JSON `{ code, message }` when failed, else null     |

Source availability (`available`) is derived at read time from the file existing on disk.

### 2.1.1 `document_pages` (schema v8)

Every readable unit is stored even when it contributes no section: one row per PDF
page, including blank/unreadable pages, and one row per non-empty DOCX block. This is
the durable Tier B presentation metadata; rendering may use it but must never change
the stored `text`.

| Column           | Type    | Notes |
| ---------------- | ------- | ----- |
| `document_id`    | TEXT    | `documents.document_id`, cascade delete |
| `page_index`     | INTEGER | Zero-based order within the document |
| `page_number`    | INTEGER | One-based PDF page; null for DOCX blocks |
| `kind`           | TEXT    | `pdf_page` \| `docx_block` |
| `text`           | TEXT    | Exact extracted/OCR text for the unit |
| `source`         | TEXT    | `native` \| `ocr` \| `none` |
| `structure_json` | TEXT    | JSON blocks `{ index, role, text, start, end, bold, alignment, size }` |

`start`/`end` are offsets into that unit's exact text. PDF hints come from PyMuPDF
span data; scanned/OCR units stay plain. DOCX roles are `title`, `heading`, `bullet`,
or `normal`.

### 2.2 `study_jobs`

| Column             | Type     | Notes                                           |
| ------------------ | -------- | ----------------------------------------------- |
| `job_id`           | TEXT PK  | Unguessable id                                  |
| `document_id`      | TEXT     | → `documents.document_id` (not enforced, retained after delete) |
| `filename`         | TEXT     | Denormalized for history after source deletion  |
| `format`           | TEXT     | `pdf` \| `docx`                                 |
| `mode`             | TEXT     | `extracted_text_only` \| `text_and_summaries` \| `summaries_only` |
| `status`           | TEXT     | `queued` \| `running` \| `completed` \| `partial` \| `failed` |
| `created_at`/`updated_at` | TEXT | ISO-8601 UTC                                |
| `created_epoch`    | REAL     | Newest-first ordering key                       |
| `expires_at`       | REAL     | `now + RETENTION_MINUTES` (refreshed on update) |
| `page_count`       | INTEGER  | Nullable                                        |
| `item_count`       | INTEGER  |                                                 |
| `items_json`       | TEXT     | JSON `StudyItem[]` (source refs, text, summary, warnings) |
| `document_summary` | TEXT     | AI modes only                                   |
| `warnings_json`    | TEXT     | Global warnings                                 |
| `error_json`       | TEXT     | `{ code, message }` when failed, else null      |
| `progress_json`    | TEXT     | `{ processed_units, total_units }`              |

Index: `(created_epoch DESC)` backs `GET /api/v1/study-jobs`.

### 2.3 `topic_sources`

| Column              | Type    | Notes |
| ------------------- | ------- | ----- |
| `source_id`         | TEXT PK | Unguessable id |
| `owner_id`          | TEXT    | Exact-match owner scope |
| `document_id`       | TEXT    | Optional linked source document |
| `document_filename` | TEXT    | Denormalized for display after deletion |
| `document_format`   | TEXT    | `pdf` \| `docx` |
| `document_page_count` | INTEGER | Nullable |
| `status`            | TEXT    | `ok` \| `partial` \| `no_text` from typed + photo text |
| `image_count`       | INTEGER | Number of note photos |
| `jottings_text`     | TEXT    | Typed/pasted jottings (schema v5) |
| `total_chars`       | INTEGER | Typed jottings + photo transcript characters |
| `topics_json`       | TEXT    | JSON reviewed topic list; cleared when input text changes |
| `warnings_json`     | TEXT    | JSON `[{ code, message }]` |
| `version`           | INTEGER | Optimistic-concurrency version |
| `created_at`/`updated_at` | REAL | Unix seconds |
| `expires_at`        | REAL | `created_at + RETENTION_MINUTES` |

### 2.4 `topic_source_images`

| Column              | Type    | Notes |
| ------------------- | ------- | ----- |
| `source_id`         | TEXT    | `topic_sources.source_id`, cascade delete |
| `image_index`       | INTEGER | Zero-based order |
| `filename`          | TEXT    | Original filename |
| `content_type`      | TEXT    | `image/png` \| `image/jpeg` |
| `size_bytes`        | INTEGER | Validated size |
| `width`/`height`    | INTEGER | Pixel dimensions |
| `path`              | TEXT    | Managed image path (never exposed) |
| `ocr_status`        | TEXT    | `ok` \| `empty` \| `timeout` \| `unavailable` \| `failed` \| `skipped` \| `manual` |
| `ocr_text`          | TEXT    | OCR or manually corrected transcript |
| `transcript_source` | TEXT    | `ocr` \| `manual` |
| `warnings_json`     | TEXT    | JSON `[{ code, message }]` |
| `created_at`        | REAL    | Unix seconds |

### 2.5 `topic_study_results` (schema v6)

| Column              | Type    | Notes |
| ------------------- | ------- | ----- |
| `result_id`         | TEXT PK | Unguessable id |
| `source_id`         | TEXT    | Originating topic source (not a foreign key; results outlive sources) |
| `owner_id`          | TEXT    | Exact-match owner scope |
| `document_id`       | TEXT    | Optional linked document id, for availability checks |
| `document_filename` | TEXT    | Denormalized display filename |
| `document_format`   | TEXT    | `pdf` \| `docx` |
| `mode`              | TEXT    | `source_passages` \| `ai_study_material` \| `both` |
| `ai_scope`          | TEXT    | `source_only` \| `source_and_beyond` |
| `status`            | TEXT    | `completed` \| `partial` \| `failed` |
| `topic_count`       | INTEGER | Number of topics in the saved output |
| `payload_json`      | TEXT    | Full study-output response plus `kind: "topic_study"` and export filename |
| `created_at`/`updated_at` | REAL | Unix seconds |
| `expires_at`        | REAL    | `created_at + RETENTION_MINUTES` |

Indexes: `(owner_id, created_at DESC)`, `(source_id, created_at DESC)`, and
`(expires_at)`. The payload is rendered directly for export, so reopening or
exporting never re-runs matching or AI generation. Deleting a topic source or
document does not delete saved results; unavailable files are reported via
`document_available`.

## 3. Client-side

The mobile app is **stateless** with respect to study sessions: the Home screen lists
recent sessions from `GET /api/v1/study-jobs`, and Results loads/reopens a session with
`GET /api/v1/study-jobs/{job_id}`. Exports are downloaded to the cache directory with
`expo-file-system` and handed to `expo-sharing` (Android share sheet). Device-local
offline storage (Expo SQLite) is not part of Stage 4.

There is no `topics` table. Owner-scoped topic-source records persist reviewed topic
text; Phase 2 passage matching is deterministic and computed per review request, not
stored. Phase 3 study output is computed per request and then persisted as a
`topic_study_results` row so it can be reopened, listed in history, and exported. The
Home screen merges `GET /api/v1/study-jobs` and `GET /api/v1/topic-results`;
photo results reopen by `result_id` and download exports through
`GET /api/v1/topic-results/{result_id}/export`.

## 4. JSON shapes (API payloads)

**Study item**

```json
{
  "index": 0,
  "source_refs": [{ "kind": "pdf_page", "page": 12, "page_index": 11 }],
  "text": "...original extracted text...",
  "summary": "...grounded AI summary...",
  "warnings": []
}
```

**Source reference**

```json
{ "kind": "pdf_page", "page": 12, "page_index": 11 }
```

```json
{ "kind": "docx_section", "section_label": "Anemias", "paragraph_index": 88 }
```

**Warning**

```json
{ "code": "ai_provider_error", "message": "Could not summarise Page 2." }
```

## 5. File lifecycle

```
upload ──▶ source file + documents row (expires_at)
   │
   ├─ parse ──▶ sections_json (sections/warnings persisted)
   │
   ├─ job ────▶ study_jobs row (items/summary/progress) ──▶ GET /study-jobs/{id}
   │
   ├─ export ─▶ rendered on demand from the job payload (never re-parses source)
   │
   └─ cleanup: DELETE endpoint OR expiry sweep
```

- The upload directory is a dedicated managed path (`TEMP_DIR`, default `.tmp/uploads`),
  never web-served.
- At startup the stores hydrate from SQLite; orphan files with no row are reconciled away,
  and rows whose source file is missing are kept with `available == false`.
- A periodic sweep removes expired documents and jobs (default 7 days).

## 6. Retention rules

| Data                        | Retention                                              |
| --------------------------- | ------------------------------------------------------ |
| Source file + document row  | ≤ `RETENTION_MINUTES` (default 7 days) or until DELETE |
| Extracted sections          | Same as the document row                               |
| Study-job results           | ≤ `RETENTION_MINUTES` (independent of document delete) |
| Generated export            | Streamed on demand; never persisted server-side        |

Retention cleanup uses the same window for documents and jobs; deleting a document does
not shorten a job's window, so completed results are not removed early.

## 7. Privacy notes

- No personal accounts; `document_id`/`job_id` are unguessable.
- Uploaded content and extracted text are retained server-side for the configured
  retention window (default 7 days). On the Gemini free tier, submitted summary text may
  be used by Google to improve its products.
- Logs contain identifiers, sizes, counts, and statuses only — never text or secrets.
- `DELETE /documents/{id}` removes the source file and document record immediately;
  results are retained (or cleaned by expiry) and remain exportable.
