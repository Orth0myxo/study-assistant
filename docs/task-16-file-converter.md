# Task 16: File Converter (a feature separate from studying)

Owner decision (2026-11-19): the "File Converter" is a general tool. It takes a file, a photo or pasted text and produces another
format. It is not tied to studying. Only conversions that are easy and reliable are included. Same rules as always: one piece at a
time (test, fix, retest), no built-in wording or phrase lists, no format hardcoding, report what was not verified.
Do not push docs-only commits during the owner's test sessions.

## In scope (build in this order)

1. **CSV to XLSX, XLSX to CSV** (and CSV/XLSX to DOCX table). Easy; build first.
2. **Photo or pasted text to table** (exists: `table-converter.tsx`, `table_reader.py`, `table_exporter.py`). Move it inside the File Converter.
3. **Word tables to Excel** (each table to a sheet). Only if easy; low priority.
4. **Text or Word to PDF.** Read paragraphs, headings and tables with python-docx and rebuild with the app's own PDF builder
   (`exporter.py`). Layout is simplified, and the app says so in its own words. No LibreOffice.
5. **PDF to Word** and **Word to PDF** through the existing parser and exporter (the owner already does these in the app). Text,
   headings and tables are kept; the original layout is not. Scanned pages follow the existing OCR/page-image rules.
6. **Summary as a table, exported to Excel or Word.** AI path: ask the user first, same notice as the photo AI reader. The model
   must return structured rows and columns (not markdown that is parsed afterwards), within the provider's output limits;
   the user edits the table before export. Reuse Task 15 B table rendering for Word.

## Out of scope (do not build now)

- PDF tables to Excel.
- Layout-faithful Word to PDF or PDF to Word (would need LibreOffice, too heavy for the free tier).

## Structure

- A section of its own: a File Converter screen (`/table-converter` becomes the "photo or text to table" step inside it) with
  1. pick the input (file, photo, or pasted text),
  2. pick the output; offer only the conversions that are really supported for that input,
  3. optional edit and preview (reuse Task 14 preview/edit and the collapsed Export control),
  4. convert and download/share.
- Backend: its own namespace (for example `/api/v1/convert/...`), owner-scoped, background job plus polling for slow work, files and
  results swept by the normal retention rules. Reuse `document_parser`, `exporter`, `table_exporter`; do not route through the
  study pipeline or the agent. The agent may call it later as a tool; that is not part of this task.
- Supported conversions come from one table in code (input kind, output kind, handler), so adding a conversion later is a one-line
  registration and the app's picker is built from that table. No special cases per sample file.

## Correctness and safety

- **CSV reading:** detect encoding (utf-8 with BOM, utf-8, then a single-byte fallback) and the delimiter (comma, semicolon, tab) from the
  data. Keep every cell as text unless the user chooses otherwise, so leading zeros, `+` and long numbers survive (phone numbers, ids).
- **Spreadsheet injection:** when writing CSV or XLSX, neutralise cells that begin with `=`, `+`, `-`, `@`, tab or carriage return (store as text / prefix safely),
  since converted content can come from untrusted files.
- **XLSX reading:** `openpyxl` read-only mode, values not formulas, size and sheet limits; a multi-sheet workbook asks which sheet (or exports all
  as separate files/sheets). Reject macros (`.xlsm`) and anything unreadable with a clear status.
- **Limits on the free tier (512 MB, shared CPU):** cap file size, rows, columns and pages; stream where possible; never hold more than one big
  file in memory; one heavy conversion at a time. Report the caps in the app's own wording.
- **Privacy:** no AI provider is used unless the user chooses the AI path. Conversions that need no AI never send content out. Never log
  cell text or file names; log kind, sizes and timings only.
- **Honesty about layout:** converted Word/PDF files say that layout is simplified; AI output is labelled as AI-written; photo-read tables are
  labelled as read from an image.

## Tests

- Each registered conversion: a small fixture in, expected cells/text/rows out, correct output type.
- CSV with commas, semicolons and tabs; quoted fields containing delimiters and newlines; leading-zero phone numbers; non-ASCII text.
- Injection: a cell `=1+1` is not written as a formula.
- Multi-sheet workbook; empty file; oversized file; unreadable file returns a clear status, never an empty output.
- The picker offers only registered conversions for an input kind.
- Fixtures differ in shape and header text; nothing depends on a particular layout.
- No cell text or file name in logs.

## APK

Mobile changes (the converter section, picker, preview) need one new APK after the work is done. Backend-only pieces deploy without one.

## Report

Commit hashes, CI results, per-conversion status (works / limited / not done), measured time and memory on the free tier for a large CSV and
a Word file, and what was not verified on a device.

## Status at handoff (2026-11-20)

Implemented on `feature/verbatim-groq-pipeline` and committed in `608e88f`, with
follow-up `c66d4eb`. Backend `compileall` and `git diff --check` pass. Backend
Actions `44734540670` and Mobile Actions `44733055707` passed. Mobile
`npm test -- --run` passes 121 tests, `npm run typecheck` passes, and lint has only
the existing unused `commandFromLocalIntent` warning.

Per-conversion status:

| Conversion | Status | Notes |
| --- | --- | --- |
| CSV ↔ XLSX, CSV/XLSX → DOCX table | Limited | Implemented; CI passed; local runtime and device export unverified |
| Photo or pasted text → table | Limited | Existing `/api/v1/tables` flow, now linked from File Converter; CI passed; device unverified |
| Word tables → Excel | Limited | Implemented with the 20-table cap/warning; CI passed; device unverified |
| Text/Word → PDF | Limited | Implemented with simplified-layout notice; CI passed; device unverified |
| PDF → Word / Word → PDF | Limited | Implemented with OCR warning and simplified layout; CI passed; device unverified |
| Summary table → Excel/Word | Limited | Implemented with explicit AI consent, structured rows, editable export; CI passed; device unverified |

Not yet done: measured time and memory on the free tier, and device verification
of picker, editing, preview, share, and exports. Preview APK build
`4f0f04e5-82bc-4a4b-a45d-48648bf38bf2` (versionCode 23) was built from `c66d4eb`.
PDF table extraction to Excel and layout-faithful conversion remain out of scope.
