# Groq command step, range extraction and summary cache

Flow: chat message -> (1) local fast path -> (2) `POST /api/v1/chat/command` (Groq, classification only) -> (3) old `parseChatIntent`.
Original text is always copied by backend code from stored sections; the AI only writes summaries.

## Endpoints
- `POST /api/v1/chat/command` `{message, context}` -> `{command|null, source}`; sends only the message and document name/page count to Groq.
- `POST /api/v1/documents/{id}/extract` `{selector:{type,spec}, mode}`; types: pages, items, paragraphs, heading, chapter, whole; modes: source_passages (no AI), both, ai_study_material.
- `GET /api/v1/documents/{id}/extract/export?format=pdf|docx&type=&spec=&mode=` re-runs the selection (summaries come from the cache).

## Environment variables (Render)
`GROQ_API_KEY`, `AI_PROVIDER_ORDER=groq,gemini`, `GROQ_MODEL`, `GROQ_COMMAND_MODEL` (default `qwen/qwen3.8-27b`; verify ids in your Groq console),
`SUMMARY_CHUNK_CHARS=4000`, `SUMMARY_MAX_CHUNKS=12`, `SUMMARY_CACHE_ENABLED=true`, `GROQ_MAX_WAIT_S=8`, `MAX_SUMMARY_CHUNK_CHARS=4000` (for the older study-job flow).
Provider failures keep their actual classification. Daily quota exhaustion is reported
as `ai_quota_exhausted`, temporary rate limits as `ai_rate_limited`, and network,
timeout, provider, and invalid-response failures keep their corresponding codes. Failed
summary-only sections retain their source text in previews and exports.

## Tool-calling probe (2026-11-12)
- `qwen/qwen3.8-27b` returned a valid `tool_calls` response for a forced `echo` function.
- `openai/gpt-oss-120b` also returned a valid tool call.
- `llama-3.3-70b-versatile` returned 404 from the current Groq account, so the planner must use the configured model and probe support at runtime.
- The planner still needs a JSON-plan fallback if a model or account stops supporting tools.

## Known limits
- `health.py`, `api/study.py`, and `config.py` now use the configured provider chain (`ai_configured`/`ai_available`), not a Gemini-only check.
- Render free tier: sleeping server and ephemeral storage; re-upload after a cold restart. Local SQLite and uploaded files survive restarts.
