# Regression Report — Sprint 3

**Story:** SCRUM-246 Sprint 3 Regression Testing
**Branch:** `SCRUM-246-Sprint-3-Regression-Testing`, from `main` at `abf06d4`
**Run date:** 2026-10-14
**Source test cases:** `Test Cases - Sprint 3.xlsx`

## Summary

| Manual test cases | Count |
|---|---|
| In the sprint sheet | 70 |
| Out of scope | 12 |
| **In scope** | **58** |
| Automated and passing | 57 |
| Automated, defect found (pinned as expected failure) | 1 (CM-022, API side) |

Out of scope: TD-001 – TD-006 (test-data tooling: the masterlist and generated documents, checked manually, same as Sprint 1 & 2) and the Testing sheet's six SCRUM-206 rows, which describe the regression process itself.

| Suite | Tests | Passed | Known defects (expected failures) |
|---|---|---|---|
| Backend — pytest (all) | 1530 | 1527 (+2 skipped) | 1 (REG-005) |
| └ tagged `sprint3` | 765 | 764 | 1 (REG-005) |
| └ tagged `sprint1` / `sprint2` (re-run) | 124 | 124 | 0 |
| Frontend — Playwright (all) | 98 | 98 | 0 |
| └ tagged `@sprint3` | 30 | 30 | 0 |

**Sprint 1 & 2 still pass.** REG-002 and REG-003 from the Sprint 2 report were fixed on `main` (`8adc10d`). Their expected-failure markers are gone and both earlier suites are fully green.

**Defects:** 1 reproduced in the automated suite (REG-005). 7 more come from the Sprint 3 live run (REG-006 – REG-012). They depend on the real test documents or on live Claude behaviour, so they're tracked here and re-tested at the end-of-sprint live run. No defects were fixed in this story: they're listed for their owners.

### Acceptance criteria

| AC | Status |
|---|---|
| AC1 — all completed Sprint 3 functionalities regression tested | ✅ All 58 in-scope cases automated (backend and/or Playwright), AI steps mocked |
| AC2 — previously passed Sprint 3 cases still pass | ✅ Every case that passed in the live run passes in the suite; both suites run on every PR |
| AC3 — defects documented and tracked | ✅ REG-005 – REG-012 below, with test case, example and owner story |
| AC4 — regression re-run after defects are fixed | ⏳ Waiting on fixes. REG-005 is a strict expected failure that flags itself once fixed; REG-006 – REG-012 are re-checked in the live run |

## How the Sprint 3 suite works

Same approach as Sprint 1 & 2:

- **Detection (new):** `tests/test_regression_detection.py` runs the **real detection graph** end to end: lead plan → three specialists in parallel (each a real tool-calling subgraph) → lead review → second look (at most once) → synthesize. The **tools and fraud checks are real** and run on each document's bytes. **Only Claude's replies are scripted** (`FakeClaude` stands in for `build_chat_anthropic`, including the numerical binder). The scripted specialists call the tools a test names, then rate whatever those tools actually found, so results are consistent and cost nothing.
- **Test documents** are generated in the test (pymupdf), each carrying its test case's injected anomaly (restyled label, indented clause, typed-over text, painted-over crest, Acrobat/"FraudTests" metadata, broken running balance, future date). The Sprint 3 PDFs (C005, C028, C034…) aren't in the repository.
- **Real data where it exists:** case match and the misspelt-company case use GLDB-02 Anchorpoint and GLDB-16 Harbor Light (`tests/fixtures/regression/`). GLDB-16's `C014_CertIncorp` really reads "HARBOR LIGHT SHIPING PTE LTD".
- **Shared report fixture:** `fixtures/regression/detection-report-c028.json` is a report the real graph produced. The Playwright suite renders it for FD-015 – FD-020, and a backend test fails if the graph's output drifts from it.
- **No real AI calls:** `conftest.py` still blocks any real Claude request. Whether Claude picks the right tools and ratings on the real documents is checked by the **manual live run at the end of each sprint**.

New files: `backend/tests/test_regression_{detection,case_match,trigger_detection,case_management}.py`, `frontend/e2e/specs/detection.spec.ts`, and Sprint 3 blocks in `case-management.spec.ts`. Existing tests that already cover Sprint 3 cases are tagged `sprint3` centrally in `conftest.py`.

## Results by test case

✅ pass · ❌ defect

| ID | Story | Scenario | Automated by | Result |
|---|---|---|---|---|
| CM-014 – CM-021, CM-023 | SCRUM-116 | Edit, save, cancel, queue/reopen/sign-in persistence, blank names | `case-management.spec.ts`; `test_regression_case_management.py`; `test_cases.py` | ✅ |
| CM-022 | SCRUM-116 | Name over the maximum length | `case-management.spec.ts` (UI stops at 100 ✅); `test_regression_case_management.py` (API) | ❌ REG-005 |
| CM-024 – CM-028 | SCRUM-117 | Delete document/case with confirmation, checklist reverts, in-flight blocked, cancel keeps | `case-management.spec.ts`; `test_regression_case_management.py`; `test_documents.py`, `test_cases.py` | ✅ |
| DI-039 – DI-047 | SCRUM-110 | Case match: company/individual match and mismatch, can't proceed, values shown, incumbency rules | `test_regression_case_match.py` (real GLDB-02/16 data); `detection.spec.ts` (DI-045); `test_case_match.py` | ✅ |
| MA-005 – MA-012 | SCRUM-26 | Approve → Approved section, locked after approve/reject, low-confidence blocks approval, detection starts < 5s, not on reject | `detection.spec.ts`; `test_regression_trigger_detection.py`; `test_documents.py` | ✅ |
| FD-001, FD-002 | SCRUM-65 | Painted-over image flagged; clean image not | `test_regression_detection.py` | ✅ |
| FD-003, FD-004 | SCRUM-63 | Text overlay flagged; clean not | `test_regression_detection.py` | ✅ |
| FD-005, FD-006 | SCRUM-62 | Indented clause flagged, nothing unrelated; clean not | `test_regression_detection.py` | ✅ (live run: ❌ REG-006) |
| FD-007, FD-008 | SCRUM-66 | Acrobat + "FraudTests" metadata flagged; issuer metadata clean | `test_regression_detection.py` | ✅ |
| FD-009, FD-010 | SCRUM-68 | Broken running balance flagged; reconciling balances not | `test_regression_detection.py` | ✅ (live run: ❌ REG-011) |
| FD-011, FD-012 | SCRUM-69 | Future incorporation date flagged; consistent dates not | `test_regression_detection.py` | ✅ (live run: ❌ REG-010) |
| FD-013, FD-014 | SCRUM-64 | Low-res/recompressed crest flagged; single image = nothing to check | `test_regression_detection.py` | ✅ |
| FD-015 – FD-020 | SCRUM-119 | One verdict/summary/findings list; specialists with briefs and tools; FLAGGED/CLEAN/INCONCLUSIVE; second look once, to another specialist | `test_regression_detection.py`; `detection.spec.ts`; `test_detection_verdict.py` | ✅ (FD-018 live run: ❌ REG-006, REG-012) |
| FD-021 – FD-024 | SCRUM-61 | Font type, size, underline flagged; clean not | `test_regression_detection.py` | ✅ (live run: ❌ REG-007, REG-008, REG-009) |
| FD-025, FD-026 | SCRUM-70 | Misspelt company name flagged; clean not | `test_regression_detection.py` (real GLDB-16 data) | ✅, see note |

**FD-025 note:** the spelling check deliberately skips company names (`spelling_anomaly._COMPANY_NAME_RE`). A misspelt company name is caught by the company-name consistency check (SCRUM-255) instead, so "SHIPING" is flagged as a company-name mismatch, not under Spelling. The test case's expected result says "identifies the Spelling fraud dimension". Either the AC or the labelling needs a decision.

## Defects

Listed only, not fixed.

| ID | Severity | Status | What goes wrong | Example | Test case | Owner story |
|---|---|---|---|---|---|---|
| REG-005 | Low | Open, pinned (`xfail(strict=True)`) | The API saves a company name of any length. Only the UI's text box stops at 100 characters, so a direct API call (or another client) can save a longer name. `POST /cases` has the same gap. | `PATCH /cases/{id}` with 766 × "A" → 200 and saved. | CM-022 | SCRUM-116 / SCRUM-96 |
| REG-006 | High | Open (live run) | Clean test documents come back **FLAGGED** because layout and rendering checks report deviations on them. With nearly every clean document flagged, the verdict can't tell clean from tampered. | C035_NOA (clean): font, underline, spacing and indentation deviations. Same pattern in the remarks on FD-002, FD-008, FD-010, FD-012 and FD-026. | FD-006, FD-018 | SCRUM-62 / SCRUM-61 |
| REG-007 | Medium | Open (live run) | A clean certificate of incumbency is flagged for font emphasis and size. | C024_CertIncumbency: dates and company name "in unexpected fonts/weights/sizes". | FD-024 | SCRUM-61 |
| REG-008 | Medium | Open (live run) | An injected font-size change is not detected on the real document (a synthetic size change is). | C024-P1_ProofAddress: "Balance Brought Forward from Previous Bill" size mismatch not flagged. | FD-022 | SCRUM-61 |
| REG-009 | Medium | Open (live run) | An injected underline is not detected on the real document. Bold deviations nearby are reported; a synthetic underline is detected. | C026_CertIncumbency: underline on the company name missed. | FD-023 | SCRUM-61 |
| REG-010 | High | Open (live run) | Specialists sometimes come back without running any tool ("incomplete outputs, no tools run"), so their dimension is never checked. The report marks them uncertain (verified in the suite), but the date anomaly the case injected was never reported. | BizFile_112712 (C058): content validation and digital authenticity both incomplete; the future incorporation date (2027-04-18) only shows in the individual date check. FD-009: digital authenticity produced no output. | FD-011 (and FD-009) | SCRUM-119 |
| REG-011 | Medium | Open (live run) | The injected available-balance change on a bank statement isn't flagged. Per the tester, extraction read the correct available balance, so the numerical check never saw the edited value. Other balance problems were flagged. | C002_BankStatement_2026-06_to_2026-08 | FD-009 | SCRUM-68 / SCRUM-9 |
| REG-012 | Medium (test data) | Open (live run) | Generated "clean" identity documents carry NRIC numbers that fail the checksum, so they're correctly flagged and can never come back CLEAN. This is a fault in the test documents, not the checker. | C004-P1: S8745678D (check letter should be A). C027_CreditReport: expected D, got L. | FD-018, FD-008 | SCRUM-12 (test data) |

### Re-testing (AC4)

- **REG-005:** once fixed, `test_a_company_name_over_100_characters_is_not_saved` reports "passed unexpectedly". Remove its `xfail` marker and re-run.
- **REG-006 – REG-011:** these need the real documents or live Claude. Re-run the listed test cases in the end-of-sprint live run after each fix and record the result here. To pin them in CI, add the failing PDFs to `backend/tests/fixtures/regression/` and add a strict `xfail` per document.
- **REG-012:** regenerate the clean documents with valid NRICs, then re-run FD-008 and FD-018 live.

## Observations (not defects)

- **FD-025 / company name consistency:** in GLDB-16 the *genuine* certificate of incumbency is also flagged ("differs from SHIPING on 1 other document"), not just the misspelt certificate. Both sides of a mismatch always flag; worth deciding whether the majority name should win.
- **FD-016:** every specialist now reviews every document (the lead writes briefs and never skips), so the AC's "each skipped specialist with its reason" is always an empty list. The AC wording needs an update (tester's remark).
- **FD-021:** the "lead left the font finding out of its report" remark can't recur in the findings list, which is merged in code from the tool results. Only the free-text summary (Claude's) can leave it out.
- **CM-024:** a deleted document's link shows the raw API error, `Failed to fetch document (404): {"detail":"Document not found"}`, while a deleted case shows a plain "Case not found.".
- **MA-011:** tester suggests hiding the whole detection block for rejected documents (it currently says "Detection doesn't run on rejected documents.").
- **DI-047:** the wrong company's incumbency is sent to manual review with "N other documents agree on UEN …". That's intended: nothing else is flagged against a doubtful reference.

## Running it

```bash
source .venv/bin/activate
cd backend && python -m pytest -m sprint3
cd ../frontend && npm run test:e2e:sprint3
```

Full regression (all sprints): `python -m pytest` and `npm run test:e2e`. Both run on every PR in CI.
