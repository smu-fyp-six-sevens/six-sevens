# Regression Report — Sprint 5

**Story:** SCRUM-323 Sprint 5 Regression Testing
**Branch:** `SCRUM-323-Sprint-5-Regression-Testing`, from `main` at `f7251e7`
**Run date:** 2026-11-02
**Source test cases:** `Test Cases - Sprint 5.xlsx`

## Summary

| Manual test cases | Count |
|---|---|
| In the sprint sheet (Sprint 5 stories) | 99 |
| Out of scope | 6 |
| **In scope** | **93** |
| Automated and passing | 93 |
| Automated, live defect since fixed | 2 (AE-014, AE-015) |

The 93 in-scope cases come from four tabs: Case Management CM-037 – CM-092 (56), Multi-Agent MA-028, MA-029, MA-031, MA-032, MA-039 (5), Cross Doc Validation CD-022 – CD-037 (16) and Agentic Evaluation AE-001 – AE-016 (16). Out of scope: TD-001 – TD-006 (test-data tooling, checked manually, as in Sprints 1 – 4). Some rows hold only an ID, with no scenario, steps or result: RS-009 – RS-024, MA-040 – MA-043, FD-029 – FD-032, and REG-014 – REG-017 on the **Testing** tab. They aren't counted, and are left until they're written.

| Suite | Tests | Passed | Known defects (expected failures) |
|---|---|---|---|
| Backend — pytest (all) | 2743 | 2741 (+2 skipped) | 0 |
| └ tagged `sprint5` | 313 | 313 | 0 |
| └ tagged `sprint1` – `sprint4` (re-run) | 1258 | 1256 (+2 skipped) | 0 |
| Frontend — Playwright (all) | 226 | 226 | 1 (REG-014) |
| └ tagged `@sprint5` | 41 | 41 | 0 |
| └ tagged `@sprint1` – `@sprint4` (re-run) | 136 | 136 | 0 |

**Sprints 1 – 4 still pass.**

**Defects:** none new in this regression. AE-014 and AE-015 failed in the live run and were fixed and passed on re-test; the suite keeps both fixed. REG-014 (Sprint 4, a race in the detection panel) is still open. Its test is now an expected failure, so it can't fail the build at random and will flag the fix when it lands.

**For a decision:** four case-overview cases (CM-041, CM-046, CM-048 and CM-063) describe a layout the app doesn't have. See [Observations](#observations-not-defects).

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — all completed Sprint 5 functionalities regression tested | ✅ All 93 in-scope cases automated (backend and/or Playwright), AI steps scripted |
| AC2 — previously passed Sprint 5 cases still pass | ✅ Every case that passed in the live run passes in the suite; both suites run on every PR |
| AC3 — defects documented and tracked | ✅ AE-014, AE-015 and REG-014 below, with test case, example and owner |
| AC4 — regression re-run after defects are fixed | ✅ AE-014, AE-015 re-tested and passing; Sprints 1 – 4 re-run. ⏳ REG-014 waiting on a fix |

## How the Sprint 5 suite works

Same approach as Sprints 1 – 4: real endpoints, workers and LangGraph graphs, with only the database in memory and Claude's replies scripted.

- **Cross-page consistency:** `tests/test_regression_page_consistency.py` reuses Sprint 3's detection harness (real graph, real tools, scripted specialists). It runs the statements that `test_page_consistency.py` generates, with each forgery CD-022 – CD-037 describes: a page missing from the numbering, a repeated page number, pages out of order, a different page total, a changed UEN, a carried-forward balance that doesn't match, a page of a different size or orientation, a page in other fonts, a terms-and-conditions last page, and a duplicated page.
- **Content guard (new):** `tests/test_regression_content_guard.py` builds each payslip AE-001 – AE-007 describes: an instruction in white text, in the PDF's metadata, drawn into an image, or written with look-alike letters; ordinary hidden wording; and a genuine payslip. It processes each the way the doc-worker does, with Sprint 2's pipeline harness. Unlike Sprint 2, the stored file is the real PDF, and text extraction, image OCR and the guard's scan all run on its bytes. exiftool's reading of the document info is read here with PyMuPDF, so the suite doesn't need exiftool installed.
- **Prompt injection in detection:** `tests/test_regression_prompt_injection.py` scans the same payslips with the real guard and runs them through the detection graph. The scripted specialists try to clear every document: they dismiss every finding and run nothing. The suite checks that the guard's findings can't be dismissed or rated lower, that they count towards the case risk as tampering, and that a missing or failed guard result is never read as clean.
- **Observability:** `tests/test_regression_observability.py` reads the LangGraph trace of a detection run on Sprint 3's C028 credit report. For MA-029 the real detection worker runs, the officer cancels it mid-review, and the run is retried through the real endpoint. For MA-039 the trace is saved through the real write path into an in-memory table and read back after the process's memory is cleared.
- **Case report:** `tests/test_regression_case_report.py` downloads the PDF from the real `GET /cases/{id}/report.pdf` on the captured Anchorpoint case (GLDB-02). Its summary is built by the real `case_service.build_summary`, and the suite reads the PDF's text.
- **Detection accuracy:** `tests/test_regression_detection_metrics.py` puts the sheet's own reviews through the real `GET /detection-metrics`: 10 spelling findings with 4 marked wrong, 2 font findings, a numbers check on target, and a metadata check with nothing real to measure recall against.
- **Audit trail:** `tests/test_regression_audit_trail.py` works through an officer's steps on the Anchorpoint case, each through the real endpoint or worker handler, and reads the events they record. The steps are: upload two utility bills, classify one, confirm its type, add a field, edit one, cancel the second bill, approve the first, and add a note.
- **Shared fixtures (new):** the last two write `detection-metrics-sprint5.json` and `audit-trail-sprint5.json`. The Playwright Accuracy tab and Audit Trail tests render these, so the UI is tested on what the backend produces, not on hand-built data. Each backend test fails if the code drifts from its fixture (regenerate with `REGENERATE_REGRESSION_FIXTURES=1`). The report download's filename is checked the same way: the backend test pins it, and the mock serves the same name.
- **Frontend:** a Sprint 5 block in each of `case-management.spec.ts` (case overview, report download), `dashboard.spec.ts` (dashboard and Accuracy tab), `audit-trail.spec.ts` and `case-status.spec.ts`. Five existing tests are tagged `@sprint5` too: four case-stage tests in `case-status.spec.ts` (CM-086, CM-087, CM-089 – CM-091) and the retry-detection test in `detection.spec.ts` (MA-029). Scored cases on the dashboard carry risks the backend computed (`case-risk-sprint4.json`). `mockBackend.ts` gained `detectionMetrics`, the report PDF, and the audit entry finalising records, as `routers/cases.py` does.
- **No real AI calls:** whether Claude still picks the right tools and ratings on the real documents is checked by the **manual live run at the end of each sprint**.

Existing tests that already cover Sprint 5 cases are tagged `sprint5` centrally in `conftest.py`: `test_page_consistency.py`, `test_content_guard.py`, `test_prompt_injection.py`, `test_finding_reviews.py`, `test_audit_trail.py`, `test_case_report.py`, `test_case_stage.py`, and the cancel-and-retry tests in `test_events.py` and `test_detection.py`.

## Results by test case

✅ pass · ❌ defect · ⚠️ passes, but the case describes something else (see observations)

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| CM-037 – CM-040 | SCRUM-299 | Company name shown; a scored case's level and score; "Not scored yet" before scoring; a long name cut with an ellipsis, in full on hover, without widening the page | `case-management.spec.ts` | ✅ |
| CM-041 | SCRUM-299 | Documents organised by status | `case-management.spec.ts` | ⚠️ |
| CM-042 | SCRUM-299 | A document moves to Approved Documents, or Rejected, when its status changes | `case-management.spec.ts` | ✅ |
| CM-043 – CM-045 | SCRUM-299 | Each document's name, type and status; "Extracting…" while processing; selecting a document opens it | `case-management.spec.ts` | ✅ |
| CM-046 – CM-048 | SCRUM-299 | An empty section says so; "No approved documents yet."; a case with no documents | `case-management.spec.ts` | ✅ CM-047 · ⚠️ CM-046, CM-048 |
| CM-049 | SCRUM-299 | The open section is kept after a reload | `case-management.spec.ts` | ✅ |
| CM-050 – CM-052 | SCRUM-297 | Cases in view; counts by risk level; each level drawn as a bar of the whole | `dashboard.spec.ts` | ✅ |
| CM-053 – CM-059 | SCRUM-297 | Counts and chart follow the status filter; an unscored case shows a dash and why; a row opens its case; the case queue opens; a newly scored or deleted case is reflected on refresh; empty state when nothing matches | `dashboard.spec.ts` | ✅ |
| CM-060 – CM-062 | SCRUM-297 | Spelling: 4 wrong of 10, precision 60%; "Below target" in red, "On target" for a check meeting both; "Few reviews" under 5 | `test_regression_detection_metrics.py`; `dashboard.spec.ts`; `test_finding_reviews.py` | ✅ |
| CM-063 | SCRUM-297 | The documents behind a flagged check | `test_regression_detection_metrics.py` | ⚠️ |
| CM-064 – CM-074 | SCRUM-298 | The case's own trail, oldest first: upload, classification (system, started by the officer), type confirmed, field added and edited with values, cancellation, note and approval by the officer, edits in time order | `test_regression_audit_trail.py`; `audit-trail.spec.ts`; `test_audit_trail.py` | ✅ |
| CM-075 – CM-085 | SCRUM-82 | PDF downloaded, named by case; only what the stage has; the latest stage after analysis; identical repeats apart from the time; no documents; a missing required document not shown as received; latest details and notes; a new upload; long names; special characters | `test_regression_case_report.py`; `case-management.spec.ts`; `test_case_report.py` | ✅ |
| CM-086 – CM-092 | SCRUM-80 | Current stage shown; Finalised after finalising, locked; the change recorded by the officer; the queue updated; no finalising before analysis; the same stage on the case page, queue and dashboard | `case-status.spec.ts`; `test_case_stage.py` | ✅ |
| MA-028, MA-031, MA-032 | SCRUM-55 | Each specialist's tool calls stored in order with times and arguments; the run's results; why each check ran and each finding was kept or dismissed | `test_regression_observability.py` | ✅ |
| MA-029 | SCRUM-55 | A cancelled run keeps its tool calls and "Cancelled by loan officer", and can be run again | `test_regression_observability.py`; `detection.spec.ts`; `test_events.py`; `test_detection.py` | ✅, see note |
| MA-039 | SCRUM-55 | The trace is read back from storage after a restart | `test_regression_observability.py` | ✅ |
| CD-022 – CD-024 | SCRUM-282 | Single page skipped; no readable text → unable to assess; two or more pages checked | `test_regression_page_consistency.py`; `test_page_consistency.py` | ✅, see note |
| CD-025 – CD-030 | SCRUM-282 | Missing, repeated and out-of-order page numbers; totals that disagree with each other or the real page count flagged; consistent numbering clean | `test_regression_page_consistency.py` | ✅ |
| CD-031 – CD-034 | SCRUM-282 | A changed UEN; a carried-forward balance that doesn't match; a page of another size or orientation | `test_regression_page_consistency.py` | ✅ |
| CD-035 – CD-037 | SCRUM-282 | A page in entirely different fonts (low); a terms-and-conditions last page left out of the font check; a duplicated page | `test_regression_page_consistency.py` | ✅ (CD-035 failed live: test data, see note) |
| AE-001 – AE-005 | SCRUM-288 | An instruction in white text, metadata, an image or look-alike letters stops the document at upload, says where, and no model reads it; a retry blocks it again | `test_regression_content_guard.py`; `test_content_guard.py` | ✅, see note |
| AE-006, AE-007 | SCRUM-288 | A genuine payslip goes on as usual; ordinary hidden wording isn't blocked but goes to manual review | `test_regression_content_guard.py` | ✅ |
| AE-008 – AE-012 | SCRUM-288 | The finding can't be dismissed or lowered; it counts as tampering (high, 8 points); a genuine payslip has none; the check runs first in every run; a missing or failed guard result is checked from the file or is unable to assess, never clean | `test_regression_prompt_injection.py`; `test_prompt_injection.py` | ✅ |
| AE-013 | SCRUM-297 | Precision and recall per check and overall (59% and 93% for the sheet's reviews) | `test_regression_detection_metrics.py`; `dashboard.spec.ts` | ✅ |
| AE-014, AE-015 | SCRUM-297 | No real issues → recall "Not measured", not 0% or 100%; a check under target flagged, one meeting both not | `test_regression_detection_metrics.py`; `dashboard.spec.ts` | ✅ (failed live, fixed, re-tested) |
| AE-016 | SCRUM-283 | Each specialist's confidence and the run's findings saved with the completed run | `test_regression_observability.py` | ✅ |

## Defects

| ID | Severity | Status | What goes wrong | Example | Test case | Owner |
|---|---|---|---|---|---|---|
| AE-014 (live) | Low | Fixed and re-tested | A check with no real issue to measure recall against had no recall shown: the report showed only the checks present. It now reads "Not measured", with the reason on hover. | Brightwave Analytics (UAT 2 Run 1) | AE-014 | SCRUM-297 |
| AE-015 (live) | Medium | Fixed and re-tested | A check under its precision or recall target wasn't flagged. It now shows "Below target" with the rate in red. | Brightwave Analytics (UAT 2 Run 1) | AE-015 | SCRUM-297 |
| REG-014 (Sprint 4) | Low | Open | After an officer cancels detection, the panel can stay on "Cancelling…" for good. `useDetection.ts` sets its cancel-pending flag when the cancel request answers, and clears it when the document leaves `detecting`. If the worker's snapshot reaches the page first, the flag is cleared and then set again, and nothing clears it after that. A retried run then shows a disabled "Cancelling…" instead of "Cancel detection". | Cancel detection, the snapshot arrives first, then Retry detection | Cancel/Retry detection (SCRUM-299) | `frontend/src/pages/document-details/useDetection.ts` (Bulbasaurabh) |

### Re-testing (AC4)

- **AE-014, AE-015:** passed on the live re-test. `test_a_check_with_no_real_issues_has_no_recall_rather_than_zero_or_one_hundred` and `test_a_check_under_its_target_is_flagged_and_one_meeting_both_is_not` keep the backend side fixed. `AE-014 …Not measured…` and `CM-061 / AE-015 …shown in red…` in `dashboard.spec.ts` keep the screen fixed.
- **REG-014:** not fixed. The cancel-and-retry test in `detection.spec.ts` used to fail about 1 run in 5, depending on whether the cancel reply or the worker's snapshot arrived first. It is now three tests:
  - the cancel ends in "Detection failed" with a retry;
  - a retry after a cancel starts again and can be cancelled (the usual order);
  - `after a cancel whose reply arrives late, a retried run can still be cancelled`. This one holds the cancel reply until the snapshot has arrived (`cancelGate` in `mockBackend.ts`), so it reproduces the defect every time, and it is marked `test.fail()`.

  All three passed every one of 10 repeated runs. When the fix lands, the third test will start passing, and Playwright reports that as a failure: remove the `test.fail()` line then.
- **Sprints 1 – 4:** all re-run and passing (backend 1256 + 2 skipped, Playwright 136).

## Observations (not defects)

- **CM-041, CM-046, CM-048 — status tabs (needs a decision):** these cases expect separate Manual Review and Rejected *tabs*, each with an empty state. The app has two sections, Documents and Approved Documents. Documents for manual review and rejected documents are cards inside Documents, and each card is shown only while it has documents. The suite tests what the app does: documents move between the sections and cards, Approved Documents has its empty state, and no empty Rejected card is shown. Either the cases should be reworded to match, or the tabs and their empty states need building (SCRUM-299, `UploadPage.tsx` / `caseSections.ts`).
- **CM-063 — documents behind a flagged check (needs a decision):** the case expects the Accuracy tab to list, next to a flagged check, the documents whose findings were marked wrong. The dashboard shows only how many documents were reviewed. Each document's own results per check are on the case's Risk section (`GET /cases/{id}/finding-reviews`), and the suite tests that: the bank statement's spelling is 4 wrong (0%) and the payslip's is 6 correct (100%). The live run passed this case, so it's worth checking what was looked at. Either the case should point to the case page, or the Accuracy tab needs the document list (SCRUM-297, `DetectionAccuracyPanel.tsx`).
- **CD-035 (failed live):** in the live document, page 3 had no amounts on it. The furniture rule keeps a first or last page with no amounts (a cover or terms page) out of the font comparison, which is what CD-036 wants. With page 3 holding transactions in other fonts, the suite gets the expected low-severity PAGE.SOURCE finding on page 3. The test document should get transactions on its odd page.
- **CD-023 (not tested live):** "can't approve a document with no fields". A scan whose text can be read is OCR'd and checked, so this case needs a scan with no readable text at all, and such a document can't be approved, so detection never runs on it from the UI. The suite runs detection on blank scanned pages directly and gets "No text could be read from any page" (unable to assess).
- **AE-001 (live):** the live result is a FLAGGED detection verdict, not a document stopped at upload. That run predates the content guard blocking instructions at ingest. The suite checks the current rule: Failed, "Blocked by the content guard: hidden text on page 1…", risk high, and no model call.
- **AE-012 (not tested live):** covered by the suite in both forms the case allows. With no guard result saved, the check scans the stored file. With a guard that couldn't run and no file, the result is unable to assess. Neither is clean.
- **MA-029:** the live note says the run was "broken after that". In the suite, a run cancelled mid-review keeps its tool calls and the error, goes back to approved, and can be retried: the second run finishes flagged. The retry works on screen too (`Retry detection after a cancelled run…` in `detection.spec.ts`). If what broke was the panel getting stuck on "Cancelling…", that is REG-014.
- **Sheet tidy-up:** the **Testing** tab reuses REG-014 – REG-017 as placeholder IDs, while REG-014 is already the Sprint 4 defect above. It's worth renumbering them when they're written.

## Running it

```bash
source .venv/bin/activate
cd backend && python -m pytest -m sprint5
cd ../frontend && npm run test:e2e:sprint5
```

Full regression (all sprints): `python -m pytest` and `npm run test:e2e`. Both run on every PR in CI.
