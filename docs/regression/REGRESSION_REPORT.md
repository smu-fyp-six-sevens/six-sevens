# Regression Report — Sprint 1 & Sprint 2

**Story:** SCRUM-206 Conduct Regression Testing
**Branch:** `SCRUM-206-Conduct-Regression-Testing`, up to date with `main` at `6ab9dfd`
**Run date:** 2026-10-01
**Source test cases:** `Test Cases - Sprint 1.xlsx`, `Test Cases - Sprint 2.xlsx`

## Summary

| Manual test cases | Sprint 1 | Sprint 2 | Total |
|---|---|---|---|
| In the sprint sheets | 38 | 60 | 98 |
| Out of scope (test-data tooling, checked manually) | 19 | 21 | 40 |
| **In scope** | **19** | **39** | **58** |
| Automated and passing | 19 | 38 | 57 |
| Automated, defect found | 0 | 1 | 1 |

Counted per test case ID. Out of scope: document generation, the mastersheet and fraud injection (TD-001 – TD-006, DT-001 – DT-028). These were done manually to build the test document sets the system is tested with; they aren't system features, so they aren't regression-tested.

| Suite | Tests | Passed | Known defects (expected failures) |
|---|---|---|---|
| Backend — pytest (all) | 876 | 873 | 3 (REG-003) |
| └ tagged `sprint1` / `sprint2` | 19 / 83 | 99 | 3 (REG-003) |
| Frontend — Playwright | 50 | 48 | 2 (REG-002, REG-003) |

**Defects:** 2 open (REG-002, REG-003). REG-001 fixed by its owner and re-tested. REG-004 (case match, found during this regression) fixed in this story.

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — Sprint 1 regression tested | ✅ Every in-scope Sprint 1 case automated and passing |
| AC2 — Sprint 2 regression tested | ✅ Every in-scope Sprint 2 case automated, including the AI cases (mocked AI responses) |
| AC3 — previously passed cases still pass | ✅ Both suites run on every PR; 2 open regressions, below |
| AC4 — defects emailed | ✅ A failing PR check emails whoever pushed it (GitHub notifications) |
| AC5 — re-run after defects fixed | ⏳ REG-001 fixed and re-tested (its expected-failure test flagged the fix, then was turned into a normal test). Waiting on REG-002 and REG-003 |

## How the regression suite works

- **Frontend (Playwright):** the real React app against a mocked backend (`frontend/e2e/support/mockBackend.ts`). No FastAPI, Supabase or AI calls.
- **Backend (pytest):** existing tests plus `tests/test_regression_*.py`, tagged by sprint (`pytest -m sprint1` / `-m sprint2`).
- **AI pipeline (new):** `tests/test_regression_ai_pipeline.py` runs the **real LangGraph pipeline** end to end — queue → ingest → rule classifier → classify agent → record classification → extract → save fields → analyzed. Only Claude's replies are scripted (a fake model standing in for `build_chat_anthropic`); extraction replies reuse real extraction results captured from Supabase. Whether Claude itself still gives the right answers is checked by the **manual live run at the end of each sprint**.
- **No real AI calls in tests:** `conftest.py` blocks any real Claude request and clears the API key, so no test run can spend credits even with a real key in `.env`.
- **Real test data:** GLDB-02 Anchorpoint and GLDB-16 Harbor Light, captured from Supabase into `backend/tests/fixtures/regression/`.
- **Known defects** are expected failures (`test.fail` / `xfail(strict=True)`): CI stays green, and each flags itself once fixed.

## Results by test case

✅ pass · ❌ defect

### Sprint 1

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| DI-001 – DI-016 | SCRUM-6 | Upload page | `e2e/specs/upload.spec.ts`; `test_regression_upload.py`; `test_documents.py`, `test_ingestion.py` | ✅ all 16 |
| DI-017 | SCRUM-8 | Clean files classified with type, confidence and routing | `test_regression_ai_pipeline.py` (C001 set, unrecognised, below threshold, tool use) | ✅ |
| MA-001 | SCRUM-17 | Stored document queued | `test_queue_service.py` | ✅ |
| MA-002 | SCRUM-17 | Handed to the classifier within 1s, status Processing | `test_regression_ai_pipeline.py` | ✅ |

### Sprint 2

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| DT-029 | SCRUM-115 | PDF, PNG, JPG and JPEG go through ingestion | `upload.spec.ts`; `test_ingestion.py`; `test_regression_ai_pipeline.py` | ✅ |
| DT-030 | SCRUM-115 | Type and file info kept after ingestion | `test_regression_ai_pipeline.py` | ✅ |
| DT-031 | SCRUM-115 | DOCX/XLSX/TXT rejected | `upload.spec.ts` | ✅ |
| CM-001 – CM-013 | SCRUM-78/83/96 | View, open, create cases | `case-management.spec.ts`; `test_cases.py` | ✅ (CM-007/009 placeholder = intended; CM-011 UI-level) |
| DI-018 | SCRUM-9 | Type-specific fields extracted | `test_regression_ai_pipeline.py` (payslip, FATCA, credit report) | ✅ |
| DI-019 | SCRUM-9 | Unrecognised documents still extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-020, DI-021 | SCRUM-9 | Field confidence shown; missing field "—" | `document-review.spec.ts` | ✅ |
| DI-022 | SCRUM-9 | PNG/JPG/scanned PDF extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-023, DI-024 | SCRUM-20 | Missing field flagged; officer can fix it | `document-review.spec.ts` | ✅ |
| DI-025 | SCRUM-20 | Completed document can proceed | `document-review.spec.ts`; `test_regression_approval.py` | ❌ REG-002, REG-003 |
| DI-026 | SCRUM-20 | Can't proceed with a field missing | `document-review.spec.ts`; `test_regression_approval.py` | ✅ (REG-001 fixed) |
| DI-027 | SCRUM-9 | Incumbency classified and officers saved | `test_regression_ai_pipeline.py` | ✅ |
| DI-028 | SCRUM-9 | NRIC front and back extracted | `test_regression_ai_pipeline.py` | ✅ |
| DI-029 | SCRUM-9 | Document confidence = average of field confidences | `test_regression_ai_pipeline.py` | ✅ (the sheet's scenario says "lowest"; its expected result and the code say average) |
| DI-030 | SCRUM-9 | Array fields displayed | `document-review.spec.ts` | ✅ |
| DI-031 – DI-038 | SCRUM-114/118 | Checklist and mapping | `checklist.spec.ts`; `test_regression_checklist.py`; `test_mapper.py` | ✅ |
| MA-003, MA-004 | SCRUM-19 | Extraction auto-triggers (also for unrecognised); reclassify on disagreement | `test_regression_ai_pipeline.py` | ✅ |

## Defects

| ID | Severity | Status | What goes wrong | Example | Test case | Owner |
|---|---|---|---|---|---|---|
| REG-001 | High | **Fixed by owner** (jianing0825, SCRUM-26), re-tested and passing | A document could be approved even when a required field was empty. | A certificate with no UEN could still be approved. | DI-026 | SCRUM-20 / SCRUM-26 |
| REG-002 | Medium | Open | A correction can't be saved if **any** field is empty, even one that is correctly blank. | A company that never had a former name: the officer fixes the UEN, but can't save until they type something into "Former Name". | DI-025 | SCRUM-20 |
| REG-003 | High | Open (new, caused by REG-001's fix) | Some **complete** documents can never be approved, because the new check mistakes "nothing to report" for "missing information". | **Credit report:** a person with no defaults is blocked because "Defaults" is empty. **Personal wealth statement:** a personal investment account is blocked because it has no company name or UEN, which it shouldn't have. | DI-025 | SCRUM-26 |
| REG-004 | High | **Fixed in this story** | The case-match check only recognised one person on the certificate of incumbency, so other directors and shareholders were wrongly flagged as "case mismatch". | Tan Wei Jun (shareholder, S8000000A) uploaded her NRIC and it was flagged as not on the certificate. Cause: the extraction schema on main now returns officers/shareholders with their ID under `identification_no`, which case match didn't read. Fixed in `case_match.py`; regression tests use the real certificate's fields. | (SCRUM-110 case match) | SCRUM-110 |

## Out of scope

Document generation, the mastersheet and fraud injection — TD-001 – TD-006 and DT-001 – DT-028 (SCRUM-10, 12, 13, 14, 15, 112, 115, 120, 121). These were done manually to produce the test document sets used with the system, not features of the system itself, so they aren't regression-tested. (DT-029 – DT-031 stay in scope: they test how the system handles each file format.)

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

After REG-002 and REG-003 are fixed, each one's expected-failure test reports "passed unexpectedly": remove its marker, re-run until both suites are fully green, and record that run here (AC5).
