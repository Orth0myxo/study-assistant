# Groq command step, range extraction and summary cache

Flow: chat message -> (1) local fast path -> (2) `POST /api/v1/chat/command` (Groq, classification only) -> (3) old `parseChatIntent`.
Original text is always copied by backend code from stored sections; the AI only writes summaries.

## Endpoints
- `POST /api/v1/chat/command` `{message, context}` -> `{command|null, source}`; sends only the message and document name/page count to Groq.
- `POST /api/v1/documents/{id}/extract` `{selector:{type,spec}, mode}`; types: pages, items, paragraphs, heading, chapter, whole; modes: source_passages (no AI), both, ai_study_material.
- `GET /api/v1/documents/{id}/extract/export?format=pdf|docx&type=&spec=&mode=` re-runs the selection (summaries come from the cache).

## Environment variables (Render)
`GROQ_API_KEY`, `AI_PROVIDER_ORDER=groq,gemini`, `GROQ_MODEL`, `GROQ_COMMAND_MODEL` (default llama-3.3-70b-versatile; verify ids in your Groq console),
`SUMMARY_CHUNK_CHARS=4000`, `SUMMARY_MAX_CHUNKS=12`, `SUMMARY_CACHE_ENABLED=true`, `GROQ_MAX_WAIT_S=8`, `MAX_SUMMARY_CHUNK_CHARS=4000` (for the older study-job flow).
When every provider is limited, the original text is still returned with the note "Summary limit reached. Original text is still available."

## Known limits
- `api/study.py`, `health.py`, `config.py` still check only the Gemini key for the legacy study-job AI modes: keep `GEMINI_API_KEY` set or those modes return 503.
- Render free tier: sleeping server and ephemeral storage; re-upload after a cold restart.
