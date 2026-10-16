# Data Model

Version 1.0 · Proposed · MVP

Two storage domains:

1. **Device (local, persistent)** — study sessions, topics, results, exports metadata.
   Primary store: Expo SQLite on the Android device.
2. **Server (temporary, ephemeral)** — uploaded files and in-flight job state only.
   No accounts, no permanent server-side documents.

## 1. Entity overview

```
Document (server, temp)         StudySession (device)
   │ 1                               │ 1
   │                                 │
   │ used by                         ├──< Topic (device)
   ▼                                 │        │ 1
StudyJob (server, temp)              │        │
   │  produces results (transferred  └──< StudyResult (device)
   │  to device on completion)                 │
   │                                           └──< Export (device)
   └── Export (server, generated on demand, streamed, not persisted long-term)
```

Server holds a temporary `Document` and `StudyJob` while processing; the client stores
the durable `StudySession`/`Topic`/`StudyResult`/`Export` records after results return.

## 2. Server-side (ephemeral)

### 2.1 Document (temporary)

| Field              | Type     | Notes                                             |
| ------------------ | -------- | ------------------------------------------------- |
| `document_id`      | UUID     | Primary key; unguessable                          |
| `filename`         | string   | Original filename (sanitized)                     |
| `format`           | enum     | `pdf` \| `docx`                                   |
| `size_bytes`       | int      | Validated size                                    |
| `status`           | enum     | `uploaded` \| `parsed` \| `failed`                |
| `extraction_status`| enum     | `ok` \| `no_text` \| `partial`                    |
| `page_count`       | int/null | PDF pages; null for DOCX                          |
| `section_count`    | int      | Logical sections                                  |
| `total_chars`      | int      | Extracted character count                         |
| `temp_path`        | string   | Managed temp location (never exposed to client)   |
| `text_ref`         | string   | In-memory/temp reference for raw + normalized text|
| `created_at`       | datetime | UTC                                               |
| `expires_at`       | datetime | `created_at + FILE_TTL_MINUTES`                   |

Not persisted beyond TTL. Deleted on `DELETE /documents/{id}` or expiry sweep.

### 2.2 StudyJob (temporary)

| Field             | Type     | Notes                                           |
| ----------------- | -------- | ----------------------------------------------- |
| `job_id`          | UUID     | Primary key                                     |
| `document_id`     | UUID     | FK → Document                                   |
| `mode`            | enum     | `verbatim` \| `summary` \| `both`               |
| `scope`           | enum     | `topics` \| `whole_document`                    |
| `topics`          | string[] | Requested topics (may be empty)                 |
| `summary_detail`  | enum     | `concise` \| `standard` \| `detailed`           |
| `status`          | enum     | `queued` \| `running` \| `completed` \| `partial` \| `failed` |
| `progress`        | object   | `{ processed_sections, total_sections }`        |
| `results`         | object   | Structured results (see `api-contract.md` §2.4) |
| `warnings`        | object[] | Global warnings                                 |
| `error`           | object?  | Error envelope if failed                        |
| `created_at`      | datetime | UTC                                             |
| `updated_at`      | datetime | UTC                                             |
| `expires_at`      | datetime | Job state TTL                                   |

Job state is in-process for the MVP (single instance). It is **not** a durable queue.

## 3. Client-side (persistent, Expo SQLite)

### 3.1 `sessions`

| Column        | Type    | Notes                                        |
| ------------- | ------- | -------------------------------------------- |
| `id`          | TEXT PK | Local UUID                                   |
| `title`       | TEXT    | Derived from filename or first topic         |
| `document_name` | TEXT  | Original filename                            |
| `document_format` | TEXT | `pdf` \| `docx`                             |
| `mode`        | TEXT    | `verbatim` \| `summary` \| `both`            |
| `scope`       | TEXT    | `topics` \| `whole_document`                 |
| `summary_detail` | TEXT | `concise` \| `standard` \| `detailed`        |
| `server_document_id` | TEXT | For cleanup/reference (may be gone)     |
| `server_job_id` | TEXT  | For reference                                |
| `created_at`  | TEXT    | ISO-8601                                     |
| `updated_at`  | TEXT    | ISO-8601                                     |

### 3.2 `topics`

| Column         | Type    | Notes                                    |
| -------------- | ------- | ---------------------------------------- |
| `id`           | TEXT PK | Local UUID                               |
| `session_id`   | TEXT FK | → `sessions.id` (cascade delete)         |
| `topic`        | TEXT    | User-entered topic                       |
| `match_status` | TEXT    | `matched` \| `partial` \| `not_found`    |
| `order_index`  | INTEGER | Display order                            |

### 3.3 `results`

| Column        | Type    | Notes                                             |
| ------------- | ------- | ------------------------------------------------- |
| `id`          | TEXT PK | Local UUID                                        |
| `session_id`  | TEXT FK | → `sessions.id`                                   |
| `topic_id`    | TEXT FK | → `topics.id` (nullable for whole-document)       |
| `verbatim`    | TEXT    | JSON array of passages with source refs           |
| `summary`     | TEXT    | AI summary or null                                |
| `source_refs` | TEXT    | JSON array of source references                   |
| `warnings`    | TEXT    | JSON array                                        |
| `whole_document_summary` | TEXT | Used for whole-document scope         |

### 3.4 `exports`

| Column        | Type    | Notes                                          |
| ------------- | ------- | ---------------------------------------------- |
| `id`          | TEXT PK | Local UUID                                     |
| `session_id`  | TEXT FK | → `sessions.id`                                |
| `format`      | TEXT    | `pdf` \| `docx`                                |
| `local_uri`   | TEXT    | Device file URI for the saved export           |
| `status`      | TEXT    | `saved` \| `shared`                            |
| `created_at`  | TEXT    | ISO-8601                                       |

Exports are saved to app storage / user-chosen location and referenced, not duplicated
in the DB.

## 4. JSON shapes (as returned by the API and stored on device)

**Verbatim passage**

```json
{
  "text": "...original text...",
  "source": { "kind": "pdf_page", "page": 12, "page_index": 11 }
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
{ "code": "topic_not_found", "message": "This topic wasn't found in the uploaded material." }
```

## 5. File lifecycle

```
upload ──▶ temp file (expires_at)
   │
   ├─ parse ──▶ raw text + normalized text (memory/temp)
   │
   ├─ job ────▶ results returned to device ──▶ persisted in SQLite
   │
   └─ cleanup: DELETE endpoint OR TTL sweep OR job completion
```

- Temp directory is a dedicated managed path (e.g. system temp or `./.tmp`), never web-served.
- Cleanup runs on a lightweight scheduled task plus on-demand checks.
- Device records survive server cleanup (offline-readable).

## 6. Retention rules

| Data                 | Retention                                             |
| -------------------- | ----------------------------------------------------- |
| Uploaded temp file   | ≤ `FILE_TTL_MINUTES` (default 60) or until DELETE     |
| Parsed text          | In request/job scope; released after job completes    |
| Job state            | Job TTL (e.g. 1 hour) or process lifetime              |
| Generated export     | Streamed on demand; not persisted server-side          |
| Device sessions      | Until the user deletes them                            |
| Device export files  | Until the user deletes them                            |

## 7. Privacy notes

- No personal accounts; the device is the source of truth for saved notes.
- Server never retains document content beyond the TTL.
- Logs contain identifiers, sizes, counts, and statuses only — never text or secrets.
- Deleting a session on device deletes its rows and any referenced local export files.
