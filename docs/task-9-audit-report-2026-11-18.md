# Task 9 audit result (OpenCode audit, source at b1d3c76)

Verdict: the app does not yet satisfy `docs/task-9-privacy.md`. Several privacy controls exist; most of the user-facing,
deletion and test requirements are missing. Kept for later use together with the owner's own policy.

Already in place: anonymous device identity (token hash stored, token in Android Keystore); owner scoping with isolation tests;
retention sweeps for documents, jobs, topic sources/images, topic results and agent state; document deletion removes file,
page rows and cached page images; six fixed agent tools with no URL/network/cross-user tool; no provider key in the app;
mostly compliant logs; Render storage is ephemeral (`/tmp`, no disk).

| Item | Status | Gap |
|---|---|---|
| 1 Data-flow map | Not done | `docs/privacy-data-flow.md` missing |
| 2 Provider disclosure/consent | Not done | upload goes straight to file selection; Settings says only "your configured server"; Topics/Results name "Gemini API key" even when Groq or the agent provider is used; no consent gate |
| 3 Logs | Partial | `page_ocr.py` logs a Tesseract stderr excerpt; no upload+study test asserting no document text or filename |
| 4 Retention/delete | Partial | summary cache only cleaned lazily on `get()`; no delete-everything endpoint or Settings button; exports/page images stay in device cache |
| 5 BYOK | Not applicable yet | no BYOK implemented; old Groq key rotation unverified |
| 6 Prompt injection | Structural safeguards, weak test | the "hostile" scenario fixture contains no hostile text and the summary client returns fixed text |
| 7 Render storage | Confirmed in code, not documented | |
| 8 Rotate old Groq key | Unverified | |
| 9 Legal basics | Owner task | no in-app privacy notice or provider terms |

Highest-risk findings: no pre-upload AI disclosure/consent; no delete-everything path; lazy summary-cache retention;
misleading provider names in the UI; meaningless hostile-document test; one log path above policy.

Not verified: key rotation, production cleanup, provider data-use terms, device cache behaviour, live agent/fallback behaviour.
