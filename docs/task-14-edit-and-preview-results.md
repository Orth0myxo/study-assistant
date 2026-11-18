# Task 14: edit and preview results before exporting

Goal: stop the loop of "export, check, export again". Let the user fix content in the app and preview the layout before a
file is built. Start after Task 12 is tested. Same rules: no built-in wording, one piece at a time, report what was not verified.

## Editing rules (protect the verbatim promise)

- **AI summaries:** freely editable.
- **Verbatim passages:** the source text is not editable as source. The user can remove passages, reorder them and choose
  which pages are included. If the owner later allows typing into a passage, the export must mark that passage as edited
  by the user, and it must no longer be labelled verbatim.
- **Tables read from images (Task 13):** editable cell by cell (already required there).
- Edits live on the phone and are sent to the backend only when exporting (privacy, and survives Render restarts).

## Preview

- Before building, show a preview of the export in the app (page images of the PDF, or a rendered view of the DOCX/PPTX
  blocks) so the user can check order, headings and alignment first. Reuse `exporter.py` block building.
- Preview must be cheap on the free tier: render only the first pages, lazily, and show a clear loading state.
- Export from the edited/previewed state must produce the same file the preview showed. Add a test that preview blocks and
  export blocks match.

## Backend

- Export endpoint accepts an optional edit payload (removed/reordered passage ids, edited summary text, table cells) and
  validates it against the owner's stored result. Reject anything that references another owner's content.

## Tests

- Removing and reordering passages changes the export; editing a summary changes the summary only; verbatim text is
  unchanged unless marked as edited.
- Edits cannot reach another owner's result.

## Report

Commit hashes, CI results, preview time on a real result, what was not verified on a device.
