# API Contract

Version 1.0 · Proposed · Base path: `/api/v1` · Content type: `application/json`
unless noted. File endpoints use `multipart/form-data` (upload) and
`application/octet-stream` (download).

## 1. Conventions

- **Base URL (dev)**: `http://localhost:8000`
- **Auth**: every `/api/v1/` data endpoint requires a per-user bearer token
  obtained from `POST /api/v1/users`. A valid per-user token is sufficient even
  when `APP_ACCESS_TOKEN` is configured. Registration is exempt so a device can
  bootstrap its identity. If a client sends the shared `X-App-Token` (or
  `?token=` for native downloads), it must match the configured value. Non-API
  routes remain behind the shared token when it is configured. AI provider
  credentials are server-side only.
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

### 2.3 `POST /api/v1/documents/{document_id}/ranges`

Select persisted extracted sections without using an AI provider. The endpoint
is scoped to the authenticated user and returns each selected section's exact
stored `raw_text` and anchor.

**Request**

```json
{ "selector": "pages 3, 5 and 8-10" }
```

PDF selectors accept page numbers, ranges, lists, and `to` ranges. An invalid
PDF page returns 422 with the document's actual `page_count` in `error.details`.
Pages that exist but were dropped because they had insufficient text are
reported in `warnings`. DOCX has no page anchors: numeric selectors map to
paragraph indexes and chapter selectors match stored heading labels.

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
| `ai_not_configured`   | 503  | No configured AI provider is available                         |
| `ai_rate_limited`     | 429  | An AI provider returned a retryable rate limit                 |
| `ai_quota_exhausted`  | 429  | All configured providers are exhausted; verbatim remains available |
| `ai_timeout`          | 504  | An AI provider request timed out                               |
| `ai_provider_error`   | 502  | AI provider 4xx/5xx/network failure; also used in job `error`  |
| `ai_invalid_response` | 502  | AI provider returned an empty, blocked, or malformed response |
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

### 2.8 Topic sources

Topic sources are owner-scoped typed jottings plus optional note images with
editable OCR transcripts and an optional attached source document. The mobile
composer uses them before topic extraction. OCR is best-effort; clients must let
the user review and edit the recognized text.

#### `POST /api/v1/topic-sources`

**Request**: `multipart/form-data`

| Field           | Type   | Required | Notes |
| --------------- | ------ | -------- | ----- |
| `jottings_text` | string | one of   | Typed/pasted study jottings; up to 50,000 characters |
| `images`        | binary | one of   | Zero or more PNG/JPEG images; repeat the field for multiple files |
| `document_id`   | string | no       | Owned, unexpired source document to attach |

At least one of `jottings_text` or `images` is required. The server validates
image bytes rather than trusting filename extensions. OCR is bounded by
`OCR_PAGE_LIMIT`; extra images are stored with `ocr_status: "skipped"` and an
`ocr_limit_reached` warning. Uploads are rejected when the per-user active-source
quota is full.

**201 Response**

```json
{
  "source_id": "8d21...c4",
  "status": "ok",
  "document": {
    "document_id": "5f1c...e2",
    "filename": "lecture-03.pdf",
    "format": "pdf",
    "page_count": 42
  },
  "document_available": true,
  "image_count": 1,
  "jottings_text": "Calvin cycle",
  "total_chars": 25,
  "topics": [],
  "images": [
    {
      "index": 0,
      "filename": "page-1.png",
      "content_type": "image/png",
      "size_bytes": 512000,
      "width": 1600,
      "height": 2200,
      "ocr_status": "ok",
      "transcript_source": "ocr",
      "text": "Chapter 1 ...",
      "image_url": "/api/v1/topic-sources/8d21...c4/images/0",
      "warnings": [
        {"code": "ocr_review_recommended", "message": "..."}
      ]
    }
  ],
  "warnings": [
    {"code": "ocr_handwriting_unreliable", "message": "..."}
  ],
  "created_at": "2026-10-23T10:00:00Z",
  "updated_at": "2026-10-23T10:00:00Z"
}
```

- `status`: `ok` when every supplied text source has non-empty text, `partial`
  when some do, `no_text` when none do. Typed jottings and photo transcripts are
  both text sources. It is derived from text, not from `ocr_status`, so a manual
  correction can make an OCR failure usable.
- `ocr_status`: `ok` | `empty` | `timeout` | `unavailable` | `failed` |
  `skipped` | `manual`.
- `transcript_source`: `ocr` until the user edits that image, then `manual`.
- `document_available`: whether the attached source file still exists. The
  document metadata is retained even after deletion for context.
- Expired sources are not returned. Reads never delete data; the retention
  sweep removes expired sources and their image directories.

#### `GET /api/v1/topic-sources?limit=20&offset=0`

Lists the caller's unexpired jottings sources, newest first. `limit` is 1-100.

**200 Response**: array of summary objects.

```json
[
  {
    "source_id": "8d21...c4",
    "status": "ok",
    "document_id": "5f1c...e2",
    "filename": "lecture-03.pdf",
    "format": "pdf",
    "image_count": 1,
    "total_chars": 25,
    "topics": ["Calvin cycle"],
    "created_at": "2026-10-23T10:00:00Z",
    "updated_at": "2026-10-23T10:05:00Z",
    "document_available": true
  }
]
```

#### `GET /api/v1/topic-sources/{source_id}`

**200** with the same response shape. **404 `topic_source_not_found`** for an
unknown, other-user, or expired source.

#### `GET /api/v1/topic-sources/{source_id}/images/{image_index}`

Streams the stored note image inline for the owning user. **200** with the
stored PNG/JPEG content type and `Content-Disposition: inline`. **404
`topic_source_not_found`** for an unknown or other-user source, **404
`image_not_found`** when the index does not exist, and **404
`topic_image_unavailable`** when the stored file is missing. Paths are resolved
and must remain inside the topic-source storage root.

#### `PATCH /api/v1/topic-sources/{source_id}`

**Request**: `application/json`

```json
{
  "topics": ["Photosynthesis", "Calvin cycle"],
  "jottings_text": "Corrected typed jottings",
  "transcripts": [{"index": 0, "text": "Corrected transcript text"}]
}
```

At least one of `topics`, `jottings_text`, or `transcripts` is required. Topics
are trimmed, de-duplicated, and bounded. A transcript edit sets that image to
`ocr_status: "manual"` and `transcript_source: "manual"`, clears its stale OCR
warnings, and adds `transcript_manually_edited`. Changing `jottings_text` or any
transcript clears the saved topic list unless `topics` is supplied in the same
request, so the next review re-segments the corrected input. The source status
and `ocr_incomplete` warning are recomputed from non-empty text.

Updates are optimistic. If a concurrent update wins, the server retries; on a
persistent conflict it returns **409 `topic_source_conflict`**. Clients should
reload and reapply their edit.

**200** with the updated source. Other errors: `no_update` (400),
`image_not_found` (400), `duplicate_transcript_index` (400), `too_many_topics`
(422), `invalid_topic` (422), `topic_too_long` (422),
`transcript_too_long` (422), `jottings_too_long` (422),
`topic_source_not_found` (404).

#### `DELETE /api/v1/topic-sources/{source_id}`

**204 No Content** on success. Deletes the source record, its transcripts, and
its image directory immediately; the quota is freed. **404
`topic_source_not_found`** for an unknown or other-user source. An expired
source owned by the caller may still be deleted explicitly. Attached documents
are not deleted.

### 2.9 `POST /api/v1/topic-sources/{source_id}/topic-review`

Deterministically segment typed jottings and corrected photo transcripts, then
match each study topic to the linked document's extracted sections. This
endpoint uses no AI provider and is the shared foundation for output choices A
(source passages only), B (AI study material grounded in those passages), and C
(both).

**Request**: optional `application/json`

```json
{
  "topics": ["Photosynthesis", "Calvin cycle"]
}
```

- Omit `topics`, or set it to `null`, to segment the current typed jottings and
  corrected photo transcripts. This is the initial review call.
- Send `topics` after the user reviews or edits the list to re-run matching
  without changing persisted source topics. Empty lists are allowed and return
  no topics with a `no_topics` warning.
- Provided topics use the same limits and errors as `PATCH` (`too_many_topics`,
  `invalid_topic`, `topic_too_long`).

**200 Response**

```json
{
  "source_id": "8d21...c4",
  "document": {
    "document_id": "5f1c...e2",
    "filename": "lecture-03.pdf",
    "format": "pdf",
    "page_count": 42
  },
  "document_available": true,
  "source_material": {
    "status": "available",
    "section_count": 2
  },
  "segmentation": {
    "input": "combined",
    "topic_count": 1,
    "warnings": []
  },
  "topics": [
    {
      "topic_id": "topic_1",
      "text": "Calvin cycle",
      "status": "matched",
      "confidence": 1.0,
      "source_labels": ["Typed jottings"],
      "matches": [
        {
          "passage_id": "5f1c...e2:1",
          "section_index": 1,
          "anchor": {"kind": "pdf_page", "page": 2, "page_index": 1},
          "passage": "The Calvin cycle fixes carbon dioxide.",
          "passage_start": 0,
          "passage_end": 38,
          "excerpt": "The Calvin cycle fixes carbon dioxide.",
          "excerpt_start": 0,
          "excerpt_end": 38,
          "score": 1.0,
          "coverage": 1.0,
          "phrase_match": true,
          "matched_terms": ["calvin", "cycle"]
        }
      ],
      "warnings": []
    }
  ],
  "warnings": []
}
```

#### Source material and ownership

- The source itself is owner-scoped. An unknown, other-user, or expired source
  returns **404 `topic_source_not_found`**.
- The linked document is looked up with the same owner scope and must still be
  available. If it is missing, unowned, or expired, the response is still
  **200** but reports `document_available: false`,
  `source_material.status: "unavailable"`, and a `document_unavailable`
  warning. Topics are segmented but explicitly `unmatched` with no passages.
- `source_material.status`:
  - `available`: matching may use the persisted extraction.
  - `not_linked`: no source document is attached; no matching is possible.
  - `unavailable`: the linked record or source file is not available.
  - `empty`: the linked document has no extractable sections.
- `source_material.section_count` is the number of usable extracted sections.
  It is `0` unless status is `available`.
- Matching never falls back to an unrelated or other-user document.

#### Segmentation contract

`segmentation.input` is `jottings` when only typed jottings were segmented,
`transcript` when only photo transcripts were segmented, `combined` when both
were used, or `provided` when the request supplied reviewed topics. Each topic
includes `source_labels` identifying the typed jottings and/or photo transcript
that supplied it. Segmentation is ordered and deterministic:

- Numbered items, inline numbered lists, bullets, pipes, and semicolons become
  separate topics.
- OCR edge noise, page labels, pure numbers, and hyphenated line wraps are
  removed/normalized.
- Topics are de-duplicated only when their normalized alphanumeric text is
  identical. Related but distinct topics (for example `Cell cycle` and
  `Cell cycle regulation`) are never merged.
- Limits use `TOPIC_MAX_TOPICS` and `TOPIC_MAX_TOPIC_CHARS`. A truncated result
  reports `topic_limit_reached` and/or `topic_truncated` in
  `segmentation.warnings`.
- `topic_id` is stable only for the ordering in this response (`topic_1`,
  `topic_2`, ...). It is not persisted; clients should send `text` when
  re-running review.

#### Matching contract and thresholds

Matching is deterministic and local:

1. Normalize both topic and section text to lowercase ASCII alphanumeric words
   (Unicode compatibility/diacritic normalization); remove common stop words.
2. For each extracted section compute term `coverage` (matched topic terms /
   total topic terms) and `phrase_match` (the normalized topic phrase appears
   contiguously).
3. Score: `0.80 * coverage + 0.20 * phrase_match`.
4. `MIN_MATCH_SCORE = 0.70`; candidates below it are discarded rather than
   silently accepted.
5. `AMBIGUITY_GAP = 0.08`. If the top two candidates are within this gap, status
   is `ambiguous`; otherwise the best candidate wins and other candidates above
   the threshold may still be returned with a `multiple_source_matches`
   warning. At most three passages are returned per topic.
6. Status is `matched`, `ambiguous`, or `unmatched`. Unmatched topics always
   have `matches: []` and `confidence: 0.0`.

Every match is a verbatim extracted section, not generated text:

- `passage` is exactly the stored `raw_text` for `section_index`.
- `passage_start` and `passage_end` are offsets into that exact string.
- `excerpt` is a verbatim, at-most-600-character window inside `passage`,
  chosen from the paragraph with the most matched terms. `excerpt_start` and
  `excerpt_end` are offsets into `passage`, so
  `passage[excerpt_start:excerpt_end] == excerpt`. Clients may show the excerpt
  for compact provenance while keeping the full passage available.
- `anchor` is copied unchanged from the document extraction (`pdf_page` with
  page/page_index, or `docx_section` with section_label/paragraph_index).
- `passage_id` is `<document_id>:<section_index>` and is the stable reference
  for later source-grounded output.

Warnings that may appear on a topic include `no_source_material`,
`topic_has_no_searchable_terms`, `no_strong_source_match`,
`ambiguous_source_match`, `multiple_source_matches`, and
`matches_truncated`.

### 2.10 `POST /api/v1/topic-sources/{source_id}/study-output`

Generate one of the three explicit Phase 3 study outputs from the same topic
segmentation and source matching used by `topic-review`. This endpoint does not
change the existing `/study-jobs` modes.

**Request**: `application/json`

```json
{
  "mode": "both",
  "topics": ["Calvin cycle", "Photosynthesis"],
  "ai_scope": "source_only"
}
```

| Field     | Type   | Required | Notes |
| --------- | ------ | -------- | ----- |
| `mode`    | string | yes      | `source_passages` (A), `ai_study_material` (B), or `both` (C) |
| `topics`  | array  | no       | Reviewed topics. If omitted, persisted reviewed topics are used; if none exist, the corrected transcript is segmented. |
| `ai_scope`| string | no       | `source_only` (default) or `source_and_beyond`. Applies only to B/C. |

**Mode semantics**

| Mode | Original passages | AI-generated material |
| ---- | ----------------- | --------------------- |
| `source_passages` | Yes, verbatim | No; AI is never called |
| `ai_study_material` | No (only basis references) | Yes |
| `both` | Yes, verbatim | Yes |

`source_passages` contains only stored `raw_text` values. Each passage keeps the
exact `passage_id`, `section_index`, `anchor`, and offsets from matching. No page
number or citation is generated by the AI.

`ai_scope: "source_only"` means the source-grounded study material is generated
from only the matched uploaded passages. `source_and_beyond` is an explicit user
opt-in: it adds a separate `beyond_source` block generated with a different prompt.
That block is marked `grounded_in_uploaded_material: false`, has no source
passages or citations, and is never mixed into `source_grounded`.

**200 Response**

```json
{
  "result_id": "b73e...91",
  "source_id": "8d21...c4",
  "document": {
    "document_id": "5f1c...e2",
    "filename": "lecture-03.pdf",
    "format": "pdf",
    "page_count": 42
  },
  "document_available": true,
  "source_material": {"status": "available", "section_count": 2},
  "mode": "both",
  "ai_scope": "source_only",
  "status": "completed",
  "topic_count": 1,
  "topics": [
    {
      "topic_id": "topic_1",
      "text": "Calvin cycle",
      "match_status": "matched",
      "material_status": "generated",
      "source_passages": [
        {
          "passage_id": "5f1c...e2:1",
          "section_index": 1,
          "anchor": {"kind": "pdf_page", "page": 2, "page_index": 1},
          "passage": "The Calvin cycle fixes carbon dioxide.",
          "passage_start": 0,
          "passage_end": 38,
          "excerpt": "The Calvin cycle fixes carbon dioxide.",
          "excerpt_start": 0,
          "excerpt_end": 38,
          "score": 1.0,
          "coverage": 1.0,
          "phrase_match": true,
          "matched_terms": ["calvin", "cycle"]
        }
      ],
      "study_material": {
        "generated": true,
        "source_grounded": {
          "text": "The Calvin cycle uses energy to fix carbon dioxide...",
          "basis_passage_ids": ["5f1c...e2:1"],
          "basis_refs": [
            {"kind": "pdf_page", "page": 2, "page_index": 1}
          ]
        },
        "beyond_source": null
      },
      "warnings": []
    }
  ],
  "ai": {
    "requested": true,
    "scope": "source_only",
    "configured": true,
    "generated_count": 1,
    "failed_count": 0,
    "skipped_count": 0,
    "beyond_generated_count": 0,
    "beyond_failed_count": 0
  },
  "warnings": [],
  "error": null,
  "created_at": "2026-10-23T10:10:00Z",
  "expires_at": "2026-10-23T11:10:00Z"
}
```

Every successful study-output response is persisted server-side and receives a
`result_id`. Persistence does not store another copy of the document text; the
response payload references the same passages. Results survive deletion of the
source and document and report `document_available: false` once the file is gone.
`created_at` and `expires_at` follow the configured retention window.

Segmentation warnings from the review step are included in `warnings` so callers
do not need a separate review call to learn about truncation or missing material.

**Topic and material states**

- `match_status`: `matched` | `ambiguous` | `unmatched`, copied from review.
- `material_status`: `not_requested` (A), `generated`, `skipped_ambiguous`,
  `skipped_unmatched`, `skipped_no_evidence`, or `ai_failed`.
- Ambiguous and unmatched topics never receive AI material. They remain visible
  with their warnings and, in modes A/C, their available passages.
- `study_material.source_grounded.text` is generated text, not source text.
  `basis_passage_ids` and `basis_refs` are copied from the exact matched passages
  used for that generated block.
- `study_material.beyond_source` is present only for the explicit
  `source_and_beyond` opt-in. It contains no citations and must not be presented
  as uploaded source material.
- `status`: `completed`, `partial`, or `failed`. A provider failure on some
  topics yields `partial`; if every eligible topic fails, the response is
  `failed` with `error`. Mode C still returns its original passages when AI fails.
- B/C with eligible matched topics return **503 `ai_not_configured`** if no AI
  provider is configured. A remains fully usable without AI. If there are no
  grounded topics, B/C return **200** with a `no_grounded_topics` warning and make
  no AI call.
- Unknown, other-user, or expired sources return **404
  `topic_source_not_found`**.

### 2.11 Topic study result history and export

Study outputs are saved per user. These endpoints make the saved material
reopenable, listable, deletable, and downloadable.

#### `GET /api/v1/topic-results?limit=20&offset=0`

Lists the caller's unexpired saved outputs, newest first. `limit` is 1-100.

**200 Response**: array of summary objects.

```json
[
  {
    "result_id": "b73e...91",
    "source_id": "8d21...c4",
    "document_id": "5f1c...e2",
    "filename": "lecture-03.pdf",
    "format": "pdf",
    "mode": "both",
    "ai_scope": "source_only",
    "status": "completed",
    "topic_count": 1,
    "created_at": "2026-10-23T10:10:00Z",
    "updated_at": "2026-10-23T10:10:00Z",
    "expires_at": "2026-10-23T11:10:00Z",
    "document_available": true
  }
]
```

#### `GET /api/v1/topic-results/{result_id}`

**200** with the same shape returned by `study-output`, including `result_id`,
`created_at`, and `expires_at`. `document_available` is recomputed at read time.
**404 `topic_result_not_found`** for an unknown, other-user, or expired result.

#### `DELETE /api/v1/topic-results/{result_id}`

**204 No Content**. Deletes the saved result immediately. **404
`topic_result_not_found`** for an unknown or other-user result.

#### `GET /api/v1/topic-results/{result_id}/export?format=pdf|docx`

Renders the saved payload with the same mode-aware exporter as study jobs.
`format` is required and must be `pdf` or `docx`. Returns the file with
`Content-Disposition: attachment` and the matching media type. **404
`topic_result_not_found`** for an unknown, other-user, or expired result;
**500 `export_failed`** if rendering fails; **422** for an invalid format.

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
| `document_unavailable`     | 409  | Attached source file is no longer available |
| `job_not_found`            | 404  | Unknown `job_id`                           |
| `topic_source_not_found`   | 404  | Unknown, other-user, or expired topic source |
| `images_required`          | 400  | No topic-note images supplied              |
| `too_many_images`          | 413  | Exceeds `TOPIC_MAX_IMAGES`                 |
| `image_too_large`          | 413  | Image bytes or pixel dimensions exceed limits |
| `images_too_large`         | 413  | Combined image bytes exceed `MAX_UPLOAD_MB` |
| `unsupported_image_type`   | 415  | Bytes are not PNG/JPEG                    |
| `empty_image`              | 400  | Zero-byte image                            |
| `corrupt_image`            | 400  | Image could not be decoded                 |
| `image_not_found`          | 400/404 | Transcript or stored image index does not exist |
| `topic_image_unavailable`  | 404  | Stored note image file is missing          |
| `topic_result_not_found`   | 404  | Unknown, other-user, or expired study result |
| `export_failed`            | 500  | Export rendering failed                    |
| `duplicate_transcript_index` | 400 | Transcript index repeated                  |
| `too_many_topics`          | 422  | Exceeds `TOPIC_MAX_TOPICS`                 |
| `invalid_topic`            | 422  | Empty topic after trimming                 |
| `topic_too_long`           | 422  | Exceeds `TOPIC_MAX_TOPIC_CHARS`            |
| `transcript_too_long`      | 422  | Exceeds `TOPIC_MAX_TRANSCRIPT_CHARS`       |
| `no_update`                | 400  | PATCH supplied neither topics nor transcripts |
| `topic_source_limit_reached` | 429 | Per-user active-source quota is full       |
| `topic_source_conflict`    | 409  | Concurrent update could not be merged      |
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
| `AI_PROVIDER_ORDER`         | `groq,gemini` | Provider fallback order; unconfigured providers are skipped |
| `GROQ_API_KEY`              | —       | Secret (server only)             |
| `GROQ_MODEL`                | `llama-3.3-70b-versatile` | Summary model |
| `GROQ_COMMAND_MODEL`        | `llama-3.3-70b-versatile` | Chat command model |
| `GROQ_BASE_URL`             | `https://api.groq.com/openai/v1` | OpenAI-compatible API base |
| `GROQ_REQUEST_TIMEOUT_S`    | 60      | Per-request timeout              |
| `GROQ_MAX_OUTPUT_TOKENS`    | 2048    | Output token cap                 |
| `MAX_UPLOAD_MB`             | 25      | Upload size cap                  |
| `MAX_DOCX_UNCOMPRESSED_MB`  | 100     | Decompressed DOCX size cap       |
| `MIN_PDF_CHARS_PER_PAGE`    | 15      | Non-whitespace chars per usable PDF page |
| `OCR_TESSERACT_PATH`        | `tesseract` | Tesseract executable          |
| `OCR_PAGE_LIMIT`            | 30      | OCR operations per document; `0` disables |
| `OCR_DPI`                   | 200     | PDF page render resolution       |
| `OCR_TIMEOUT_S`             | 30      | Per-page/image OCR timeout       |
| `OCR_LANGUAGE`              | `eng`   | Tesseract language data          |
| `TOPIC_MAX_IMAGES`          | 10      | Note images per topic source     |
| `TOPIC_MAX_IMAGE_MB`        | 10      | Per-image byte cap               |
| `TOPIC_MAX_IMAGE_PIXELS`    | 40000000 | Per-image decoded-pixel cap     |
| `TOPIC_MAX_TOPICS`          | 20      | Topics per source                |
| `TOPIC_MAX_TOPIC_CHARS`     | 200     | Characters per topic             |
| `TOPIC_MAX_TRANSCRIPT_CHARS`| 50000   | Characters per transcript        |
| `TOPIC_MAX_SOURCES_PER_USER`| 20      | Active topic sources per user; delete frees a slot |
| `TOPIC_OCR_MAX_CONCURRENCY` | 4       | Process-local topic-image OCR workers |
| `MAX_SECTION_CHARS`         | 6000    | Chunk size                       |
| `MAX_SUMMARY_CHUNK_CHARS`   | 4000    | Max source chars sent to the AI per request |
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
