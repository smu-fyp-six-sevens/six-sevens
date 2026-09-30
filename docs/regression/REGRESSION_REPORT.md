# Regression Report — Sprint 1 & Sprint 2

**Story:** SCRUM-206 Conduct Regression Testing
**Branch:** `SCRUM-206-Conduct-Regression-Testing`, up to date with `main` at `f233702` (LangGraph merged)
**Run date:** 2026-09-30
**Source test cases:** `Test Cases - Sprint 1.xlsx`, `Test Cases - Sprint 2.xlsx`

## Summary

| Manual test cases | Sprint 1 | Sprint 2 | Total |
|---|---|---|---|
| In the sprint sheets | 38 | 60 | 98 |
| Automated and passing | 28 | 46 | 74 |
| Automated, defect found | 2 | 3 | 5 |
| Retired (tooling removed from the product) | 8 | 11 | 19 |

Counted per test case ID. TD-001 – TD-006 appear in both sheets and are counted in each; in Sprint 1, DT-006 – DT-011 repeat TD-001 – TD-006 under Development Test IDs.

| Suite | Tests | Passed | Known defects (expected failures) |
|---|---|---|---|
| Backend — pytest (all) | 878 | 877 | 1 (REG-005) |
| └ tagged `sprint1` / `sprint2` | 26 / 83 | 108 of 109 | 1 |
| Frontend — Playwright | 49 | 47 | 2 (REG-001, REG-002) |

**Defects:** 3 open (REG-001, REG-002, REG-005), 2 closed because the tool they were in was retired (REG-003, REG-004).

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — Sprint 1 regression tested | ✅ Every Sprint 1 case automated, or retired with its tooling |
| AC2 — Sprint 2 regression tested | ✅ Every Sprint 2 case automated, or retired with its tooling (AI cases now included, with mocked AI responses) |
| AC3 — previously passed cases still pass | ✅ Both suites run on every PR; 3 open regressions, below |
| AC4 — defects emailed | ✅ A failing PR check emails whoever pushed it (GitHub notifications) |
| AC5 — re-run after defects fixed | ⏳ Waiting on fixes for REG-001, REG-002, REG-005 |

## How the regression suite works

- **Frontend (Playwright):** the real React app against a mocked backend (`frontend/e2e/support/mockBackend.ts`). No FastAPI, Supabase or AI calls.
- **Backend (pytest):** existing tests plus `tests/test_regression_*.py`, tagged by sprint (`pytest -m sprint1` / `-m sprint2`).
- **AI pipeline (new):** `tests/test_regression_ai_pipeline.py` runs the **real LangGraph pipeline** end to end — queue → ingest → rule classifier → classify agent → record classification → extract → save fields → analyzed. Only Claude's replies are scripted (a fake model standing in for `build_chat_anthropic`); extraction replies reuse real extraction results captured from Supabase. Whether Claude itself still gives the right answers is checked by the **manual live run at the end of each sprint**.
- **No real AI calls in tests:** `conftest.py` blocks any real Claude request and clears the API key, so no test run can spend credits even with a real key in `.env`.
- **Real test data:** GLDB-02 Anchorpoint and GLDB-16 Harbor Light, captured from Supabase into `backend/tests/fixtures/regression/`.
- **Known defects** are expected failures (`test.fail` / `xfail(strict=True)`): CI stays green, and each flags itself once fixed.

## Results by test case

✅ pass · ❌ defect · ⊘ retired

### Sprint 1

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| TD-001 / DT-010 | SCRUM-15 | Unrecognised template matches no defined type | `test_regression_mastersheet.py` | ✅ |
| TD-002 / DT-011 | SCRUM-15 | Unrecognised documents labelled in masterlist | `test_regression_mastersheet.py` | ✅ |
| TD-003 / DT-006 | SCRUM-13 | Every generated document logged in masterlist | — | ⊘ generator and check script retired |
| TD-004 / DT-007 | SCRUM-13 | Each case record has required company/applicant info | `test_regression_mastersheet.py` | ✅ |
| TD-005 / DT-008 | SCRUM-13 | Each document linked to the correct case | `test_regression_mastersheet.py` | ✅ |
| TD-006 / DT-009 | SCRUM-13 | Fraud details recorded, or none | `test_regression_mastersheet.py` | ❌ REG-005 |
| DT-001 – DT-003, DT-005 | SCRUM-10/12 | Templates per type, synthetic values, reproducible output, sets match mastersheet | — | ⊘ generator retired |
| DT-004 | SCRUM-12 | ≥36 complete sets across 11 types | `test_regression_mastersheet.py` | ✅ |
| DT-012 – DT-013 | SCRUM-14 | Fraud injection (PDF) | — | ⊘ injection tool excluded from tests (dev-only) |
| DI-001 – DI-016 | SCRUM-6 | Upload page | `e2e/specs/upload.spec.ts`; `test_regression_upload.py`; `test_documents.py`, `test_ingestion.py` | ✅ all 16 |
| DI-017 | SCRUM-8 | Clean files classified with type, confidence and routing | `test_regression_ai_pipeline.py` (C001 set, unrecognised, below threshold, tool use) | ✅ |
| MA-001 | SCRUM-17 | Stored document queued | `test_queue_service.py` | ✅ |
| MA-002 | SCRUM-17 | Handed to the classifier within 1s, status Processing | `test_regression_ai_pipeline.py` | ✅ |

### Sprint 2

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| TD-001, 002, 004, 005 | SCRUM-13/15 | (same as Sprint 1) | `test_regression_mastersheet.py` | ✅ |
| TD-003 | SCRUM-13 | (same as Sprint 1) | — | ⊘ retired |
| TD-006 | SCRUM-13 | (same as Sprint 1) | `test_regression_mastersheet.py` | ❌ REG-005 |
| DT-014 – DT-019 | SCRUM-121 | Fraud injection on PNG/JPG | — | ⊘ injection tool excluded from tests (dev-only) |
| DT-020 – DT-022 | SCRUM-112 | Optional documents / NOA generated | — | ⊘ generator retired |
| DT-023 – DT-026 | SCRUM-120 | ≥15 new cases, optional types, realistic names, fraud tags | `test_regression_mastersheet.py` | ✅ (C042 → REG-005) |
| DT-027 | SCRUM-115 | Generated in PDF/PNG/JPG/JPEG | — | ⊘ generator retired |
| DT-028 | SCRUM-115 | Formats follow per-type assumptions | `test_regression_mastersheet.py` | ✅ |
| DT-029 | SCRUM-115 | All formats go through ingestion | `upload.spec.ts`; `test_ingestion.py`; `test_regression_ai_pipeline.py` | ✅ |
| DT-030 | SCRUM-115 | Type and file info kept after ingestion | `test_regression_ai_pipeline.py` | ✅ |
| DT-031 | SCRUM-115 | DOCX/XLSX/TXT rejected | `upload.spec.ts` | ✅ |
| CM-001 – CM-013 | SCRUM-78/83/96 | View, open, create cases | `case-management.spec.ts`; `test_cases.py` | ✅ (CM-007/009 placeholder = intended; CM-011 UI-level) |
| DI-018 | SCRUM-9 | Type-specific fields extracted | `test_regression_ai_pipeline.py` (payslip, FATCA, credit report) | ✅ |
| DI-019 | SCRUM-9 | Unrecognised documents still extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-020, DI-021 | SCRUM-9 | Field confidence shown; missing field "—" | `document-review.spec.ts` | ✅ |
| DI-022 | SCRUM-9 | PNG/JPG/scanned PDF extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-023, DI-024 | SCRUM-20 | Missing field flagged; officer can fix it | `document-review.spec.ts` | ✅ |
| DI-025 | SCRUM-20 | Completed document can proceed | `document-review.spec.ts` | ❌ REG-002 |
| DI-026 | SCRUM-20 | Can't proceed with a field missing | `document-review.spec.ts` | ❌ REG-001 |
| DI-027 | SCRUM-9 | Incumbency classified and officers saved | `test_regression_ai_pipeline.py` | ✅ |
| DI-028 | SCRUM-9 | NRIC front and back extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-029 | SCRUM-9 | Document confidence = average of field confidences | `test_regression_ai_pipeline.py` | ✅ (the sheet's scenario says "lowest"; its expected result and the code say average) |
| DI-030 | SCRUM-9 | Array fields displayed | `document-review.spec.ts` | ✅ |
| DI-031 – DI-038 | SCRUM-114/118 | Checklist and mapping | `checklist.spec.ts`; `test_regression_checklist.py`; `test_mapper.py` | ✅ |
| MA-003, MA-004 | SCRUM-19 | Extraction auto-triggers (also for unrecognised); reclassify on disagreement | `test_regression_ai_pipeline.py` | ✅ |

## Defects

| ID | Status | Severity | Test case | Owner | Defect |
|---|---|---|---|---|---|
| REG-001 | Open | High | DI-026 | SCRUM-20 / SCRUM-26 | A document with a flagged, empty required field can still be approved: the button is only disabled while processing, and the approve endpoint only checks that processing finished. |
| REG-002 | Open | Medium | DI-025 | SCRUM-20 | Save fields requires every field to be filled, including ones that are correctly empty (e.g. no former name), so a correction can't be saved without a placeholder. |
| REG-003 | Closed — retired | Low | TD-003 / DT-006 | SCRUM-13 | `scripts/verify_documents.py` pointed at a missing v9 mastersheet. The script was removed in refactor `61d730b`. |
| REG-004 | Closed — retired | Low | DT-003 | SCRUM-10 | The Certificate of Incorporation's receipt number was random on each generation. The generator was removed in refactor `61d730b`. |
| REG-005 | Open | Low (test data) | TD-006 / DT-009, DT-026 | SCRUM-13 / SCRUM-120 | Mastersheet row `BizFile_994116.pdf` (C042) is marked Fraud Injected = Y with no fraud type or description. |

## Retired test cases

Removed from the product by teammates after Sprint 2, so there is nothing left to regression-test:

- **Mock-document generator** (`scripts/generate_from_mastersheet.py`, `verify_documents.py`), deleted in refactor `61d730b`: TD-003 / DT-006, DT-001 – DT-003, DT-005, DT-020 – DT-022, DT-027. The mastersheet itself is still tested.
- **Fraud-injection tool**, kept in the repo but its tests excluded from normal runs as dev-only tooling (`conftest.py` `collect_ignore`, `9dee1c7`): DT-012 – DT-019. Its 159 tests still pass when run directly.

## Changes needed after merging `main`

- Renamed document fields (`classification_outcome` → `routing_status`, `confidence` → `classification_confidence`) in the fixtures, checklist test and mocked backend; `case_status` → `case_service`.
- New endpoints (LangGraph trace, detection report, retry, cancel) added to the mocked backend.
- Two backend upload tests removed by the refactor were replaced (`test_regression_upload.py`: file over 25MB, storage failure).
- Real-AI guard added to `conftest.py`.

## Observations (not defects)

- **Save confirmation disappears (DI-024):** the review section closes after a successful save, taking the "Changes saved" message with it.
- **CM-011 unique case ID** is a database trigger; checked at UI level only.
- **CM-007 / CM-009** show their placeholders, which is the current intended behaviour.

## Running it

```bash
source .venv/bin/activate
cd backend && python -m pytest -m "sprint1 or sprint2"
cd ../frontend && npm run test:e2e
```

After REG-001, REG-002 and REG-005 are fixed, each one's expected-failure test reports "passed unexpectedly": remove its marker, re-run until both suites are fully green, and record that run here (AC5).
