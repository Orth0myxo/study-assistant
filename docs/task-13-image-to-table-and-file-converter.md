# Task 13: image of a table (for example an attendee list) to DOCX / Excel

The app shows a "File Converter, coming soon" strip. Owner's first real use case: a friend has an image of a list
(columns such as S/N, name, phone number, one row per attendee) and wants it as an Excel file. Test first with DOCX.

Same rules: no built-in wording, one piece at a time, report what was not verified. Depends on Task 12 part H
(photo without a source document).

## What to build

1. **Read the table from the image.** Use a vision-capable model (currently Gemini for images; Groq Qwen 3.8 27B accepts
   images in the playground, so make the provider a setting). Ask for rows and columns as structured data, not prose. Keep
   headers if present. Do not use phrase lists or fixed column names; columns come from the image.
2. **Review before export (required).** Show the extracted table in the app as an editable grid. Names and phone numbers are
   easy to misread, so the user must be able to fix cells, add/remove rows and columns, and see low-confidence cells marked
   (use model confidence if the provider gives it, otherwise mark cells the reader flagged as unreadable). Label the table as
   read from an image, not as source text.
3. **Export.** DOCX: a real table (python-docx table, header row, borders, readable size). Excel: `.xlsx` needs a library
   (openpyxl or xlsxwriter) which is a new dependency, so ask the owner first. A CSV export needs no dependency and opens in
   Excel; offer it as the fallback. Keep phone numbers as text so leading zeros and `+` are not lost.
4. **Use the Export control from Task 12 part A** (collapsed "Export", then format choices), and the same job/polling pattern.
5. **Large lists:** photos with many rows should be read in bounded passes (strips of rows) and merged in order; report row
   count read so the user can compare with the image.

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
