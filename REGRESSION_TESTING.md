# Regression Testing (SCRUM-206) — current state

What actually exists in the codebase today, verified by reading the test files and
running `pytest --collect-only` / grepping the Playwright specs directly (not taken from
an older analysis doc — this is a fresh pass).

Each sprint's manual test cases (tracked in "Test Cases - Sprint N.xlsx") get an automated
regression test once that functionality is built, so a later refactor can't silently break
behaviour a human already signed off on manually.

## Two places this lives, not one

1. **Backend (pytest)** — API/pipeline-level, `backend/tests/test_regression_*.py`.
2. **Frontend (Playwright)** — UI-level, `frontend/e2e/specs/*.spec.ts`.

Both tag tests with `@sprint1` / `@sprint2` (pytest marker, Playwright `@sprint1`/`@sprint2`
annotation in the test title) and a story/test-case id (`@pytest.mark.story(...)` /
`@DI-001` etc. in the title), so either suite can be run scoped to one sprint:
`pytest -m sprint1`, or Playwright's `--grep @sprint1`.

## Backend — dedicated regression files

| File | Covers | Sprint | Tests |
|---|---|---|---|
| `test_regression_upload.py` | SCRUM-6 Upload Documents — backend side of DI-009 (25MB limit), DI-012/013 (per-file failure) | 1 | 2 |
| `test_regression_ai_pipeline.py` | SCRUM-8/17 Classify, SCRUM-9/19 Extract, SCRUM-115 file formats — full LangGraph pipeline run end to end with Claude's replies scripted | 1 & 2 | 31 (incl. parametrized) |
| `test_regression_approval.py` | SCRUM-20 Validate Extracted Data / Support Manual Correction — DI-025 (approve), DI-026 (block on missing field) | 2 | 5 (3 are `xfail`, see below) |
| `test_regression_checklist.py` | SCRUM-114 Classify Optional Documents, SCRUM-118 Map Documents — checklist/requirement coverage | 2 | 23 (incl. parametrized) |
| `test_regression_fixtures.py` | Keeps the shared fixture data in step with `EXTRACTION_SCHEMAS` (not a sprint test case itself — a guard so the other files' fixtures don't silently drift) | — | 1 |

**62 tests total** in these 5 files. Three notable things about how they're built, not just what they cover:

- `test_regression_ai_pipeline.py` and the checklist/approval files run against **real
  extraction results captured from Supabase** (`tests/fixtures/regression/anchorpoint.json`,
  `harbor-light.json`, via `scripts/capture_regression_fixture.py`), not invented data — only
  Claude's replies are scripted (`FakeClaude`), so these exercise the real pipeline code path
  (queue → ingest → classify → extract → persist), not a mock of it.
- `test_regression_approval.py` has **3 tests marked `xfail(strict=True)`**, not skipped — they
  document a known defect (REG-003: the missing-required-field check treats an empty list
  or an absent company field as "missing" even when that's the schema's own correct value for
  a clean credit report / a personal source-of-wealth statement). `strict=True` means these
  flip to a hard failure the moment someone fixes the underlying bug without updating the test —
  intentional, not neglect.
- `test_regression_fixtures.py` exists purely to stop the two suites (Python backend,
  TypeScript frontend) from drifting apart: the Playwright mock backend can't import
  `EXTRACTION_SCHEMAS` directly (it's Python), so it reads a generated JSON snapshot instead,
  and this test fails the build if that snapshot goes stale.

### Existing unit tests double-counted as regression coverage

This is the thing worth flagging for the "should unit tests have regression-specific stuff in
them" question: **no regression-specific test code lives in the unit test files** — but
`tests/conftest.py` (lines 224–264) has a small central registry,
`_EXISTING_REGRESSION_TESTS`, that retroactively tags specific **existing, ordinary unit
tests** as also satisfying a sprint's manual test case, via a `pytest_collection_modifyitems`
hook:

| Unit test | Tagged as |
|---|---|
| `test_documents.py::test_upload_batch_partial_failure` | Sprint 1, SCRUM-6, DI-013 |
| `test_documents.py::test_upload_batch_rejects_too_many_files` | Sprint 1, SCRUM-6, DI-004 |
| `test_ingestion.py::test_ingest_rejects_unsupported_type` | Sprint 1, SCRUM-6, DI-008 |
| `test_queue_service.py::test_enqueue_marks_queued_and_puts_document_id_on_queue` | Sprint 1, SCRUM-17, MA-001 |
| `test_cases.py::test_list_cases` | Sprint 2, SCRUM-78, CM-001 |
| `test_cases.py::test_get_case_returns_summary_with_documents_status` | Sprint 2, SCRUM-83, CM-005 |
| `test_cases.py::test_create_case_endpoint_with_company_name` | Sprint 2, SCRUM-96, CM-010/012 |
| `test_ingestion.py::test_ingest_native_pdf` / `_scanned_pdf_uses_ocr` / `_ingest_image` | Sprint 2, SCRUM-115, DT-029 |
| every test in `test_mapper.py` (35 tests) | Sprint 2, SCRUM-118, DI-035–038 |

So: **no duplication, no regression-flavoured code sitting inside unit test files** — this is
a tagging layer only, bolted on from the outside. The actual assertions still live in the
normal unit test and only run once. This is a deliberate, documented design (see the comment
at conftest.py:224), not something to clean up.

### Backend totals

- `pytest -m sprint1` → **19 tests** (15 from dedicated files + 4 tagged-in-place)
- `pytest -m sprint2` → **83 tests** (42 from dedicated files + 41 tagged-in-place, mostly
  from `test_mapper.py`'s 35)
- Full backend suite: **1,032 tests** collected; regression-tagged tests are a subset run
  as part of every normal `pytest` invocation, not a separate job.

## Frontend — Playwright specs, in detail

All run against a fully mocked backend (`e2e/support/mockBackend.ts`) — these are
UI-behaviour tests (does the page show/do the right thing given a canned API response), not
pipeline tests. The backend regression files above are what exercises the real pipeline/API
logic. **52 tests total, only the 16 in `upload.spec.ts`'s first block are `@sprint1`.**

### `upload.spec.ts` — Upload documents (SCRUM-6, Sprint 1) — 16 tests

| Test | What it actually checks |
|---|---|
| DI-001 | Initial page state: drop zone visible with its hint text, "No files selected yet" |
| DI-002 | A file dropped via drag-and-drop appears in the file list |
| DI-003 | Dropping files one at a time doesn't overwrite the previous one |
| DI-004 | Dropping 2 files at once adds both |
| DI-005 | A file added via the Browse file-input appears in the list |
| DI-006 | Browse after a drop adds to the list rather than replacing it |
| DI-007 | A real PDF (correct magic bytes) uploads without a format error |
| DI-008 | A `.txt` file (no PDF/PNG/JPG signature) is rejected — the exact message, and confirms no upload request was even sent |
| DI-009 | A file just over 25MB is rejected with the size-limit message, no request sent |
| DI-010 | While an upload is in-flight: badge reads "Uploading", an aria-label is present, the button reads "Uploading…" |
| DI-011 | A successful upload shows "Uploaded" and the returned document ID |
| DI-012 | A simulated storage failure shows "Failed" and the server's error text |
| DI-013 | Two files uploaded together, one failing — each keeps its own independent badge |
| DI-014 | After a failure, the button becomes "Upload again"; retrying succeeds and sends a second batch request |
| DI-015 | The completion toast's exact wording after a successful upload |
| DI-016 | Full flow: drop + browse two files, upload both, then the case's live document table picks them up |

### `upload.spec.ts` — Generate additional file formats (SCRUM-115, Sprint 2) — 2 tests

| Test | What it actually checks |
|---|---|
| DT-031 | `.docx`/`.xlsx`/`.txt` (valid zip/text bytes, wrong signature for this app) are all rejected with the same "does not match a supported type" message |
| DT-029 | PDF, PNG, JPG and JPEG (note: `.jpeg` extension, not just `.jpg`) are all accepted |

### `document-review.spec.ts` — Extract text from documents (SCRUM-9, Sprint 2) — 3 tests

| Test | What it actually checks |
|---|---|
| DI-020 | Every extracted field row shows a confidence badge formatted `0.00`–`1.00` |
| DI-021 | A field extraction left empty renders as `—`, and that alone doesn't put the document in an error state |
| DI-030 | An array-valued field ("Loan Terms") shows an item count and expands into a table listing every item |

### `document-review.spec.ts` — Validate extracted data / manual correction (SCRUM-20, Sprint 2) — 9 tests + 1 untagged + 2 known-defect

| Test | What it actually checks |
|---|---|
| DI-023 | A document with one missing required field (UEN) routes to Manual Review, that field is editable, high-confidence fields stay read-only |
| DI-024 | Filling the flagged field and saving persists it, closes the review section, and the saved value survives a page reload at confidence 1.00 |
| DI-025 (×1) | Once the missing field is fixed, routing flips to Accepted and the document can be approved; appears on the case's Approved Documents list afterward |
| DI-025 (×1) | A document that was *also* flagged by the classifier (not just low field confidence) still clears all the way to Approved after saving |
| DI-026 (×1) | Clicking Save fields with the required field still empty shows the exact error and sends no request |
| DI-026 (×1) | Approve is disabled with the exact warning text while a required field is missing, and sends no request |
| *(untagged)* | An unrecognised document: type dropdown starts blank/disabled-confirm, selecting "Other" and confirming persists `document_type: 'unrecognised'` and displays as "Other" |
| DI-025 *(`test.fail`, REG-003)* | **Known, tracked defect**: a clean credit report (`defaults: []`, the normal/correct case) can never be approved — the check treats an empty list as "missing" |
| DI-025 *(`test.fail`, REG-002)* | **Known, tracked defect**: Save fields refuses to save if *any* field is empty, even one extraction was confident is legitimately absent (not just the flagged one) |

I checked whether REG-002 is still live by reading `DocumentDetailsPage.tsx`'s
`handleSaveFields` directly ([DocumentDetailsPage.tsx:1202](frontend/src/pages/DocumentDetailsPage.tsx#L1202)):
`hasEmptyField` loops over **every** extracted field, not just the ones flagged
`canEditField`/needing attention — so yes, still live, the test's `test.fail` is accurate today,
not stale.

### `checklist.spec.ts` — Classify optional documents (SCRUM-114, Sprint 2) — 4 tests

| Test | What it actually checks |
|---|---|
| DI-031 | Required vs. additional documents render as two separate labelled lists with the right counts |
| DI-032 | Removing every optional document still shows the case as "Ready for review" (optional items never count against completeness) |
| DI-033 | Uploaded optional documents tick their matching optional requirement and appear in the documents table |
| DI-034 | A case missing one optional item still shows full required coverage, and only the missing optional item is unticked |

### `checklist.spec.ts` — Map documents (SCRUM-118, Sprint 2) — 4 tests

| Test | What it actually checks |
|---|---|
| DI-035 | Each document's displayed type matches its classification, and ticks the right requirement (incl. an "additional" one, list of loans) |
| DI-036 | An NRIC front+back pair fulfils *both* the identity-document and proof-of-address requirements, shown as "(2/2)" |
| DI-037 | Loading two different cases' fixtures at once — each case's document table only shows its own documents |
| DI-038 | Cross-checks every required requirement's ticked/unticked state against the fixture's actual fulfilled list (11 requirements checked individually) |

### `case-management.spec.ts` — View cases (SCRUM-78, Sprint 2) — 4 tests

| Test | What it actually checks |
|---|---|
| CM-001 | Search by company name substring, and by exact case ID |
| CM-002 | Search narrows correctly; a no-match search shows the empty-state message |
| CM-003 | Clicking a case card navigates to that case's upload page |
| CM-004 | Status chip filtering — but only exercises 2 of the 5 real status chips (see gaps below) |

### `case-management.spec.ts` — View case details (SCRUM-83, Sprint 2) — 5 tests

| Test | What it actually checks |
|---|---|
| CM-005 | Opening a case shows its case ID and company name in the header |
| CM-006 | Case details only show that case's own documents, not another case's |
| CM-007 | The Customer Risk Summary section renders its "not available yet" placeholder (risk scoring isn't built) |
| CM-008 | The Flagged Items panel lists only the unrecognised/manual-review document, not the accepted one, with the right reason text |
| CM-009 | The Case Audit Trail section renders its "not available yet" placeholder (audit log isn't built) |

### `case-management.spec.ts` — Create case (SCRUM-96, Sprint 2) — 4 tests

| Test | What it actually checks |
|---|---|
| CM-010 (×2) | Creating a case sends the right payload; creating with no company name shows a validation error and sends nothing |
| CM-011 | A new case gets a case ID distinct from every existing one |
| CM-012 | A created case's details persist across a page reload |
| CM-013 | A newly created case shows up in the queue and is findable by its case ID via search |

## CI

Both suites run on every push/PR, not as a separate "regression" job:

- `backend-ci` runs the full `pytest` suite (`.github/workflows/ci-cd.yml`) — no `-m` filter,
  so sprint1/sprint2-tagged tests run alongside everything else every time.
- `frontend-ci` runs `npm run test:e2e` (the full Playwright suite, same story — no
  sprint-scoped CI step exists; `-m sprint1`/`--grep @sprint1` are there for a developer to
  scope a local run, not something CI uses).

## What I did NOT find

- No regression-specific test code duplicated inside backend unit test files — only the
  tagging registry described above.
- No CI step that runs only `sprint1` or only `sprint2` — both sprints' tests run together,
  every time, as part of the normal suites.

## Gaps found by reading the frontend code (Sprint 1 & 2)

Everything below is a real, implemented, user-facing behaviour I found by reading the actual
source (not a paraphrase of an AC) that has **zero Playwright coverage** anywhere in `e2e/`.
Fraud-detection-specific checks (font/layout/metadata/numerical/etc.) are deliberately excluded,
per earlier agreement that those are tracked separately.

### Sprint 1 — Upload documents (SCRUM-6)

All four of these are real branches in [fileValidation.ts](frontend/src/utils/fileValidation.ts),
each with its own user-facing error message, and none is ever triggered by a test:

1. **Empty file (0 bytes)** — `describeIssue`'s `'empty'` case ("File is empty (0 bytes).").
2. **Unsafe/invalid filename** — `sanitizeFilename` (path-traversal stripping, unsafe characters,
   a name that sanitizes to nothing) and its `'invalid-filename'` message.
3. **Duplicate file** — content-hash dedup against both the current batch and this case's
   previously-uploaded files (`getUploadedHashes`/`recordUploadedHash`). The test helper's own
   comment even describes this behaviour; nothing exercises it.
4. **Bulk file-count limit** — `MAX_BULK_FILES = 20` and `checkFileCount`'s truncation.

### Sprint 2 — Document review / manual correction (SCRUM-9/20)

5. **Reject Document** — a real button with a confirm dialog
   ([DocumentDetailsPage.tsx:2225](frontend/src/pages/DocumentDetailsPage.tsx#L2225)) that calls
   a real endpoint. The mock backend even has a working `/reject` handler
   (`mockBackend.ts:303`) — it's just never called from any spec.
6. **Cancel / Retry detection** — the cancel-in-flight and retry-after-crash buttons built this
   session have no e2e coverage at all (the old mock handler for the removed manual "run
   detection" button was deleted, but nothing was added for the two new ones).
7. **Add/delete a custom field** in manual review (`+ Add field`, `.delete-field-button`) —
   untested.
8. **Editing an array/object-valued field** via `ArrayFieldEditButton`/`ObjectFieldEditButton` —
   DI-030 only tests *displaying* an array field, never editing one through manual review.
9. **Correcting a low-confidence but recognised type** — the only type-correction test
   (untagged, in `document-review.spec.ts`) covers "Unrecognised → Other." Nothing tests the more
   common case: a document classified as a real type but below the confidence threshold, which
   an officer corrects to a *different* real type.

### Sprint 2 — Document checklist (SCRUM-114/118)

10. **Proof-of-address "unmatched names" hint** — `UploadPage.tsx`'s
    `.requirement-unmatched` block ([UploadPage.tsx:1720](frontend/src/pages/UploadPage.tsx#L1720))
    shows names that have an address on file but don't match any identified individual
    (backend: `compute_unmatched_proof_of_address_names`). Never triggered in any spec.
11. **NRIC "missing side" hint** — the single-identity case where only the front or only the
    back has been uploaded shows a specific hint ("front uploaded, back missing") instead of a
    bare fraction ([UploadPage.tsx:1685](frontend/src/pages/UploadPage.tsx#L1685)). DI-036 only
    tests the complete 2/2 case.

### Sprint 2 — Case management (SCRUM-78/83/96)

12. **3 of 5 status filter chips** — `CaseQueuePage.tsx` has 5 status chips ("Ready for review",
    "In progress", "Documents failed", "Processing stuck", "Documents pending"). CM-004 only
    exercises the first two; no fixture anywhere in the e2e suite even sets
    `documents_status` to `'failed'` or `'stuck'`.
13. **Delete a case** — a real feature, callable both from the case queue and from inside a case
    (`deleteCase` in `uploadService.ts`, wired in `CaseQueuePage.tsx` and `UploadPage.tsx`, with a
    status guard mirroring the backend's delete rule). Zero coverage — `create-case.spec` only
    ever adds cases, never removes one.

Of these, I'd prioritize **5 and 6** (Reject, Cancel/Retry detection) — they're the most recently
built, least battle-tested, and most consequential if broken silently (a reject that doesn't
actually lock the document, or a retry that double-launches detection, would be a real
production bug, not a cosmetic one).
