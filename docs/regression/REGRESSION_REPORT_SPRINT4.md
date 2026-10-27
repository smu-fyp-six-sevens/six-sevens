# Regression Report — Sprint 4

**Story:** SCRUM-296 Sprint 4 Regression Testing
**Branch:** `SCRUM-296-Sprint-4-Regression-Testing`, from `main` at `4542eac`
**Run date:** 2026-10-27
**Source test cases:** `Test Cases - Sprint 4.xlsx`

## Summary

| Manual test cases | Count |
|---|---|
| In the sprint sheet (Sprint 4 stories) | 60 |
| Out of scope | 6 |
| **In scope** | **54** |
| Automated and passing | 54 |
| Automated, defect found and since fixed | 1 (RS-002, REG-013) |

Out of scope: TD-001 – TD-006 (test-data tooling: the masterlist and generated documents, checked manually, same as Sprints 1 – 3). The workbook's other two tabs aren't Sprint 4 cases: **Sprint 3 – Fraud Detection Retest** (FD-001 – FD-026) is the Sprint 3 suite, re-run below, and **Testing** holds the Sprint 3 regression story's own rows (SCRUM-246).

| Suite | Tests | Passed | Known defects (expected failures) |
|---|---|---|---|
| Backend — pytest (all) | 2325 | 2323 (+2 skipped) | 0 |
| └ tagged `sprint4` | 381 | 379 (+2 skipped) | 0 |
| └ tagged `sprint1` / `sprint2` / `sprint3` (re-run) | 931 | 931 | 0 |
| Frontend — Playwright (all) | 155 | 155 | 0 |
| └ tagged `@sprint4` | 24 | 24 | 0 |
| └ tagged `@sprint1` / `@sprint2` / `@sprint3` (re-run) | 102 | 102 | 0 |

**Sprints 1 – 3 still pass.** REG-005 from the Sprint 3 report (an over-long company name accepted by the API) was fixed on `main`: its expected-failure marker is gone and the test passes. REG-006 – REG-012 were re-tested in the Sprint 4 live retest (the workbook's retest tab) and pass; FD-016 there is still "Not tested" live, but stays covered by the suite.

**Defects:** 1 new (REG-013), found by this regression, fixed in this story (`frontend/src/pages/upload/CaseRiskCard.tsx`) and re-tested. 2 more were found in the Sprint 4 live run (CD-007, CD-016), fixed by their owners and re-tested. The suite now keeps all three fixed. 1 more (REG-014, a race in the detection panel) is open for its owner.

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — all completed Sprint 4 functionalities regression tested | ✅ All 54 in-scope cases automated (backend and/or Playwright), AI steps scripted |
| AC2 — previously passed Sprint 4 cases still pass | ✅ Every case that passed in the live run passes in the suite; both suites run on every PR |
| AC3 — defects documented and tracked | ✅ REG-013 and REG-014 below, with test case, example and owner story; CD-007 and CD-016 recorded with their fixes |
| AC4 — regression re-run after defects are fixed | ✅ REG-013, CD-007, CD-016 (Sprint 4) and REG-005 – REG-012 (Sprint 3) re-tested and passing. ⏳ REG-014 waiting on a fix |

## How the Sprint 4 suite works

Same approach as Sprints 1 – 3:

- **Case analysis (new):** `tests/test_regression_case_analysis.py` runs the real `POST /cases/{id}/analysis/run` endpoint, the real case-worker handler and the real case pipeline: the code-side cross-document comparisons, scoring, the outlook and the run trace (the "Case analysis trace (debug)" panel the tester read). Only the runs table is in memory, and the cross-document agent is scripted to propose nothing further. The evidence requests and summary fall back to their no-Claude versions, since `conftest.py` blocks every real Claude call.
- **Risk score:** `tests/test_regression_case_risk.py` runs the real scoring over detection reports built from the live run's documents and findings (C002/AisyahRazak, TanAhKow, LimCG). It reproduces the live figures exactly: **Critical 89/100, 60 points from 7 findings on 5/5 documents, 87 without the cross-document finding.**
- **Shared fixture:** the test writes `fixtures/regression/case-risk-sprint4.json` (each RS case's risk and scoring trace). The Playwright suite renders it for RS-001 – RS-008 and MA-020/021, and the backend test fails if scoring drifts from it (regenerate with `REGENERATE_REGRESSION_FIXTURES=1`), like Sprint 3's C028 report.
- **Invalid agent output:** `tests/test_regression_invalid_output.py` reuses Sprint 2's real LangGraph pipeline harness. The scripted extraction reply is prose instead of JSON, once (MA-024) or on both attempts (MA-025 – MA-027): no `FORCE_BAD_EXTRACTION` hook is needed.
- **Cross-document validation:** `tests/test_regression_cross_document.py` runs the case analysis's code comparisons (company names, people, addresses, UENs) with the live run's names, IDs and addresses. CD-008 – CD-011 run the document's real external validation, with OneMap's and HDB's answers faked as they came back in the live run. SWIFT BICs come from the bundled reference table.
- **AI-generated content:** `tests/test_regression_ai_generation.py` reuses Sprint 3's detection harness (real graph, real tools, scripted specialists) on uploaded scans generated in the test: one with a generator's upsampling checkerboard, one with ordinary sensor noise.
- **Notes:** `tests/test_regression_notes.py` runs the real notes endpoints over an in-memory `case_notes` table, for what only a stored note shows: a note saved through the API is the one read back on reload, and a note on an approved document can be added to while the document is only read. The single-request rules stay in `test_notes.py`, tagged `sprint4` per test.
- **Frontend:** `frontend/e2e/specs/case-analysis.spec.ts` (new), and a Sprint 4 block in `notes.spec.ts`. `mockBackend.ts` answers `GET /cases/{id}/analysis` and `POST …/analysis/run` with the backend's rules (not ready → 409, already running → 409), and works out `analysis_ready` and the documents status from the case's documents on every read, as the backend does. What it can't compute it serves from fixtures that backend tests keep in step: the checklist and coverage for a set of documents (`checklist-variants.json`) and a misfiled document's case match (`case-match-foreign-company.json`). Its error messages match the server's word for word. The e2e code is now typechecked (`tsconfig.e2e.json`, part of `npm run typecheck`).
- **No real AI calls:** whether Claude still picks the right tools and ratings on the real documents is checked by the **manual live run at the end of each sprint**.

Existing tests that already cover Sprint 4 cases (`test_notes.py`, `test_case_risk.py`, `test_case_analysis.py`, `test_output_validation.py`, `test_company_identity.py`, `test_cross_document_*.py`, `test_address_matching.py`, `test_registry_lookups.py`, `test_ai_generation.py`) are tagged `sprint4` centrally in `conftest.py`.

## Results by test case

✅ pass · ❌ defect

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| CM-029 – CM-036 | SCRUM-81 | Note on a case / a document; author, time and document shown; case note has no document; both persist on reload; adding to a note on an approved document leaves it locked | `notes.spec.ts`; `test_regression_notes.py`; `test_notes.py` | ✅ |
| RS-001 | SCRUM-84 | Clean case → Low, 0, no findings | `test_regression_case_risk.py`; `case-analysis.spec.ts` | ✅, see note |
| RS-002 | SCRUM-84 | Failed detection run → no score, state failed, 2 of 3 scanned | `test_regression_case_risk.py`; `case-analysis.spec.ts` | ✅ (REG-013 fixed, re-tested) |
| RS-003 | SCRUM-84 | Highs on 3+ documents → Critical 80 – 100, with the reason | `test_regression_case_risk.py`; `case-analysis.spec.ts` | ✅ |
| RS-004, RS-005 | SCRUM-84 | One medium (3 pts) → Medium; one high (8 pts) → High 50 | `test_regression_case_risk.py`; `case-analysis.spec.ts` | ✅ |
| RS-006 – RS-008 | SCRUM-90 | Each finding's document, type, check, severity, summary, rationale, points; high → medium → low; nothing while unscored | `test_regression_case_risk.py`; `case-analysis.spec.ts`; `test_case_risk.py` | ✅ |
| MA-013, MA-014 | SCRUM-52 | Button hidden while scanning; shown once every document is scanned | `case-analysis.spec.ts`; `test_regression_case_analysis.py`; `test_case_analysis.py` | ✅, see note |
| MA-015 – MA-018 | SCRUM-52 | Starts one run for the case; duplicate requests refused (409) and start nothing; error shown; a failed run says so | `case-analysis.spec.ts`; `test_regression_case_analysis.py` | ✅ |
| MA-019 – MA-023 | SCRUM-53 | No scoring while the cross-document check runs; scoring after it, including its findings; no scoring when it fails, and the failure is shown | `test_regression_case_analysis.py`; `case-analysis.spec.ts` | ✅ |
| MA-024 – MA-027 | SCRUM-54 | One retry of an invalid reply; a second invalid reply fails only that document; the reason is shown; the rest carry on and it can be retried | `test_regression_invalid_output.py`; `case-analysis.spec.ts`; `test_output_validation.py` | ✅ |
| CD-001 – CD-007 | SCRUM-255 | Suffix punctuation and abbreviations, case and spacing match; P.te, a different core name and LLP vs Pte Ltd are flagged | `test_regression_cross_document.py`; `test_company_identity.py` | ✅ (CD-007 failed live, fixed, re-tested) |
| CD-008 – CD-011 | SCRUM-256 | A real HDB address clean; a street not at its postal code flagged; correct SWIFT BIC clean, another bank's flagged | `test_regression_cross_document.py`; `test_registry_lookups.py` | ✅ |
| CD-012 – CD-017 | SCRUM-253 | Matching name and ID; case and word order ignored; different ID flagged; different name with the same ID flagged; only the same person compared | `test_regression_cross_document.py`; `test_cross_document_deterministic.py` | ✅ (CD-016 failed live, fixed, re-tested) |
| CD-018 – CD-021 | SCRUM-254 | Same person at another address, another unit, same UEN at another address flagged; the same address written differently not flagged | `test_regression_cross_document.py`; `test_address_matching.py` | ✅ |
| FD-027, FD-028 | SCRUM-67 | AI-generated scan flagged (Spectral Peaks), with layout and digital checks run; clean scan not flagged for AI | `test_regression_ai_generation.py`; `test_ai_generation.py` | ✅, see note |

## Defects

REG-013 was fixed in this story; the others by their owners.

| ID | Severity | Status | What goes wrong | Example | Test case | Owner story |
|---|---|---|---|---|---|---|
| REG-013 | Low | Fixed and re-tested | When a document's detection run failed, the Customer Risk Summary showed only "Detection failed on a document." The case's risk carries how many documents were scanned (the API returns 2 of 3), and the pending state showed that count, but the failed state never did. Fixed: the failed state now reads "2 of 3 documents scanned. Retry detection on the failed document…". | Case with 3 documents, 1 failed | RS-002 | SCRUM-84 / SCRUM-90 |
| CD-007 (live) | Medium | Fixed and re-tested | "Zenith Renewable Energy LLP" matched "… Pte Ltd": a different entity type wasn't flagged. | COI vs bank statement | CD-007 | SCRUM-255 |
| CD-016 (live) | Medium | Fixed and re-tested | The same NRIC with a different name (Tan Wei Ming / Lim Wei Ming) wasn't flagged. | NRIC vs bank statement | CD-016 | SCRUM-253 |
| REG-014 | Low | Open | After an officer cancels detection, the panel can stay on "Cancelling…" for good. `useDetection.ts` sets its cancel-pending flag when the cancel request answers, and clears it when the document leaves `detecting`. If the worker's snapshot (run stopped) reaches the page before that answer, the flag is cleared first and set after, and nothing clears it again: once Retry detection starts a new run, the panel shows a disabled "Cancelling…" instead of "Cancel detection". Found from an intermittent failure of the cancel-and-retry browser test. | Cancel detection, the snapshot arrives first, then Retry detection | Cancel/Retry detection (SCRUM-299) | `frontend/src/pages/document-details/useDetection.ts` |

### Re-testing (AC4)

- **REG-013:** fixed in `CaseRiskCard.tsx`. `RS-002 a failed detection run shows no score, says detection failed and how many were scanned` in `case-analysis.spec.ts` now passes as a normal test.
- **REG-014:** not fixed. The browser test waits for the cancel to be accepted before the worker's snapshot (the usual order), so it is stable and doesn't hide the defect. Once fixed (e.g. clearing the flag when a new run starts), add a test where the snapshot arrives first and re-run.
- **CD-007, CD-016:** fixed during the sprint and passed on re-test. `test_a_different_entity_type_is_flagged` and `test_the_same_id_with_different_names_is_flagged` keep them fixed.
- **Sprint 3's REG-005 – REG-012:** REG-005 is fixed and its test is a normal test. REG-006 – REG-012 passed in the Sprint 4 live retest of FD-001 – FD-026.

## Observations (not defects)

- **MA-013 / readiness:** a document still being *extracted* doesn't hide the Run Case Analysis button. By design (`case_pipeline/readiness.py`), only approved documents count, and a document mid-extraction can't be approved yet. The test case's wording ("detection or extraction") says it should. The live run passed with a document in detection. Worth deciding which is meant; `test_a_document_still_being_extracted_does_not_hold_the_analysis_back` records the current rule.
- **RS-001:** a clean case's reason reads "Rated Low: detection found nothing worth flagging.", not "the combined severity of findings" as the test case words it. That wording is used only when there are findings. The live run had no clean documents to check this.
- **MA-017:** the "already running" error shows the raw API reply: `Failed to start the case analysis (409): {"detail":"An analysis is already running for this case."}` (the same pattern as Sprint 3's CM-024 note).
- **FD-028 / CD-021 (live):** the clean NRIC and the reformatted-address credit report weren't flagged for AI generation or address, as expected, but both documents still came back FLAGGED overall, from layout/image findings. This is the clean-documents-flagged pattern from Sprint 3's REG-006.
- **CD-012 / CD-013:** the sheet's actual result reads "Returns flagged findings as expected" while the expected result is "no mismatch". The suite checks the expected result. Worth correcting the wording in the sheet.
- **FD-027:** the live passport scored 0.42 (medium) for AI generation: resampling peaks plus missing camera metadata. The same scan was also flagged for its NRIC checksum and "OFSINGAPORE".

## Running it

```bash
source .venv/bin/activate
cd backend && python -m pytest -m sprint4
cd ../frontend && npm run test:e2e:sprint4
```

Full regression (all sprints): `python -m pytest` and `npm run test:e2e`. Both run on every PR in CI.
