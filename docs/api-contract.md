# API Contract

Version 1.0 · Proposed · Base path: `/api/v1` · Content type: `application/json`
unless noted. File endpoints use `multipart/form-data` (upload) and
`application/octet-stream` (download).

## 1. Conventions

- **Base URL (dev)**: `http://localhost:8000`
- **Auth**: none in the MVP. AI provider credentials are server-side only.
- **IDs**: UUIDv4 strings (e.g. `document_id`, `job_id`).
- **Timestamps**: ISO-8601 UTC.
- **Errors**: consistent envelope (see §3).
- **Idempotency**: uploads create new documents; job creation is not idempotent.
- **Diagnostics**: every response includes an `X-Request-ID` header; upload and
  export responses also include `Server-Timing` durations. These headers never
  contain document content, filenames, or credentials.
- **CORS**: open in dev for the emulator; restricted in any deployment.

## 2. Endpoints

### 2.1 `GET /health`

Backend availability and AI configuration status.

**200 Response**

```json
{
  "status": "ok",
  "version": "1.0.0",
  "ai": {
    "provider": "configured",
    "model": "example-model",
    "reachable": true
  },
  "limits": {
    "max_upload_mb": 25,
    "retention_minutes": 10080
  }
}
```

- `status`: `ok` | `degraded`.
- `ai.provider`: `configured` | `missing`.
- `ai.reachable`: whether the provider responded to a lightweight check (may be `null`
  if checks are disabled).

### 2.2 `POST /api/v1/documents`

Upload and parse a PDF/DOCX.

**Request**: `multipart/form-data`

| Field  | Type   | Required | Notes                        |
| ------ | ------ | -------- | ---------------------------- |
| `file` | binary | yes      | PDF or DOCX, within size cap |

**201 Response**

```json
{
  "document_id": "5f1c...e2",
  "filename": "lecture-03-hematology.pdf",
  "format": "pdf",
  "size_bytes": 1048576,
  "page_count": 42,
  "extraction": {
    "status": "ok",
    "text_extractable": true,
    "section_count": 42,
    "total_chars": 81234
  },
  "warnings": []
}
```

For scanned/empty PDFs when no usable text is recovered:

```json
{
  "document_id": "5f1c...e2",
  "filename": "scan.pdf",
  "format": "pdf",
  "size_bytes": 2097152,
  "page_count": 10,
  "extraction": {
    "status": "no_text",
    "text_extractable": false,
    "section_count": 0,
    "total_chars": 0
  },
  "warnings": [
    {
      "code": "scanned_pdf_no_text",
      "message": "This PDF appears to be scanned; no usable text could be extracted."
    }
  ]
}
```

- `extraction.status`: `ok` | `no_text` | `partial`.
- `total_chars` counts non-whitespace characters in usable sections.
- A PDF page counts as usable when it has at least `MIN_PDF_CHARS_PER_PAGE`
  non-whitespace characters. Pages below that threshold are excluded from
  `section_count` and reported through a `partial_extraction` warning.
- Damaged PDFs that the parser had to repair are returned as `partial` with a
  `document_repaired` warning; they are never reported as scanned. `is_repaired`
  PDFs with no usable text also use `partial`, not `no_text`.
- DOCX uploads report `page_count: null` and use `section_count` for structure.
- Scanned PDF pages and images embedded in DOCX are OCR'd locally with the Tesseract
  CLI when it is available. OCR is bounded by `OCR_PAGE_LIMIT`, rendered at
  `OCR_DPI`, and limited by `OCR_TIMEOUT_S` per page/image. No cloud OCR is used.

**Extraction warning codes**

| Code                      | Meaning                                              |
| ------------------------- | ---------------------------------------------------- |
| `document_repaired`       | PDF was repaired; text may be missing                |
| `partial_extraction`      | Some PDF pages lacked usable text                    |
| `scanned_pdf_no_text`     | PDF appears scanned and no usable text was recovered |
| `ocr_unavailable`         | Tesseract is not installed/executable                |
| `ocr_page_limit_reached`  | PDF OCR stopped at `OCR_PAGE_LIMIT`                  |
| `ocr_image_limit_reached` | DOCX image OCR stopped at `OCR_PAGE_LIMIT`           |
| `ocr_timeout`             | OCR timed out for one or more items                  |
| `ocr_failed`              | OCR could not read one or more items                 |
| `ocr_no_text`             | OCR ran but found no usable text on a PDF page       |
| `no_extractable_text`     | DOCX contained no usable text                        |

Extracted sections are stored server-side in SQLite keyed by `document_id`, so a later
`POST /api/v1/study-jobs` can reuse the page/section-aware sections without re-uploading.
Document metadata and the original file are retained for `RETENTION_MINUTES` (default
7 days) and removed on `DELETE /documents/{id}`, on expiry, or by the periodic cleanup
sweep. Records survive backend restarts; source files with no record are reconciled away
at startup.

**Study job warning / error codes**

| Code                  | HTTP | Meaning                                                        |
| --------------------- | ---- | -------------------------------------------------------------- |
| `document_not_found`  | 404  | No such document, or it expired                                |
| `no_extractable_text` | 409  | Document has no source text to study                           |
| `ai_not_configured`   | 503  | AI mode requested but `GEMINI_API_KEY` is not set              |
| `ai_rate_limited`     | 429  | Gemini returned 429; retry later                               |
| `ai_timeout`          | 504  | Gemini request timed out                                       |
| `ai_provider_error`   | 502  | Gemini 4xx/5xx/network failure; also used in job `error`       |
| `ai_invalid_response` | 502  | Gemini returned an empty, blocked, or malformed response       |
| `document_unavailable`| 409  | Source file is gone; new processing blocked, results retained  |
| `job_not_found`       | 404  | Unknown or expired study job                                   |
| `job_not_ready`       | 409  | Export requested before the job finished successfully          |
| `export_failed`       | 500  | Export generation failed                                       |

### 2.3 `POST /api/v1/study-jobs`

Start a study job for an uploaded document.

**Request** (`application/json`)

```json
{
  "document_id": "5f1c...e2",
  "mode": "text_and_summaries"
}
```

| Field         | Type   | Required | Allowed values |
| ------------- | ------ | -------- | -------------- |
| `document_id` | string | yes      | Existing document UUID |
| `mode`        | string | yes      | `extracted_text_only` \| `text_and_summaries` \| `summaries_only` |

**Modes**

| Mode                  | AI call | Returns                                                        |
| --------------------- | ------- | -------------------------------------------------------------- |
| `extracted_text_only` | no      | Source text per extracted section/page                         |
| `text_and_summaries`  | yes     | Source text **and** a grounded AI summary per unit             |
| `summaries_only`      | yes     | Grounded AI summaries + source references, no source text      |

**Validation / errors**

- Unknown or expired `document_id` → `404 document_not_found`.
- Source file is no longer available → `409 document_unavailable`.
- Document has no extractable text → `409 no_extractable_text`.
- AI mode with no `GEMINI_API_KEY` → `503 ai_not_configured`
  (`extracted_text_only` still works).
- Invalid `mode` → `422 validation_error`.

**200 / 202 Response**

Jobs with ≤ `SYNC_PROCESSING_MAX_SECTIONS` sections run synchronously and return the full
job object (`status` `completed`, `partial`, or `failed`). Larger jobs return `202` with
`status: "queued"`; poll `GET /study-jobs/{job_id}` until a terminal status.

### 2.4 `GET /api/v1/study-jobs/{job_id}`

Job status and structured results.

**200 Response**

```json
{
  "job_id": "a91b...77",
  "document_id": "5f1c...e2",
  "filename": "lecture.pdf",
  "format": "pdf",
  "mode": "text_and_summaries",
  "status": "completed",
  "created_at": "2026-01-15T10:00:00Z",
  "updated_at": "2026-01-15T10:00:07Z",
  "page_count": 25,
  "item_count": 2,
  "document_summary": "Overall grounded summary of the document.",
  "items": [
    {
      "index": 0,
      "source_refs": [{ "kind": "pdf_page", "page": 12, "page_index": 11 }],
      "text": "Iron deficiency anemia is a microcytic, hypochromic anemia...",
      "summary": "Iron deficiency anemia is a microcytic hypochromic anemia...",
      "warnings": []
    },
    {
      "index": 1,
      "source_refs": [
        { "kind": "docx_section", "section_label": "Anemias", "paragraph_index": 88 }
      ],
      "text": null,
      "summary": "Megaloblastic anemia is macrocytic...",
      "warnings": []
    }
  ],
  "warnings": [],
  "error": null,
  "progress": { "processed_units": 2, "total_units": 2 },
  "document_available": true
}
```

**Status values**

| Status      | Meaning                                            |
| ----------- | -------------------------------------------------- |
| `queued`    | Accepted, not started                              |
| `running`   | Processing                                         |
| `completed` | Finished successfully                              |
| `partial`   | Finished with recoverable errors (see `warnings`)  |
| `failed`    | Terminal failure (see `error`)                     |

**Item fields**

| Field         | Notes                                                              |
| ------------- | ------------------------------------------------------------------ |
| `index`       | Zero-based order                                                   |
| `source_refs` | One or more source anchors covered by this item                   |
| `text`        | Original extracted source; `null` in `summaries_only`             |
| `summary`     | Grounded AI summary; `null` in `extracted_text_only` or on failure |
| `warnings`    | Per-item warnings (for example `ai_provider_error`)               |

`document_summary` is present only for AI modes and only when at least one unit
succeeded. AI summaries are normalized to plain text: markdown headings, emphasis,
and code fences are removed, and bullets use a leading `- `. When the combined section
summaries exceed the summary size limit, the overview is reduced in batches and an
`overview_reduced` warning is returned instead of silently truncating text. `error` is
a `{ code, message }` object when the job is `failed`. The `progress` object reports
`processed_units` / `total_units`. `document_available` is `false` when the original
source file is no longer present; the job result is still returned and can still be
exported.

**Source reference objects**

| Field             | Applies to | Notes                                  |
| ----------------- | ---------- | -------------------------------------- |
| `kind`            | all        | `pdf_page` \| `docx_section`           |
| `page`            | pdf        | 1-based, user-visible                  |
| `page_index`      | pdf        | 0-based internal                       |
| `section_label`   | docx       | Nearest heading text                   |
| `paragraph_index` | docx       | Approximate anchor                     |

### 2.5 `GET /api/v1/study-jobs`

List recent study jobs, newest first, for the history/review workflow.

**Query parameters**

| Param    | Type | Default | Notes                                    |
| -------- | ---- | ------- | ---------------------------------------- |
| `limit`  | int  | 20      | 1–100                                     |
| `offset` | int  | 0       | Skip N newest jobs                        |

**200 Response** — array of summary objects:

```json
[
  {
    "job_id": "a91b...77",
    "document_id": "5f1c...e2",
    "filename": "lecture.pdf",
    "format": "pdf",
    "mode": "text_and_summaries",
    "status": "completed",
    "created_at": "2026-01-15T10:00:00Z",
    "updated_at": "2026-01-15T10:00:07Z",
    "page_count": 25,
    "item_count": 2,
    "document_available": true
  }
]
```

List entries omit `items`/`document_summary`; fetch `GET /study-jobs/{job_id}` for the
full result. Results are served from server-side persistence and survive restarts until
their retention window ends.

### 2.6 `GET /api/v1/study-jobs/{job_id}/export`

Generate and download the notes.

**Query parameters**

| Param    | Allowed         | Required |
| -------- | --------------- | -------- |
| `format` | `pdf` \| `docx` | yes      |

**200 Response**

- `Content-Type`: `application/pdf` or
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- `Content-Disposition`: `attachment; filename="<session-title>.<ext>"`
- Body: binary file stream.

The job must be `completed` or `partial`; otherwise `409 job_not_ready`. Exports are
rendered on demand from the persisted job payload (not by re-parsing the source), so they
work even after the source file is gone. Content respects the job mode:
`extracted_text_only` (source text), `text_and_summaries` (source text + summary),
`summaries_only` (summaries and source references only — no full source text). Because
rendering is on demand, clients should not issue a second export request just to recover
an error; use `GET /study-jobs/{job_id}` for job status and error context instead.

### 2.7 `DELETE /api/v1/documents/{document_id}`

Delete a stored document and its source file.

**204 No Content** on success. `404` if unknown. The source file and document record are
removed immediately. **Completed study-job results are retained** for the retention
window so they remain readable and exportable; they simply report
`document_available: false`.

## 3. Error format

All non-2xx responses use one envelope:

```json
{
  "error": {
    "code": "unsupported_file_type",
    "message": "Only PDF and DOCX files are supported.",
    "details": { "received": "image/png" },
    "request_id": "req_8c21"
  }
}
```

`details` is optional and must not contain secrets or document content.

**Error codes**

| Code                       | HTTP | Meaning                                    |
| -------------------------- | ---- | ------------------------------------------ |
| `validation_error`         | 422  | Request body failed schema validation      |
| `unsupported_file_type`    | 415  | Not a PDF/DOCX                             |
| `file_too_large`           | 413  | Exceeds `MAX_UPLOAD_MB`, or a DOCX expands beyond `MAX_DOCX_UNCOMPRESSED_MB` |
| `empty_file`               | 400  | Zero-byte or unreadable upload             |
| `corrupt_document`         | 400  | Parser could not open the file             |
| `encrypted_document`       | 400  | Password-protected / encrypted             |
| `no_extractable_text`      | 422  | Scanned/empty PDF (job cannot proceed)     |
| `document_not_found`       | 404  | Unknown `document_id`                      |
| `job_not_found`            | 404  | Unknown `job_id`                           |
| `job_not_ready`            | 409  | Export requested before completion         |
| `ai_provider_unavailable`  | 503  | Provider timeout / unreachable             |
| `ai_rate_limited`          | 429  | Provider rate limit                        |
| `ai_quota_exhausted`       | 402  | Provider credits exhausted                 |
| `ai_invalid_response`      | 502  | Model returned unusable output             |
| `ai_not_configured`        | 503  | Missing server-side credentials            |
| `processing_failed`        | 500  | Unexpected server error (no content leaked)|
| `export_failed`            | 500  | Document generation failed                 |

Client guidance:

- `4xx`: show the actionable `message`; do not auto-retry except `429`.
- `429`/`503`: retry with backoff; surface "AI is busy, retrying…".
- `no_extractable_text`: explain the scanned/no-text limitation, offer a different file.

## 4. Configurable settings (server)

Environment-driven (see `.env.example`):

| Variable                    | Default | Purpose                          |
| --------------------------- | ------- | -------------------------------- |
| `GEMINI_API_BASE_URL`       | `https://generativelanguage.googleapis.com/v1beta` | Provider API base |
| `GEMINI_API_KEY`            | —       | Secret (server only)             |
| `GEMINI_MODEL`              | `gemini-3.8-flash` | Model identifier        |
| `GEMINI_REQUEST_TIMEOUT_S`  | 60      | Per-request timeout              |
| `GEMINI_MAX_OUTPUT_TOKENS`  | 2048    | Output token cap                 |
| `MAX_UPLOAD_MB`             | 25      | Upload size cap                  |
| `MAX_DOCX_UNCOMPRESSED_MB`  | 100     | Decompressed DOCX size cap       |
| `MIN_PDF_CHARS_PER_PAGE`    | 15      | Non-whitespace chars per usable PDF page |
| `OCR_TESSERACT_PATH`        | `tesseract` | Tesseract executable          |
| `OCR_PAGE_LIMIT`            | 30      | OCR operations per document; `0` disables |
| `OCR_DPI`                   | 200     | PDF page render resolution       |
| `OCR_TIMEOUT_S`             | 30      | Per-page/image OCR timeout       |
| `OCR_LANGUAGE`              | `eng`   | Tesseract language data          |
| `MAX_SECTION_CHARS`         | 6000    | Chunk size                       |
| `MAX_SUMMARY_CHUNK_CHARS`   | 12000   | Max source chars sent to the AI per request |
| `RETENTION_MINUTES`         | 10080   | Document + job retention (7 days) |
| `DATABASE_PATH`             | `.tmp/study.db` | SQLite file for documents/jobs |
| `JOB_TIMEOUT_S`             | 300     | Max job duration                 |
| `RATE_LIMIT_PER_MINUTE`     | 30      | Simple in-process limiter        |
| `SYNC_PROCESSING_MAX_SECTIONS` | 20   | Jobs ≤ this many sections run synchronously and return results inline; larger jobs return a `job_id` for polling |

## 5. Accepted files

| Aspect      | Rule                                                        |
| ----------- | ----------------------------------------------------------- |
| Types       | PDF (`application/pdf`), DOCX (Open XML)                    |
| Legacy DOCX | `.doc` (binary) is **not** supported — reject clearly       |
| Validation  | extension + magic bytes (`%PDF-` within the first 1024 bytes, `PK\x03\x04`) |
| Size        | ≤ `MAX_UPLOAD_MB`; DOCX uncompressed ≤ `MAX_DOCX_UNCOMPRESSED_MB` |
| Encryption  | Rejected with `encrypted_document`                          |
| Scanned PDF | Accepted; bounded local Tesseract OCR when available, otherwise reported as a limitation |
| DOCX images | Embedded images are OCR'd when local Tesseract is available; bounded by `OCR_PAGE_LIMIT` |

## 6. Versioning

- Path-versioned (`/api/v1`).
- Additive, backward-compatible changes within v1.
- Breaking changes require `/api/v2` and a documented migration.
