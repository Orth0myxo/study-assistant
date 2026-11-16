# Task 3 — OCR failures, empty passages, empty export (backend only)

Evidence: Render logs and phone test of 2026-11-16 07:43 on `Test 1.pdf` (53 pages, 46 native-text pages, 11 OCR candidates), topic "vaginal cytology". Deployed commit `36a229a`.

## What the logs showed
- Upload is fast now (0.9 s). The wait moved to the first topic request: `topic-review` took 18.7 s.
- Page OCR: page 2 `done` 4.4 s, page 4 `failed` 3.3 s, page 17 `failed` 2.4 s, page 22 `failed` 2.4 s, page 25 `done` 6.1 s.
- Failures end in 2–3 s, so they are not the 30 s timeout. They are `OcrFailed` (non-zero Tesseract exit) or another exception. The log line prints only `status=failed`, never why. Memory pressure on the Render free instance at `OCR_DPI=200` is a hypothesis, not a finding.
- Pages 27, 31, 32, 41, 43, 47 stayed pending (5-page / 20 s cap), so topic matching ran on an incomplete set.
- The user then typed "Page 2": the app showed a passage with the heading "Page 2" and an empty body, although the page's OCR status was `done`. The topic-review → chosen passage → export path also produced an empty file.

## Work, in this order. One sub-task at a time: implement, test, push, then the next.

### 3a. Say why OCR fails (do first, smallest)
In `page_ocr.py` / `ocr.py`: log the exception type, Tesseract return code, and the first 200 characters of stderr (no document text, no file name). Also log image width x height, DPI, and the number of characters OCR returned for each page. Keep stdout and stderr out of API responses. Test with a fake runner that raises `OcrFailed`.
Deliver, then ask the owner to repeat the cytology request and send the new log lines. Do not guess a fix before this data exists.

### 3b. Reduce OCR failures
After 3a shows the cause. Likely options, apply only what the logs justify: render at 150 DPI, downscale pages above a pixel bound, and on `OcrFailed` retry once at a lower DPI before marking the page failed. Test with the fake runner.

### 3c. Never present an empty result as verbatim
When OCR is `done` but the text is empty or whitespace, mark the page (`ocr_empty`) and return `has_page_image=true`. The passage must say "No text could be read from this page" in its text field, never a blank body. Exports must embed the slide image for that page (Task 2 already supports `include_page_images`) and add a visible note. Add API and export tests.

### 3d. Topic-review choice → empty file
Reproduce with a test: an ambiguous topic ("Multiple source passages matched this topic with similar strength") where the user then selects one passage. Required behaviour:
1. The pages of the selected passage are OCR'd (or reported pending/failed) before returning.
2. A selected passage with no text returns an explicit error or warning, not a 200 with an empty body.
3. Export refuses to write a file with zero passages of text. It returns a clear error naming the pages (extend the existing `ocr_incomplete` handling).

### 3e. Order OCR by usefulness (only if time allows)
Pending pages are processed in page order under the 5-page / 20 s cap. Order candidates so pages near a native-text heading that matches the topic come first. Keep the cap. Do not add background OCR in this task.

## Out of scope (Task 4, needs a new APK)
Stale "no extractable text was found" message for scanned PDFs (`mobile/app/workspace.tsx:724`), `%20` shown in file names, OCR progress text in chat, study-mode chips visible before the first request.

## Rules
- One commit per sub-task, pushed without force. Do not re-author or rebase published commits.
- Add tests with each change. CI (backend-tests) is the check; local runs are not possible here.
- Update `SESSION.md` with the commit ids and what remains unmeasured. State plainly what could not be verified.
