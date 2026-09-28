# Regression Report — Sprint 1 & Sprint 2

**Story:** SCRUM-206 Conduct Regression Testing
**Branch:** `SCRUM-206-Conduct-Regression-Testing` (based on `017c9ad`)
**Run date:** 2026-09-28
**Source test cases:** `Test Cases - Sprint 1.xlsx`, `Test Cases - Sprint 2.xlsx`

## Summary

| Suite | Tests | Passed | Known defects (expected failures) | Failed |
|---|---|---|---|---|
| Backend — pytest (all) | 910 | 907 | 3 (REG-003, REG-004, REG-005) | 0 |
| └ tagged `sprint1` | 85 | | | |
| └ tagged `sprint2` | 119 | | | |
| Frontend — Playwright | 49 | 47 | 2 (REG-001, REG-002) | 0 |

| Manual test cases | Sprint 1 | Sprint 2 | Total |
|---|---|---|---|
| In the sprint sheets | 38 | 60 | 98 |
| Automated and passing | 31 | 47 | 78 |
| Automated, defect found | 5 | 4 | 9 |
| Deferred — AI pipeline (until LangGraph) | 2 | 9 | 11 |

(Counted per test case ID. TD-001 – TD-006 appear in both sprint sheets and are counted in each; in Sprint 1, DT-006 – DT-011 repeat TD-001 – TD-006 under Development Test IDs, so the REG-003 and REG-005 defects are counted under both.)

**5 defects found** (REG-001 – REG-005), all reported to their owning stories and not fixed in this story. See [Defects](#defects).

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — Sprint 1 regression tested | ✅ All Sprint 1 test cases automated, except SCRUM-8/17 AI hand-off (deferred) |
| AC2 — Sprint 2 regression tested | ✅ All Sprint 2 test cases automated, except SCRUM-9/19 AI extraction (deferred) |
| AC3 — previously passed cases still pass | ✅ Both suites run on every PR (`.github/workflows/ci-cd.yml`); 5 regressions found, below |
| AC4 — defects emailed | ✅ A failing PR check emails the pusher (GitHub default notifications) |
| AC5 — re-run after defects fixed | ⏳ Waiting on fixes for REG-001 – REG-005 by their owners; re-run then |

## How the regression suite works

- **Backend (pytest):** existing unit/API tests plus new regression files, tagged by sprint. Run `pytest -m sprint1` or `pytest -m sprint2`.
- **Frontend (Playwright):** the real React app against a mocked backend (`frontend/e2e/support/mockBackend.ts`) — no FastAPI, Supabase or AI calls. Run `npm run test:e2e:sprint1` / `test:e2e:sprint2`, or one case with `npx playwright test --grep @DI-008`.
- **Real test data:** two cases captured from Supabase before its reset — GLDB-02 Anchorpoint (complete case) and GLDB-16 Harbor Light (C014 set) — in `backend/tests/fixtures/regression/`, shared by both suites. Refresh with `python scripts/capture_regression_fixture.py <GLDB-id> <name>`.
- **Mock documents:** generated for real from `doc_gen_scripts/v16_document_mastersheet.xlsx` (C001 and C064) into a temp folder on each run.
- **Known defects** are marked as expected failures (`test.fail` / `xfail(strict=True)`), so CI stays green but each one flags itself as soon as it's fixed.
- The original runs used cases C060, C064 (Supabase) and specific files (C007 payslip, NRIC_6554, Case 45/68 images) that no longer exist after the database reset; equivalent real data from GLDB-02/GLDB-16 is used instead.

## Results by test case

✅ pass · ❌ defect (see Defects) · ⏸ deferred until the LangGraph pipeline is in place

### Sprint 1

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| TD-001 / DT-010 | SCRUM-15 | Unrecognised template matches no defined type | `test_regression_document_generation.py::test_unrecognised_templates_match_no_defined_document_type` | ✅ |
| TD-002 / DT-011 | SCRUM-15 | Unrecognised documents labelled in masterlist | `…::test_unrecognised_documents_are_listed_under_the_unrecognised_sheets` | ✅ |
| TD-003 / DT-006 | SCRUM-13 | Every generated document logged in masterlist | `…::test_every_generated_document_is_logged_in_the_masterlist` ✅ · `…::test_masterlist_check_script_runs_against_the_committed_mastersheet` | ❌ REG-003 |
| TD-004 / DT-007 | SCRUM-13 | Each case record has required company/applicant info | `…::test_every_case_record_has_the_required_company_and_applicant_details` | ✅ |
| TD-005 / DT-008 | SCRUM-13 | Each document linked to the correct case | `…::test_every_document_row_is_linked_to_an_existing_case` | ✅ |
| TD-006 / DT-009 | SCRUM-13 | Fraud details recorded, or none | `…::test_every_document_row_records_its_fraud_details_or_none` ✅ · `…::test_known_untagged_fraud_rows_have_been_tagged` | ❌ REG-005 |
| DT-001 | SCRUM-10 | A template exists for each of the 11 types | `…::test_a_template_produces_each_of_the_11_document_types` (×11) | ✅ |
| DT-002 | SCRUM-10 | Templates accept synthetic values | `…::test_templates_contain_the_supplied_synthetic_values` | ✅ |
| DT-003 | SCRUM-10 | Same inputs → same output | `…::test_the_same_inputs_produce_the_same_documents` ✅ (12 of 13) · `…::test_certificate_of_incorporation_is_reproducible` | ❌ REG-004 |
| DT-004 | SCRUM-12 | ≥36 complete sets across 11 types | `…::test_at_least_36_complete_sets_across_all_11_document_types` | ✅ |
| DT-005 | SCRUM-12 | Documents in a set consistent with mastersheet | `…::test_every_company_document_in_a_set_matches_the_mastersheet` (×6), `…::test_personal_documents_in_a_set_match_the_applicant` | ✅ |
| DT-012 | SCRUM-14 | Each defect type injectable on demand | `test_fraud_injections.py::test_all_defect_types_inject_on_each_format`, `::test_every_defect_type_has_an_injector_and_changed_keys` | ✅ |
| DT-013 | SCRUM-14 | Injection targets only the specified field | `test_fraud_injections.py` (changed-keys checks), `test_injection_router.py` | ✅ |
| DI-001 – DI-016 | SCRUM-6 | Upload page: components, drag-and-drop, Browse, formats, 25MB limit, per-file status, retry, completion feedback, full workflow | `e2e/specs/upload.spec.ts` (one test per ID); backend `test_documents.py` upload tests, `test_ingestion.py::test_ingest_rejects_unsupported_type` | ✅ all 16 |
| DI-017 | SCRUM-8 | Clean files classified correctly | — | ⏸ AI |
| MA-001 | SCRUM-17 | Stored document queued | `test_queue_service.py::test_enqueue_marks_queued_and_puts_document_id_on_queue` | ✅ |
| MA-002 | SCRUM-17 | Dequeued and handed to Classifier Agent within 1s | — | ⏸ AI |

### Sprint 2

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| TD-001 – TD-006 | SCRUM-13/15 | (same as Sprint 1) | (same as Sprint 1) | ✅ ×4, ❌ REG-003, REG-005 |
| DT-014 – DT-019 | SCRUM-121 | Fraud injection on PNG/JPG, chosen field/value/location, structure kept, correct format | `test_fraud_injections.py::test_all_defect_types_inject_on_each_format[png/jpg]`, `test_injectors.py`, `test_injection_router.py` | ✅ |
| DT-020 – DT-021 | SCRUM-112 | Personal SOW / Corporate POA generated | `…::test_optional_documents_are_generated_from_the_mastersheet` (×3) | ✅ |
| DT-022 | SCRUM-112 | Corporate NOA generated with its fields | `…::test_corporate_noa_is_generated_with_its_mastersheet_fields` | ✅ |
| DT-023 | SCRUM-120 | ≥15 new cases | `…::test_at_least_15_new_cases_were_added` (34 new) | ✅ |
| DT-024 | SCRUM-120 | New cases include new optional types | `…::test_new_cases_include_the_new_optional_document_types` | ✅ |
| DT-025 | SCRUM-120 | Realistic document names | `…::test_new_cases_use_realistic_submission_filenames` | ✅ |
| DT-026 | SCRUM-120 | Fraud dimensions tagged | `…::test_fraud_cases_are_tagged_with_a_known_fraud_dimension` | ✅ (C042 → REG-005) |
| DT-027 | SCRUM-115 | Generated in PDF/PNG/JPG/JPEG | `…::test_documents_are_generated_in_pdf_png_jpg_and_jpeg` | ✅ |
| DT-028 | SCRUM-115 | Formats follow the per-type assumptions | `…::test_generated_formats_follow_the_document_format_assumptions` | ✅ |
| DT-029 | SCRUM-115 | Formats proceed through ingestion | `upload.spec.ts` DT-029; `test_ingestion.py` native PDF / scanned PDF / image | ✅ (classification step ⏸ AI) |
| DT-030 | SCRUM-115 | Type and file info kept after ingestion | — | ⏸ AI (type comes from classification) |
| DT-031 | SCRUM-115 | DOCX/XLSX/TXT rejected | `upload.spec.ts` DT-031 | ✅ |
| CM-001 – CM-004 | SCRUM-78 | Search by name/ID, only matches shown, select, filter by status | `case-management.spec.ts`; `test_cases.py::test_list_cases` | ✅ |
| CM-005 – CM-009 | SCRUM-83 | Open details, selected case only, risk summary, flagged anomalies, audit trail | `case-management.spec.ts` | ✅ (CM-007/009: section present with its placeholder — current intended behaviour) |
| CM-010 – CM-013 | SCRUM-96 | Create case, unique ID, stored and reopened, appears in queue | `case-management.spec.ts`; `test_cases.py` | ✅ (CM-011: uniqueness is a DB trigger; UI-level check only) |
| DI-018 | SCRUM-9 | Type-specific fields extracted | — | ⏸ AI |
| DI-019 | SCRUM-9 | Extraction on unrecognised documents | — | ⏸ AI |
| DI-020 | SCRUM-9 | Per-field confidence displayed | `document-review.spec.ts` DI-020 | ✅ |
| DI-021 | SCRUM-9 | Missing field shown as "—", document not failed | `document-review.spec.ts` DI-021 | ✅ |
| DI-022 | SCRUM-9 | Extraction works for PNG/JPG/scanned PDF | — | ⏸ AI |
| DI-023 | SCRUM-20 | Missing required field flagged and identified | `document-review.spec.ts` DI-023 | ✅ |
| DI-024 | SCRUM-20 | Officer fills flagged field, value kept | `document-review.spec.ts` DI-024 | ✅ |
| DI-025 | SCRUM-20 | Completed document can proceed | `document-review.spec.ts` DI-025 ✅ · legitimately-empty field variant | ❌ REG-002 |
| DI-026 | SCRUM-20 | Can't proceed with a field missing | `document-review.spec.ts` DI-026 save ✅ · approve | ❌ REG-001 |
| DI-027 – DI-029 | SCRUM-9 | Incumbency / NRIC front+back extraction, confidence calculation | — | ⏸ AI |
| DI-030 | SCRUM-9 | Array fields displayed | `document-review.spec.ts` DI-030 | ✅ |
| DI-031 – DI-034 | SCRUM-114 | Required vs optional, optional not blocking, optional mapped, overall status | `checklist.spec.ts`; `test_regression_checklist.py` | ✅ |
| DI-035 – DI-038 | SCRUM-118 | Type + requirement identified, NRIC → ID + POA, recorded against case, not flagged missing | `checklist.spec.ts`; `test_regression_checklist.py`; `test_mapper.py` | ✅ |
| MA-003 – MA-004 | SCRUM-19 | Extraction auto-triggers after classification | — | ⏸ AI |

## Defects

All reported, not fixed in this story (owners to fix; AC5 re-run afterwards). Each is pinned by a test that is marked as an expected failure and will flag itself once fixed — delete the marker then.

| ID | Severity | Test case | Owner story | Defect | Where |
|---|---|---|---|---|---|
| REG-001 | High | DI-026 | SCRUM-20 / SCRUM-26 | A document with a flagged, empty required field can still be approved: **✓ Approve Document** is only disabled while processing, and `POST /documents/{id}/approve` only checks `status == analyzed`. Passed in Sprint 2 only because approve wasn't implemented yet. | `DocumentDetailsPage.tsx` (approve button), `routers/documents.py` `approve_document` |
| REG-002 | Medium | DI-025 | SCRUM-20 | **Save fields** requires every field to be non-empty, including fields extraction was confident are genuinely absent (e.g. Company Former Name on a company that never had one). Fixing the flagged UEN alone can't be saved; the officer must type a placeholder. | `DocumentDetailsPage.tsx` `handleSaveFields` |
| REG-003 | Low (dev tooling) | TD-003 / DT-006 | SCRUM-13 | `scripts/verify_documents.py` (and `_inspect_mastersheet.py`) point at `v9_document_mastersheet.xlsx`, which isn't in the repo (only v16) — the masterlist check crashes with `FileNotFoundError`. Its expected-filename logic is also out of date (expects `C001-P1_NRIC`, generator writes `C001-P1_NRICPassport`). | `scripts/verify_documents.py` |
| REG-004 | Low (dev tooling) | DT-003 | SCRUM-10 | The Certificate of Incorporation isn't reproducible: its receipt number uses unseeded `random`, so it changes on every run. The other 12 documents per case are identical. | `doc_gen_scripts/certificate_of_incorporation/coi_generator.py` `_generate_receipt_no` |
| REG-005 | Low (test data) | TD-006 / DT-009, DT-026 | SCRUM-13 / SCRUM-120 | Mastersheet row `BizFile_994116.pdf` (C042, Meridian Family Clinic, Cert of Incorporation) is marked Fraud Injected = **Y** with no Detection Dimension or Fraud Description — C042's only fraud row. | `doc_gen_scripts/v16_document_mastersheet.xlsx` |

### Observations (not defects)

- **Save confirmation disappears (DI-024):** after a successful save, the manual-review section closes as soon as nothing is flagged, taking the "Changes saved" message with it. The save works; the officer just gets no visible confirmation.
- **CM-011 unique case ID:** uniqueness comes from the database trigger (`migrations/001_add_greenlink_case_id_generation.sql`), which the mocked frontend suite can't exercise.
- **`pyproject.toml` was missing `openpyxl` and `pandas`** (present in `requirements.txt`), so CI couldn't import the document generators; added as part of this story.

## Deferred: AI pipeline test cases

These test the classification/extraction AI itself. They're deferred until the migration from Dify to LangGraph (`langgraph-test` branch) is merged, and should be written against the LangGraph node boundaries (classify output, `extract.run_extraction`) rather than Dify's API, so they survive the migration:

DI-017 (SCRUM-8) · MA-002 (SCRUM-17) · DI-018, DI-019, DI-022, DI-027, DI-028, DI-029 (SCRUM-9) · MA-003, MA-004 (SCRUM-19) · DT-030 (SCRUM-115) · the classification half of DT-029.

## Re-running (AC5)

After REG-001 – REG-005 are fixed:

```bash
cd backend && pytest -m "sprint1 or sprint2"
cd frontend && npm run test:e2e
```

Each fixed defect's expected-failure test will then report "passed unexpectedly" — remove its `test.fail(...)` / `xfail(...)` marker (and, for REG-004/REG-005, its entry in `REG_004_NONDETERMINISTIC_FILES` / `REG_005_UNTAGGED_FRAUD_ROWS`), re-run until both suites are fully green, and record that run here.
