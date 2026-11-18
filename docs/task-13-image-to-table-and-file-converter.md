# Task 13: image of a table (for example an attendee list) to DOCX / Excel

The app shows a "File Converter, coming soon" strip. Owner's first real use case: a friend has an image of a list
(columns such as S/N, name, phone number, one row per attendee) and wants it as an Excel file. Test first with DOCX.

Same rules: no built-in wording, one piece at a time, report what was not verified. Depends on Task 12 part H
(photo without a source document).

## What to build

1. **Read the table from the image: local first, AI optional.**
   - Default (local, nothing leaves the server): Tesseract word boxes (`image_to_data`) grouped into rows by vertical position
     and columns by horizontal gaps. No phrase lists or fixed column names; the layout comes from the image.
   - Optional "read with AI" for blurry, crooked or handwritten photos: a vision-capable model (Gemini today; Groq Qwen 3.8 27B
     accepts images in the playground, so make the provider a setting). The app must say the image goes to the provider before
     sending it. Never use it silently as a fallback.
   - Measure both on the owner's real attendee list (row count and cell accuracy, time on the free tier) and report before
     choosing the default. Local OCR on Render free took 14-28 s per photo earlier, so long lists need bounded passes.
2. **Review before export (required).** Show the extracted table in the app as an editable grid. Names and phone numbers are
   easy to misread, so the user must be able to fix cells, add/remove rows and columns, and see low-confidence cells marked
   (use model confidence if the provider gives it, otherwise mark cells the reader flagged as unreadable). Label the table as
   read from an image, not as source text.
3. **Export.** DOCX: a real table (python-docx table, header row, borders, readable size). Excel: `.xlsx` via `openpyxl` or `xlsxwriter`
   (the owner approved adding it; also `python-pptx` for PowerPoint). Add to `backend/requirements.txt` with pinned versions;
   Render installs them during deploy, nothing is needed on the owner's phone. Run CI before pushing so a bad dependency cannot
   break the live deploy, and check the added build time. CSV stays as a no-dependency fallback. Keep phone numbers as text so leading zeros and `+` are not lost.
4. **Use the Export control from Task 12 part A** (collapsed "Export", then format choices), and the same job/polling pattern.
5. **Large lists:** photos with many rows should be read in bounded passes (strips of rows) and merged in order; report row
   count read so the user can compare with the image.

## PowerPoint (after tables work)

Add `.pptx` export through the same Export control: one slide per topic/page group, verbatim text kept as source, summaries
labelled AI. Use `python-pptx`. Keep slide text readable (large body size) and reuse the export block list.

## Privacy

Attendee names and phone numbers belong to other people and are sent to the vision provider. The app must say so before
the image leaves the device (ties to the owner's privacy policy). Do not log cell text, only counts and timings. Delete the
image and the extracted table with the normal retention sweep and the delete controls.

## Tests

- Fake vision provider returns a 3-column, 12-row table: the grid has 12 rows; DOCX has a table with 12 rows + header; CSV
  has the same cells; phone numbers keep leading zeros.
- Provider failure returns a clear status (not an empty file).
- No cell text appears in logs.

## Report

Commit hashes, CI results, which provider read the sample image, how many rows/columns were read against the real image
(owner supplies the count), and what was not verified on a device.

## Addendum: no single-format implementation (owner requirement)

The owner's sample (a handwritten attendance sheet: a title line and date above a table with six columns) is only one example.
Nothing may be built around it. The attendee list in this file is an illustration, not a layout.

- No fixed column names, column counts, header wording, languages or sheet titles anywhere in code, prompts or tests
  (the reader's module comment already says so; keep it true). A grep for the sample's header words in `backend/app` and
  `mobile` must find nothing.
- Inputs: a photo of a table, a photo with a title or notes above/below the table (the title is not a row), a photo with
  no header row, a typed or pasted text list (tabs, commas, repeated spaces, one item per line), and several photos of one
  table. Text input needs no OCR and no AI: split by the structure present, and offer the AI reader only for unstructured text.
- Handwriting: Tesseract is weak on handwriting, so for such photos the local read may be poor. Detect low overall confidence
  and offer the optional AI reader in the app's own words, never silently.
- Generalisation tests: at least these fixtures with different shapes, none sharing header text with each other: 3 columns x 10
  rows typed; 6 columns x 25 rows; no header; a title line above the table; a text list with commas; a text list with tabs; a
  slightly rotated image; a single column. Assert shape and cell content for each.
- Output formats stay generic: the same table data feeds CSV, XLSX and DOCX (and later PPTX).
