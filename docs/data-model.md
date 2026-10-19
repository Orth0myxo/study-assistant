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

Source availability (`available`) is derived at read time from the file existing on disk.

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

## 3. Client-side

The mobile app is **stateless** with respect to study sessions: the Home screen lists
recent sessions from `GET /api/v1/study-jobs`, and Results loads/reopens a session with
`GET /api/v1/study-jobs/{job_id}`. Exports are downloaded to the cache directory with
`expo-file-system` and handed to `expo-sharing` (Android share sheet). Device-local
offline storage (Expo SQLite) is not part of Stage 4.

No `topics` table anywhere; topic matching is out of scope.

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
