# Security and error handling

## Verified security boundaries

### Identity and ownership

- `backend/app/main.py:_requires_user_identity` makes `/api/v1/*` data routes require a per-install Bearer token; `/api/v1/users` registration is the exception. `/health` is public.
- `UserStore.create()` (`backend/app/services/users.py`) generates a high-entropy opaque URL-safe token, returns it to the caller, and persists only SHA-256 digest. `resolve()` hashes received token and queries `users` by unique hash. The app access token is not treated as a user identity.
- `mobile/services/apiClient.ts:ensureUserToken()` stores the opaque token with Expo SecureStore, with a memory-only fallback. Requests attach bearer authorization. The optional shared `EXPO_PUBLIC_API_TOKEN`, if present, is compiled into the app and is explicitly described in code as only a mild outer gate; it is not a secret boundary.
- `DocumentStore`, `JobStore`, and `TopicSourceStore` expose owner-scoped access methods; API handlers pass `request.state.user_id`. The document delete handler and source/job lookups hide non-owned IDs as not found. Isolation tests are present in backend tests.
- Existing source data is not served by arbitrary filesystem paths. Upload code stores server-generated identifiers and API responses do not expose document paths. Topic response omits image file path and bytes.

### Input and file safety

- Document uploads are read with an explicit max-plus-one bound and checked by extension and file magic in `document_parser.py`; legacy `.doc` is rejected. DOCX ZIP uncompressed size is bounded.
- Topic images are restricted to PNG/JPEG by actual bytes, checked for dimensions/pixels before decode and after PyMuPDF decode, bounded by bytes/image count/total request limits, and assigned safe basename filenames.
- Tesseract calls have item timeout and configured concurrency limits. PDF OCR page count, DPI and per-call timeout are configurable.
- User-facing error messages generally avoid raw parser/provider internals. Unexpected server exceptions become generic `processing_failed` responses. Export errors log request ID, format, exception type and timing rather than exception message or source content.
- Docker uses one worker for in-process job assumptions and disables Uvicorn access logs to avoid query-token leakage if query tokens are used.

### AI data handling

Gemini API key is read on backend. `ai_client.py` uses distinct grounded and beyond-source system prompts. Normal topic AI path only supplies matched passages. Beyond-source is a separate request and response field gated by `ai_scope=source_and_beyond` in the API schema. The mobile UI sends source-only behavior by default and exposes no toggle. Uploaded text is sent to the external AI provider for AI modes; the README warns users about provider/free-tier data terms. This data transfer is implemented; actual provider policy/runtime was not tested here.

## Error and recovery paths

| Failure | Backend behavior | Frontend behavior / recovery |
|---|---|---|
| Invalid/oversized/unsupported upload | `ApiError` status/code; document parser checks extension, size and signatures; FastAPI validation errors are 422 envelope. | Upload shows server message and retry for request errors. Oversize is also client-checked from health limit. |
| Corrupt/encrypted/no-text document | Parser produces safe client error or `no_text` extraction status and warnings; no study job can start without extractable sections. | Upload displays warnings/status; continue button only appears for extractable source. |
| OCR unavailable, timeout, failure, empty text | Typed exceptions become extraction warnings/status; topic OCR response records per-image status and editable empty transcript. | Photo Notes shows warnings and allows manual transcript entry. No OCR means user can type instead. |
| Gemini not configured | Study API returns `ai_not_configured` before job start; topic study maps it to API error. | Legacy Results displays API error. Photo Results offers passage-only fallback. |
| Gemini quota, timeout, provider/malformed response | AI client maps to rate-limit/timeout/provider/invalid-response classes; legacy processing can be partial/failed; topic generation can preserve successes and mark topic failure. | Results shows warnings/partial status; Photo Results retries or switches to passages. |
| Network/HTTP timeout | `apiClient.ts:send()` uses per-operation timeout and converts abort/network failure to `ApiError`; upload logs timing by safe request IDs. | Screens display message; upload, source retrieval, topic output, export have retry paths. Legacy polling stops after a fetch error and displays it. |
| Auth failure | Middleware returns 401 with `invalid_user_token` or `unauthorized` envelope/challenge. | Client can forget/re-register after applicable auth response; no user-facing login/account recovery. |
| Database/storage exception | Route exception handler emits generic 500 `processing_failed`; endpoint-specific failures may be caught and translated. | Client sees generic/specific error; no offline persistence or queued request synchronization. |
| Export render/download/share failure | Export route returns `export_failed`; missing/not-ready are 404/409. Mobile file downloader maps statuses; Share availability and failure are handled separately. | Results displays export or share error; user can retry. A downloaded file can exist even if share fails. |
| Process interruption | Large job is `asyncio.create_task` in current process; DB may retain queued/running status but no durable worker resumes it after restart. | Results may continue polling until network/server responds, then error. No stale job recovery is evident. |

## Verified issues versus concerns

### Confirmed from configuration/code

1. **Ephemeral deployment persistence:** `render.yaml` sets DB and upload paths in `/tmp` and comments say no persistent disk. This is a real configured durability limitation, not a hypothetical vulnerability.
2. **Topic results persistence/export (resolved):** outputs are now persisted in `topic_study_results` (schema v6) and exposed through owner-scoped list/get/delete/export routes. Remaining risk to verify: durable deployment storage, since results still expire and disappear on an ephemeral filesystem/database reset.
3. **No human identity/recovery:** identity is an anonymous install token; there is no account linking, password reset, device transfer, or revocation interface.
4. **Production EAS config gaps:** preview sets server URL and React Native fetch override, while production does not in checked-in `eas.json`. Release connectivity/multipart behavior is therefore unverified and likely requires EAS environment configuration.
5. **Registration is public and visibly has no endpoint-specific throttle:** every new registration inserts a user row. The generic `rate_limit_per_minute` exists in config but no rate-limit middleware was found in `main.py` or routes. This supports an abuse/DB growth concern for a public deployment; no exploit was executed.
6. **Secrets and content scope:** a compiled shared app token is extractable from a client bundle; source document text is intentionally transmitted to Gemini for AI modes. Both are described by repository comments/docs; treat accordingly.

### Not established as vulnerabilities

- No cross-user data leak was demonstrated; the stores/routes consistently use owner-scoped access and tests cover isolation. A full independent adversarial penetration test was not performed.
- No file-path traversal was demonstrated; filenames are sanitized and stored paths are server generated. Parsing libraries and image decoders remain attack surface despite format/size constraints.
- No live TLS, deployed CORS, reverse-proxy or secret-manager configuration was inspected. Preview URL uses HTTPS, but runtime deployment settings were not queried.
- No actual database corruption or race condition was observed. SQLite writes are serialized and topic update has version conflict handling; scale beyond the single-worker design is not established.

## Resource and privacy boundaries

- Global limits include upload MB, DOCX uncompressed MB, OCR pages/DPI/time, per-image MB/pixels, max topic count/chars, transcript/jottings lengths, per-user source count and Gemini token/time/retry limits. See `backend/app/config.py` and `.env.example`.
- File retention defaults to seven days. User identity rows have no retention cleanup apparent in code.
- Raw document text, OCR transcript, job output and topic transcripts are stored in SQLite JSON/text. These are sensitive study materials even if image/document bytes expire separately.
- App access token can also be accepted as a query parameter on gated routes; query strings can leak through non-container proxies/logging. The configured Docker command disables Uvicorn access logs; production proxy behavior is unverified. Mobile export currently uses headers.
- `CORS_ORIGINS` is configurable, defaults are local development origins. Native mobile requests do not rely on browser CORS enforcement; a future web client deployment needs correct origins.

## Test evidence

Backend test modules cover access control, ownership isolation, user registration, old schema migration, upload parsing/OCR, AI failures, export and topic workflows. Mobile tests cover the request layer and pure topic helper behavior. In the preceding audit, 200 backend and 19 mobile tests passed, with typecheck/lint passing. The Android bundle attempt failed at local Hermes executable permissions; no physical-device or deployed security test was completed.
