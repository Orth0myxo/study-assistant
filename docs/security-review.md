# Secret history scan

Date: 2026-11-12

Scope: all reachable commits (`git log --all -p`) across the repository.

## Finding

- `.env.example` contained a real-looking Groq API key (length 56, `gsk_` prefix) in commit `dcf8ddd45c62`.
- The line was removed in commit `2a9c325a7faf`.
- The key is not present in the current working tree; `backend/.env` is git-ignored.
- No OpenAI, GitHub, AWS, Google API key, private key, or bearer-token patterns were found in history.

## Required action

Rotate the exposed Groq key. History still contains the old value, so removal from the current tree is not sufficient. Do not rewrite history without explicit approval.
