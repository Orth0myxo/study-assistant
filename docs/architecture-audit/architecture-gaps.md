# Architecture gaps and documentation discrepancies

This list separates direct source observations from behaviors that require runtime verification. It is intended as traceability, not a redesign proposal.

## Confirmed implementation gaps

| Finding | Evidence | Practical consequence |
|---|---|---|
| Topic/photo output is not part of study history and is not exportable. | No topic result/job table or export route in `backend/app/api/topic_sources.py`; `mobile/app/photo-results.tsx` says results are not saved study jobs and PDF/DOCX export is unavailable. Home lists only `study_jobs`. | A result exists only in screen memory/route context; there is no saved-result reopen/export path. Reopening Photo Results calls generation again. |
| Original uploaded note photos cannot be reviewed visually after server submission/reload. | `TopicSourceImage` response schema contains metadata and transcript, no URI/bytes; `_response()` omits filesystem path; `photo-notes.tsx` post-create branch renders file name/transcript but no remote image. | Student can edit OCR text but cannot compare the saved transcript against the stored original through this API/UI. |
| Beyond-source opt-in exists only at API level. | `TopicStudyRequest.ai_scope` supports `source_and_beyond`; `apiClient.createTopicStudyOutput()` has scope argument/default, while Photo Topics exposes only passage/AI/both mode and Photo Results calls default. | App users cannot deliberately enable the backend's separate extra-background output through current screens. |
| Free Render config is not durable. | `render.yaml` sets `DATABASE_PATH=/tmp/study.db` and `TEMP_DIR=/tmp/uploads`, with explicit comment about loss on restart/redeploy. | Users lose identities, docs, jobs, topic records and files when the ephemeral disk resets. |
| Production EAS profile lacks preview environment values. | `mobile/eas.json`: preview sets `EXPO_PUBLIC_USE_RN_FETCH` and `EXPO_PUBLIC_API_URL`; production only selects app bundle. | Production standalone API resolution falls back to runtime host/emulator logic unless EAS environment provides missing variables; production multipart upload behavior is not established. |
| Anonymous account registration has no apparent endpoint-specific throttle. | `api/users.py:register_user()` unconditionally creates a new identity; `main.py` has no rate-limit middleware. `rate_limit_per_minute` is a setting but not applied in inspected routes. | Public deployment can accumulate anonymous user rows; abuse impact/rate was not measured. |
| Background jobs are process-local. | `api/study.py:_run_background()` uses `asyncio.create_task`; Docker requires one worker; no durable task queue/recovery logic. | Restart during a long job can leave persisted job in queued/running without its task. |
| Stored source records have no user-facing source library. | Home calls only `listStudyJobs()`; topic sources have get/delete by known ID but no list endpoint and no Home screen integration. | Topic records can be reopened only with known source ID/route, not discovered from history. |
| Delete APIs are disconnected from normal UI. | `deleteDocument` and `deleteTopicSource` client wrappers exist; no standard screen button calls them. | Cleanup depends on TTL unless another caller is added; user cannot explicitly delete through current app screens. |

## Documentation vs implementation

| Existing document claim/shape | Current source evidence | Audit interpretation |
|---|---|---|
| `README.md` says document and job SQLite data survives backend restarts. | True only if `DATABASE_PATH` and source `TEMP_DIR` point to durable persistent storage. Current Render config points both to ephemeral `/tmp`. | Qualify persistence claim by deployment volume; current Render free deployment does not meet it. |
| `docs/implementation-plan.md` calls status “Stage 4 implemented” and topic work “implemented, pending review”; cites 191 backend tests and says mobile integration/device validation are outside extension. | Photo screens, service, helper tests are now present; preceding audit ran 200 backend tests and 19 mobile tests. | Plan status/test count is stale relative to current source. It still correctly records device validation as unverified. |
| `README.md` / architecture list broad photo/topic actions and optional beyond-source behavior. | Mobile does not display stored remote photos; no scope toggle; topic output does not export or persist. | API capability and UI/product capability should not be treated as equivalent. |
| `docs/data-model.md` describes durable SQLite store. | Storage backend is configurable; deployment explicitly uses `/tmp`. | Data model correctly explains mechanism but not actual deployment persistence guarantee. |
| `README.md` describes Tesseract as optional. | Docker installs Tesseract and English data; local runtime can lack executable and return warning. | Optionality depends on host; Docker config includes it. |
| `docs/architecture.md` main screen table is now more complete than README file tree. | Current tree includes `/photo-notes`, `/photo-topics`, `/photo-results`, `topicWorkflow.ts`, photo tests and topic backend modules; README repository tree still largely shows original structure. | README tree is incomplete and should not be used as a full inventory. |

## Dependency usage observations

The mobile manifest contains Expo modules that are not directly imported by the inspected app source (`@expo/ui`, `expo-device`, `expo-font`, `expo-glass-effect`, `expo-linking`, `expo-symbols`, `expo-web-browser`). Some may be Expo Router/runtime/plugin dependencies or reserved for platform integrations, and `react-dom` is relevant to the configured web target. This source scan only identifies candidates for dependency-use verification; it does not prove they are safe to remove. The directly evident app imports include `expo-constants`, `expo-file-system`, `expo-secure-store`, `expo-document-picker`, `expo-image-picker`, `expo-image`, `expo-sharing` and `expo-status-bar`.

## Disconnected or duplicate interfaces

- `DELETE /api/v1/documents/{id}` and `DELETE /api/v1/topic-sources/{id}` plus client wrappers have no normal user-facing invocation. Backend retention sweep is the routine cleanup path.
- `/api/v1/topic-sources/{id}` supports restore by ID but there is no endpoint to list a user's topic sources and no link in Home history.
- Topic review is run on Photo Topics load and again after edits; topic study output rebuilds review/matches again rather than consuming an immutable review token. This keeps backend output based on current stored input, but can repeat deterministic matching computation.
- Topic source/study/review functions use in-flight maps in the client to coalesce concurrent identical requests. This is client process optimization only; calls after the map entry clears or across devices are separate requests.
- AI completion for the legacy study flow and topic flow share `GeminiClient` but have different result persistence/job behavior. Legacy jobs are persisted/polled; topic AI is synchronous/transient.

## Unverified behavior and unavailable evidence

- No physical Android session was observed; picker permission prompts, image capture, SecureStore behavior, native multipart fetch, share sheet and back navigation are not independently confirmed.
- Local Android export did not complete: Windows denied permission to execute `hermesc.exe`. It is not evidence that an EAS remote build will fail or succeed.
- No Docker image build, live Render `/health`, Render restart persistence test, real document upload, Tesseract binary check in target container, or Gemini request was run in this documentation task.
- Existing tests use generated fixtures and injected OCR/AI behavior. Their quality does not establish accuracy on representative lecture materials, handwriting, scanned PDFs or real-world topic ambiguity.
- No version control metadata dates the last uncommitted edits. The best stopping-point inference is documented in `system-overview.md`; source alone cannot reveal which developer action was last.
- Potential runtime constraints such as request body limits in upstream proxy, provider quotas, app store configuration, EAS project credentials and Android target SDK compliance require actual build/deployment inspection.

## Architectural boundaries to preserve during future evaluation

- There are two user-facing flows with materially different persistence and export behavior.
- Topic labels (which note the topic came from) and document passage matches (which ground results) are different data relationships.
- Deterministic lexical matching is not AI-based topic inference; explicit ambiguous/unmatched statuses are part of the current contract.
- Only existing study jobs have the current export/share pipeline.
- Per-install identity is not a recoverable account system; ownership relies on possession of the bearer token.
- Deployment storage is part of application persistence semantics, not a transparent infrastructure detail.
