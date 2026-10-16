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
    "file_ttl_minutes": 60
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

For scanned/empty PDFs:

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
      "message": "This PDF appears to be scanned; text extraction isn't available for it yet."
    }
  ]
}
```

- `extraction.status`: `ok` | `no_text` | `partial`.
- DOCX uploads report `page_count: null` and use `section_count` for structure.

### 2.3 `POST /api/v1/study-jobs`

Start topic extraction and/or summarization.

**Request** (`application/json`)

```json
{
  "document_id": "5f1c...e2",
  "mode": "both",
  "scope": "topics",
  "topics": [
    "Iron deficiency anemia",
    "Megaloblastic anemia",
    "Leukemia classification"
  ],
  "summary_detail": "standard"
}
```

| Field            | Type   | Required | Allowed values                                  |
| ---------------- | ------ | -------- | ----------------------------------------------- |
| `document_id`    | string | yes      | Existing document UUID                          |
| `mode`           | string | yes      | `verbatim` \| `summary` \| `both`               |
| `scope`          | string | yes      | `topics` \| `whole_document`                    |
| `topics`         | list   | cond.    | Required & non-empty when `scope == "topics"`   |
| `summary_detail` | string | no       | `concise` \| `standard` \| `detailed` (default `standard`) |

**Validation rules**

- `scope == "topics"` requires ≥ 1 non-empty, trimmed topic; else `422`.
- `scope == "whole_document"` allows an empty/absent `topics` list.
- `summary_detail` ignored when `mode == "verbatim"`.

**202/200 Response**

```json
{
  "job_id": "a91b...77",
  "document_id": "5f1c...e2",
  "status": "queued",
  "created_at": "2026-01-15T10:00:00Z"
}
```

- Small documents may return `status: "completed"` with results inline (see §2.4).
- Large documents return `status: "queued"` (or `running`) for polling.

### 2.4 `GET /api/v1/study-jobs/{job_id}`

Job status and structured results.

**200 Response**

```json
{
  "job_id": "a91b...77",
  "document_id": "5f1c...e2",
  "status": "completed",
  "mode": "both",
  "scope": "topics",
  "summary_detail": "standard",
  "created_at": "2026-01-15T10:00:00Z",
  "updated_at": "2026-01-15T10:00:07Z",
  "progress": { "processed_sections": 42, "total_sections": 42 },
  "topics": [
    {
      "topic": "Iron deficiency anemia",
      "match_status": "matched",
      "verbatim_passages": [
        {
          "text": "Iron deficiency anemia is a microcytic, hypochromic anemia...",
          "source": { "kind": "pdf_page", "page": 12, "page_index": 11 }
        }
      ],
      "summary": "Iron deficiency anemia is a microcytic hypochromic anemia caused by...",
      "source_refs": [
        { "kind": "pdf_page", "page": 12, "page_index": 11 },
        { "kind": "pdf_page", "page": 14, "page_index": 13 }
      ],
      "warnings": []
    },
    {
      "topic": "Leukemia classification",
      "match_status": "not_found",
      "verbatim_passages": [],
      "summary": null,
      "source_refs": [],
      "warnings": [
        { "code": "topic_not_found", "message": "This topic wasn't found in the uploaded material." }
      ]
    }
  ],
  "whole_document_summary": null,
  "warnings": []
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

**Match status**

| Value       | Meaning                                            |
| ----------- | -------------------------------------------------- |
| `matched`   | Strong local match found                           |
| `partial`   | Weak match; returned with a warning, may use AI assist |
| `not_found` | No usable match; no fabricated content             |

**Whole-document scope** returns `whole_document_summary` (string) and may omit `topics`.

**Source reference objects**

| Field        | Applies to | Notes                                  |
| ------------ | ---------- | -------------------------------------- |
| `kind`       | all        | `pdf_page` \| `docx_section`           |
| `page`       | pdf        | 1-based, user-visible                  |
| `page_index` | pdf        | 0-based internal                       |
| `section_label` | docx    | Nearest heading text                   |
| `paragraph_index` | docx  | Approximate anchor                     |

### 2.5 `GET /api/v1/study-jobs/{job_id}/export`

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

The job must be `completed` or `partial`; otherwise `409`.

### 2.6 `DELETE /api/v1/documents/{document_id}`

Delete an uploaded document and associated temporary data.

**204 No Content** on success. `404` if unknown. Associated temp files removed
immediately; results already returned to the client are unaffected (they live on device).

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
| `file_too_large`           | 413  | Exceeds `MAX_UPLOAD_MB`                    |
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
| `AI_PROVIDER_BASE_URL`      | —       | Provider API base                |
| `AI_PROVIDER_API_KEY`       | —       | Secret (server only)             |
| `AI_MODEL`                  | —       | Model identifier                 |
| `AI_REQUEST_TIMEOUT_S`      | 60      | Per-request timeout              |
| `MAX_UPLOAD_MB`             | 25      | Upload size cap                  |
| `MAX_SECTION_CHARS`         | 6000    | Chunk size                       |
| `MAX_AI_TOKENS_PER_REQUEST` | 2048    | Output token cap                 |
| `FILE_TTL_MINUTES`          | 60      | Temp file lifetime               |
| `JOB_TIMEOUT_S`             | 300     | Max job duration                 |
| `RATE_LIMIT_PER_MINUTE`     | 30      | Simple in-process limiter        |

## 5. Accepted files

| Aspect      | Rule                                                        |
| ----------- | ----------------------------------------------------------- |
| Types       | PDF (`application/pdf`), DOCX (Open XML)                    |
| Legacy DOCX | `.doc` (binary) is **not** supported — reject clearly       |
| Validation  | extension + magic bytes (`%PDF-`, `PK\x03\x04`)             |
| Size        | ≤ `MAX_UPLOAD_MB`                                           |
| Encryption  | Rejected with `encrypted_document`                          |
| Scanned PDF | Accepted, parsed to `no_text`, reported as a limitation     |

## 6. Versioning

- Path-versioned (`/api/v1`).
- Additive, backward-compatible changes within v1.
- Breaking changes require `/api/v2` and a documented migration.
