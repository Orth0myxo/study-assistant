# Task 10: restore the previous workspace interface and run it on the agent route

## Problem

The Task 6 chat screen (`mobile/components/AgentWorkspaceScreen.tsx`, added in `b3d8d1e`, fixed in `46a66ff`) replaced the
interface the owner liked. It is selected by `EXPO_PUBLIC_AGENT === '1'` in `mobile/app/workspace.tsx`; the old screen is
still there as `LegacyStudyWorkspaceScreen`. The owner's screenshots of the old interface are the reference:

- header: hamburger menu on the left, serif "Study Assistant" title centred, "Settings" link on the right;
- empty state: serif heading, one short muted line, lots of calm space;
- attach: a bottom sheet "Add to your study" with two rows, "Upload a document" (PDF or DOCX) and
  "Add photos of notes" (choose a source document, then select images), plus Cancel;
- no preset chips; a single message box.

The new screen instead has a plain sans header with no menu or Settings, four built-in chips ("Original passages",
"Summary", "Both", "Page range"), loose "File" / "Image" pills, and the message box with Send on its own row.

## Goal

Same look and structure as the old interface, but the message goes to the agent endpoint (`POST /api/v1/agent/chat`).
Change the logic underneath, not the design.

## Requirements

1. Keep the old screen's layout, typography, header (menu, title, Settings), empty state and attach sheet exactly.
   Compare `LegacyStudyWorkspaceScreen` with `AgentWorkspaceScreen` and keep the legacy styles.
2. Reuse the agent plumbing already written: `requestAgentChat`, `agentWorkflow` (uploads, passages, share),
   `agentPresentation` (blocks), conversation id handling, attachment limits.
3. Remove the preset chips and `ACTIONS`. Remove `WELCOME_ACTIONS`/`buildWelcomeRequest` from the agent route, because
   they build phrases ("verbatim text about ...") the app must not invent. The user's own text goes to the agent unchanged.
4. Remove the floating gear button if it exists in the old screen (it duplicates Settings and looks accidental).
5. The hamburger menu still opens past results/chats, using what the old screen did.
6. No `parseChatIntent`, keyword or regex intent routing on the agent path. The agent decides.
7. Make the agent path the default build, not an env flag. Keep a way to fall back to the legacy path only if the
   backend reports the agent is disabled, and say so on screen in the existing error style.
8. Keep Settings showing the real Android build number and nothing else new.
9. Do not add any built-in suggestion text, sample sentences or prompts.

## Tests and report

- Mobile checks (typecheck, lint, vitest) must pass in CI.
- Add a test that the agent route sends the user's text unmodified and that no preset wording is exported from the
  mobile services used by the screen.
- Build a preview APK and report: commit hash, CI result, build number, and which old-interface elements were verified
  on a device versus only read in code.
