# Task 8: topic continuation across pages, and table-aware extraction

Owner's rules still apply: no built-in wording, no phrase lists, no keyword/regex intent matching,
no sample sentences in code. Source passages stay verbatim (taken from stored text, never AI-written).
Work one piece at a time: test, fix, retest.

## Observed problems (test file: a 5-page stains table, "HISTOPATHOLOGY & CYTOPATHOLOGY STAINS PMLT")

1. Asking for a topic (e.g. the cytology stains) returned only page 1 (the title page of the table).
   Rows 10-50, including the actual cytology stains on pages 2-5, were not returned.
2. Table rows are flattened: columns run together, row numbers get glued to the end of the previous row,
   so the exported text is hard to read.

## Part A: topic continuation

Goal: when a topic starts on a page or heading, include everything that belongs to it, even if the
matching words only appear on its first page.

- Use two-phase matching in `find_passages` (agent tool) and the topic study path:
  1. find the anchor section(s) where the topic is introduced;
  2. extend forward while the content is still the same topic: no new top-level heading,
     same table continuing (repeated header row, continuing row numbers), or the next page
     begins with continuation of the same structure.
- The decision to continue must come from document structure (headings, table continuation, numbering)
  and, if needed, one bounded model judgement over section boundaries. Not from phrase lists.
- Return the extended range as ordinary source passages with their page anchors so exports
  keep "Page N" headings and page images.
- Tests: a synthetic multi-page table PDF where only page 1 contains the topic word; the result must
  include every page of the table and must stop at the next unrelated heading.
- Log with `topic continuation pages=a-b reason=...` so Render logs show what happened.

## Part B: table-aware extraction

- On PDF ingest use PyMuPDF `page.find_tables()`. Store each table as rows/cells next to the page text
  (keep `Section.raw_text` unchanged so verbatim guarantees hold).
- A table row must never be split or merged with its neighbour. Cell text stays verbatim.
- Tables that continue over pages keep their rows in order; a repeated header row is recognised
  structurally, not by wording.
- Exports: render real tables in DOCX (python-docx table, header row repeated per page, borders) and PDF
  (PyMuPDF drawn grid or `insert_htmlbox`/table layout; readable body size, wrapping cells, rows not split
  across pages).
- Fallback: if table detection finds nothing or looks wrong, keep the current text layout and the page
  image. The page image stays available either way.
- Tests (CI is the only runner for PyMuPDF/python-docx): synthetic table PDF with 3 columns and 12 rows;
  assert rows come back intact, DOCX has a table with the right row/column counts, PDF text keeps the row
  order, and numbers are not glued to the previous row.

## Order of work

1. Part B extraction + storage, with tests.
2. Part B export rendering (DOCX first, then PDF).
3. Part A continuation, using the stored structure.
4. Re-run the owner's 5-page stains file and report: pages returned, table shape, export check.

Report back with commit hashes, CI results and what was not verified.
