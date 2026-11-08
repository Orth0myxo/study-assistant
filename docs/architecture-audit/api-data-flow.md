# API inventory and data flow

## Conventions and authentication

Base paths are literal FastAPI routes (no version prefix on health; API routes use `/api/v1`). JSON requests/responses use Pydantic schemas where declared. Errors generally use `{ "error": { "code", "message", "request_id", "details? } }` via `backend/app/errors.py`. Request IDs are accepted only if they match the safe 1–64 character pattern; otherwise the app generates one. Document upload returns `Server-Timing`.

All `/api/v1/*` data routes require a valid per-install `Authorization: Bearer <token>`. `POST /api/v1/users` is exempt and returns a token. `/health` is public. If `APP_ACCESS_TOKEN` is configured, it is an additional outer gate for requests without a valid bearer (registration/health exempt); a supplied `X-App-Token` or query token must match. The mobile client normally sends app token in a header if compiled/configured and obtains user bearer via `ensureUserToken()`.

## Endpoint inventory

| Method and route | Auth | Request → response | Handler and work | Persistence/files | Mobile caller |
|---|---|---|---|---|---|
| `GET /health` | Public | No body → `HealthResponse` (`status`, `version`, AI configured/model/reachable, limits) | `api/health.py`; reads settings, does not prove provider reachability | None | `apiClient.getHealth()` from Home and Upload |
| `POST /api/v1/users` | Exempt | No body → `UserRegistrationResponse {user_id, token}`; 201 | `api/users.py:register_user` -> `UserStore.create`; token is random and response-only | Insert `users` with token SHA-256 hash and timestamp | `apiClient.registerUserToken()` invoked lazily by `ensureUserToken()` |
| `POST /api/v1/documents` | Bearer | Multipart `file`; → `DocumentResponse` with document ID, metadata, extraction status/counts/warnings; 201 | `api/documents.py:upload_document` bounds read, `document_parser.parse_document`, constructs stored sections | Writes original bytes under configured temp root and parsed JSON/metadata to `documents` | `apiClient.uploadDocument()` from Upload; Photo Notes may upload pending source first |
| `DELETE /api/v1/documents/{document_id}` | Bearer | No body → 204 | `api/documents.py:delete_document`; owner lookup then delete | Deletes source file and document record; completed study jobs remain | `apiClient.deleteDocument()` exists; no normal screen action found |
| `POST /api/v1/study-jobs` | Bearer | JSON `{document_id, mode}`; `StudyJobResponse` inline (200) or queued payload (202) | `api/study.py:create_study_job`; validates owned/available/extractable doc; `process_document`; large case starts in-process task | Creates/updates `study_jobs` JSON payload | `apiClient.startStudyJob()` from Results |
| `GET /api/v1/study-jobs?limit=&offset=` | Bearer | Query limit 1–100, offset ≥0 → array `StudyJobSummary` | `api/study.py:list_study_jobs`; filters owner and recomputes document availability | Reads `study_jobs` and `documents` | `apiClient.listStudyJobs()` from Home |
| `GET /api/v1/study-jobs/{job_id}` | Bearer | No body → full job payload/status/progress/items | `api/study.py:get_study_job`; checks owner and expiry | Reads job and document availability | `apiClient.getStudyJob()` from Results, including polling/reopen |
| `GET /api/v1/study-jobs/{job_id}/export?format=pdf\|docx` | Bearer | Required format enum → binary with content type/disposition | `api/study.py:export_study_job`; requires completed/partial; `exporter.render()` | Reads persisted job payload; creates export bytes in memory; no export file persistence | `apiClient.downloadStudyExport()` from Results, then Expo Sharing |
| `POST /api/v1/topic-sources` | Bearer | Multipart optional `document_id`, `jottings_text`, repeated `images`; → `TopicSourceResponse`; 201 | `api/topic_sources.py:create_topic_source`; validates linked doc ownership, size/count/image, `validate_image`, threaded OCR | Saves topic metadata/transcripts in topic tables and image bytes under `TEMP_DIR/topic_sources/{source_id}` | `apiClient.createTopicSource()` from Photo Notes |
| `GET /api/v1/topic-sources/{source_id}` | Bearer | No body → source document/availability, jottings/topics, image metadata/transcripts/warnings | `get_topic_source` -> owner-scoped source store and linked document lookup | Reads topic tables and document store; does not stream image bytes | `apiClient.getTopicSource()` from Photo Notes and Photo Topics |
| `PATCH /api/v1/topic-sources/{source_id}` | Bearer | JSON subset of `topics`, `jottings_text`, `transcripts[{index,text}]` → updated source | `update_topic_source`; validates length/duplicates; `update_for_owner` applies optimistic versioning and resets stale topics when text changes | Updates topic and image transcript rows; does not rewrite original photo bytes | `apiClient.updateTopicSource()` from Photo Notes and Photo Topics |
| `DELETE /api/v1/topic-sources/{source_id}` | Bearer | No body → 204 | `delete_topic_source`; owner-scoped | Deletes source/image rows and image directory, not linked document | `apiClient.deleteTopicSource()` exists; no normal screen action found |
| `POST /api/v1/topic-sources/{source_id}/topic-review` | Bearer | Optional JSON `{topics?: [...]}` → `TopicReviewResponse` with source status, segmentation, topics, status/confidence/labels/matches | `review_topic_source` -> `_build_topic_review`, segmenter or provided topics, `_labels_for_topic`, `match_topics` | Reads topic source and stored document sections; no result persistence | `apiClient.reviewTopicSource()` from Photo Topics |
| `POST /api/v1/topic-sources/{source_id}/study-output` | Bearer | JSON `{mode, topics?, ai_scope?}` → `TopicStudyOutputResponse`, with per-topic passages/material/status and AI counters | `create_topic_study_output` rebuilds topic review, `build_study_output`, invokes Gemini for eligible AI topics | Reads topic/document state; **does not save returned output** | `apiClient.createTopicStudyOutput()` from Photo Results |

`TopicSourceResponse` deliberately does not include local image file path or image bytes; `TopicSourceImage` contains metadata, OCR status, transcript provenance/text and warnings. Therefore the client can restore transcript editing but cannot display the original persisted photo from this API response.

## Legacy document-study flow trace

1. Upload UI calls `uploadDocument()`. The client uses `FormData`; the native-fetch override is configured to work around Expo SDK 57 multipart behavior (`mobile/.env.example`, `mobile/eas.json`). Server parses and persists before responding with extraction summary.
2. `topics.tsx` selects mode; Results calls `startStudyJob(documentId, mode)`. Its UI promise cache avoids duplicate starts for the same document/mode in one screen instance.
3. The server gets source sections by owner. For small documents, processing completes within POST; for larger docs, POST returns 202 and job status is polled by `GET` every two seconds.
4. JobStore writes full output to SQLite. Home history and Results reopen by owner-scoped job ID.
5. Export API renders from saved result payload, so deleting the original document does not remove job text/output; the source availability field changes to false.

## Photo/topic flow trace

1. Upload may first create a document record. Photo Notes then submits this ID plus note photos and typed jottings.
2. Backend stores source photo bytes and editable OCR text; mobile response hydrates editable fields. User edits are PATCHed.
3. Topic review POST either segments source transcript/jottings or accepts the edited topics. It labels input sources by normalized substring presence and independently matches terms against document sections.
4. Topic-study POST re-runs review/matching from current source state (it does not accept or trust a prior review response ID), then returns source passages and optional generated study material.
5. Mobile holds the returned topic output only in screen state. Refreshing/revisiting Photo Results triggers another POST and may incur new AI calls; there is no saved topic result/history/export API.

## Request/response shapes and status semantics

- Document extraction statuses: `ok`, `partial`, `no_text`; warnings explain OCR/extraction gaps. `DocumentResponse` has counts, not extracted body text.
- Study job statuses: `queued`, `running`, `completed`, `partial`, `failed`; progress uses processed/total units. Job schema is in `schemas/study.py`.
- Topic source statuses: `ok`, `partial`, `no_text`; transcript status distinguishes OCR or manual editing.
- Topic matching statuses: `matched`, `ambiguous`, `unmatched`; output materials can be generated, not requested, skipped or AI-failed. Topic schemas are in `schemas/topic_sources.py`.
- Export errors distinguish missing job (404), not-ready job (409), invalid format (422), and renderer failure (500).

## Retries, idempotency and duplicate work

`apiClient.ts` has request timeouts, stable error mapping, and in-flight promise sharing for start-study and relevant topic calls. This only deduplicates concurrent/same-key requests within a running client; it is not server-side idempotency. Revisiting Photo Results initiates topic study again. The legacy results screen preserves job ID in route parameters to avoid restarting a previously created job after remount. Topic output does not have a job ID.

API endpoints with no regular screen caller: document delete and topic-source delete are exposed by the client but no standard screen control invokes them. There is no endpoint for topic image download, topic result history, or topic result export. No evidence of a duplicate document parse request in the normal upload flow; the photo flow uploads once when the pending document is submitted, then creates the topic source.

## Error mapping

FastAPI `ApiError` and validation handlers return a common envelope. `apiClient.ts:toApiError()` parses it into `ApiError`; `send()` handles network/abort failures and auth token rejection/re-registration logic. Screens typically display `ApiError.message`. Specific recovery is workflow dependent: upload retry, topic-source reload retry, AI-to-source-passage fallback, Results polling/export retry. Interrupted server process can lose in-flight background task even though job status was persisted as queued/running; no startup repair routine for stale active status is evident.
