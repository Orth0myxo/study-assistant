# Code responsibility index

Ranges identify important regions/functions, not exact semantic boundaries for every statement. File paths are relative to the repository root. The main entry/function lines were obtained from the current source tree; screen bodies/styles are large, so function start and key action references are emphasized.

## Mobile

| File | Responsibility and important exports/functions | Used by / calls |
|---|---|---|
| `mobile/app/_layout.tsx:4-29` | `RootLayout()` registers the Expo Router stack and screen titles for all seven route files. | Expo Router entry; route files. |
| `mobile/app/index.tsx:26-105` | `fetchStatus()`, `HomeScreen()`, `loadRecent()`, focus effect, route actions. Home status and study-job history. | Calls `getHealth`, `listStudyJobs`; routes to Upload, Photo Notes and Results. |
| `mobile/app/index.tsx:108-164` | `RecentList()` and `StatusLine()` present loading/empty/error/healthy/degraded state. | `HomeScreen`. |
| `mobile/app/upload.tsx:27-211` | `formatSize()`, `UploadScreen()`, `uploadAsset()`, `pickAndUpload()`, `ResultCard()`. Picker, client size check, upload, retry and flow-dependent routing. | Calls `getHealth`, `uploadDocument`; routes to Topics or Photo Notes. |
| `mobile/app/topics.tsx:27-80` | `TopicsScreen()` holds selected legacy mode and routes with document ID/mode. | Upload -> Results. Job creation is deferred to Results. |
| `mobile/app/results.tsx:34-81` | `formatRef()`, status predicates, `SummaryText()` presentation helpers. | Results component. |
| `mobile/app/results.tsx:83-240+` | `ResultsScreen()` state/effects: start promise cache, `startStudyJob`, `getStudyJob` polling, route `jobId`, export download and Expo Sharing. | Topics or Home-history route; calls study/export API client. |
| `mobile/app/photo-notes.tsx:38-62` | `assetToUpload()` and `formatSize()` adapt Expo picker assets to multipart DTO. | Photo Notes. |
| `mobile/app/photo-notes.tsx:64-360+` | `PhotoNotesScreen()` manages source/document/jottings/photos/transcripts/busy/error; picker/camera; `createJottings()` upload-and-create; `continueToTopics()` saves edits and routes. | Home/Upload entry; calls upload, create/get/update topic-source client functions. |
| `mobile/app/photo-topics.tsx:61-65` | `preview()` trims and bounds passage preview text. | Topic review cards. |
| `mobile/app/photo-topics.tsx:68-182` | `PhotoTopicsScreen()` loads source and automatic review; topic draft editing; `runReview()` saves topics and fetches review; `generate()` routes to results. | Photo Notes; calls source get/update/review endpoints. |
| `mobile/app/photo-results.tsx:26-65` | Mode/status mapping and `FormattedText()` helper. | Photo Results. |
| `mobile/app/photo-results.tsx:67-370+` | `PhotoResultsScreen()` reads source/mode parameters, runs topic output request, supports retry/source-passages fallback and displays passage/AI output. | Topic Topics screen; calls topic study-output API. No result persistence/export. |
| `mobile/components/screen.tsx:5-15` | `Screen()` common safe-area and scroll container. | All screens. |
| `mobile/components/primary-button.tsx:9-27` | `PrimaryButton()` and `Card()` reusable UI primitives. | All screens. |
| `mobile/services/apiClient.ts:41-136` | SecureStore token read/write/delete, `registerUserToken()`, `ensureUserToken()`. | Every authenticated request through `send()`. |
| `mobile/services/apiClient.ts:138-259` | Request IDs, timing parse/log helpers, base URL selection, `ApiError`, upload DTO and abort detection. | API client internals; Home can use `getBaseUrl`/`setBaseUrl` where needed. |
| `mobile/services/apiClient.ts:261-357` | `toApiError()`, `send()`, `request()`, `requestJson()` manage auth headers, fetch selection, timeout and response decoding. | All exported request wrappers. |
| `mobile/services/apiClient.ts:358-431` | `getHealth`, `uploadDocument`, `deleteDocument`. | Home, Upload, Photo Notes; delete wrapper has no standard screen caller. |
| `mobile/services/apiClient.ts:432-618` | Topic source multipart creation, get/patch/review/output/delete plus in-flight promise maps. | Photo Notes, Photo Topics, Photo Results. Delete wrapper has no standard screen caller. |
| `mobile/services/apiClient.ts:620-778` | Study-job start deduplication, get/list, export URL/error mapping and `downloadStudyExport()`. | Results and Home. |
| `mobile/services/topicWorkflow.ts:9-129` | Client-side limits; jotting/topic validators; topic cleaning, source/reference/status labels, mode eligibility and review counts. | Photo Notes, Photo Topics, Photo Results. |
| `mobile/types/api.ts:1-294` | Typed health/document/job/export/topic DTOs and enums mirroring server response contracts. | API client and screen components. |
| `mobile/tests/apiClient.test.ts` | Mocks network/secure-store behavior and tests client request paths/errors. | Vitest. |
| `mobile/tests/topicWorkflow.test.ts` | Tests pure topic helper/validation behavior. | Vitest. |
| `mobile/vitest.config.mts` | Vitest setup and alias/test environment configuration. | `npm test`. |
| `mobile/app.json` | Expo app metadata, Android package, Router/SecureStore/Sharing/ImagePicker plugins and media permissions. | Expo config/build. |
| `mobile/eas.json` | EAS preview APK config (API URL and RN fetch env) and production app-bundle profile. | EAS CLI; production profile lacks the preview-specific API/fetch variables. |
| `mobile/package.json`, lockfile | Expo/React Native runtime dependencies and start/lint/typecheck/test scripts. | npm install and Expo CLI. |

## Backend app, routes and schemas

| File | Responsibility and functions | Uses/called by |
|---|---|---|
| `backend/app/main.py:39-71` | `_cleanup_loop()` and `lifespan()` configure DB/stores, hydrate/reconcile/sweep, set OCR concurrency and run periodic cleanup. | Uvicorn/FastAPI lifespan. |
| `backend/app/main.py:79-169` | `_requires_user_identity()`, `require_access_token()`, CORS and exception handler registration. | Every request. |
| `backend/app/main.py:173-197` | Router registration, `add_request_id()`, root service response. | FastAPI. |
| `backend/app/config.py:6-80` | `Settings`, derived `ai_configured`/CORS list, cached `get_settings()`. | All app/services. |
| `backend/app/errors.py:11-80` | `ApiError`, `error_payload`, handlers for API, validation and unhandled errors. | Route/service exceptions. |
| `backend/app/api/health.py:9-` | `health()` returns configuration and limits. | Public client health calls. |
| `backend/app/api/users.py:11-` | `register_user()` issues anonymous identity. | Client bootstrap. |
| `backend/app/api/documents.py:21-170` | `upload_document()` bounds input, parses/stores, returns extraction/timing; `delete_document()` owner-scoped deletion. | Mobile upload/delete wrappers. |
| `backend/app/api/study.py:29-285` | Job payload helpers; `_run_background()`; create, list, get and export endpoints. | Legacy study UI/client. |
| `backend/app/api/topic_sources.py:51-710+` | Response mapping; create/get/patch/delete source; `_input_sources`, labels, segmentation/review builder; topic review and study-output endpoints. | Photo workflow client. |
| `backend/app/schemas/health.py` | Health/AI/limits response DTOs. | Health API. |
| `backend/app/schemas/users.py` | Registration response DTO. | User API. |
| `backend/app/schemas/documents.py` | Warnings, extraction info and document response. | Document API/mobile DTO match. |
| `backend/app/schemas/study.py` | Job request, status, item, progress, summary and export format contracts. | Study API. |
| `backend/app/schemas/topic_sources.py` | Topic source, images, update/review, passage match, study request/output DTOs. | Topic API. |

## Backend services

| File | Responsibility and important functions | Dependencies / consumers |
|---|---|---|
| `backend/app/services/db.py:14-103` | V1–V3 DDL and table/index definitions. | `Database.configure()`. |
| `backend/app/services/db.py:107-278` | `Database`; connection setup, migration v1–v5, transactions, execute/query. | All stores. |
| `backend/app/services/storage.py:16-310` | `StoredDocument`, `DocumentStore`: save, owner lookup/delete, sweep, load/reconcile, JSON row conversion. | Document and study routes, topic review. |
| `backend/app/services/job_store.py:22-260+` | `JobStore`: put/get/list owner-scoped, load, persist JSON fields, sweep/expiry. | Study route and Home history. |
| `backend/app/services/users.py:12-60` | `hash_token`, `UserStore.create/resolve/clear`. | Auth middleware and registration endpoint. |
| `backend/app/services/document_parser.py:39-450+` | Parsed structures; `parse_document`, PDF/DOCX extraction, DOCX ZIP checks, page/image OCR. | Upload handler; `ocr` and PyMuPDF/python-docx. |
| `backend/app/services/ocr.py:12-` | Typed OCR exceptions and `run_tesseract()`. | Document parser and topic image OCR. |
| `backend/app/services/study_processor.py:22-` | Study outcomes; chunk/unit splitting, source refs, AI map/reduce and statuses. | Study API. |
| `backend/app/services/ai_client.py:108-` | Response normalization, typed provider errors, Gemini protocol/client, prompt selection/retries/parsing/client factory. | Study processor/topic study. |
| `backend/app/services/exporter.py:54-` | Mode-aware blocks, Unicode PDF writer, DOCX rendering, file naming/format dispatcher. | Study export endpoint. |
| `backend/app/services/topic_images.py:52-` | OCR concurrency config, PNG/JPEG validation, safe filename, Tesseract call and transcript warnings. | Topic source creation endpoint. |
| `backend/app/services/topic_segmentation.py:42-` | `segment_transcript()` and `prepare_provided_topics()`. | Topic review builder. |
| `backend/app/services/topic_matching.py:44-` | Match DTOs, normalization/content terms, `_matches_for_topic()` and `match_topics()`. | Topic review builder. |
| `backend/app/services/topic_study.py:26-` | Topic output DTOs, source text/ref construction, `_skipped_status()`, `build_study_output()`. | Topic study-output endpoint. |
| `backend/app/services/topic_sources.py:17-631` | Topic record dataclasses and `TopicSourceStore`: save/get/update/delete/load/sweep, optimistic concurrency and SQLite/filesystem persistence. | Topic source API. |

## Configuration, packaging and tests

- `backend/requirements.txt`: FastAPI/Uvicorn/Pydantic settings, multipart, HTTPX, PyMuPDF and python-docx. `requirements-dev.txt` adds pytest and pytest-asyncio. `pyproject.toml` declares Python >=3.11 and pytest paths; it does not duplicate all runtime dependencies.
- `backend/Dockerfile`: Python 3.12 slim, installs Tesseract English, Python requirements and app source; checks vendored fonts; configures temp defaults; runs a single Uvicorn worker with access logs disabled.
- `render.yaml`: free Render Docker web service, `/health` check, `/tmp` database/upload paths, optional shared app gate. Free ephemeral disk means persisted records/files are not durable across restart.
- `mobile/package.json`: Expo SDK 57, React Native 0.86, React 19, Expo Router, Expo picker/filesystem/sharing/SecureStore; scripts are `start`, platform start commands, `lint`, `typecheck`, `test`.
- `mobile/tests` covers API client and topic helpers; backend `tests/` covers health, upload/parser, OCR, study/AI, export, persistence, access/isolation and topic segmentation/matching/source/study. Automated tests use fixtures/stubs for external systems; they are not live service or device verification.
- `backend/app/assets/fonts/` contains DejaVu font files and license for PDF export.

## Documentation and assets

- `README.md`: onboarding, endpoints, environment, integration notes and current claims.
- `docs/architecture.md`, `api-contract.md`, `data-model.md`, `implementation-plan.md`: design/contracts/history. Their claims should be checked against source; current plan status/count and Render durability wording need qualification.
- `mobile/assets/images/` are app icon/splash/favicons; `assets/expo.icon/` is Expo icon metadata/art. No runtime-generated topic illustration or generated image feature is present.
- `backend/app/__init__.py`, `api/__init__.py`, `schemas/__init__.py`, `services/__init__.py` are package markers; no independent business behavior.
