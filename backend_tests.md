# Tests: where we are and what's next

Updated 2026-10-19. This comes from reading the tests, not from a full run: recent changes were checked by calling test functions directly, never with `pytest`. So the first job is a real run (below).

> **Note: all test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document here, and in the manual test sets, is synthetic. That is a limitation of the whole system, not of this suite: real fraudsters' methods may differ from the ones we inject. It is recorded under the sponsor-confirmed facts in `CLAUDE.md`.

## 1. What you need to do next

1. **Give the service role `DELETE` on the tables the backend now deletes from.** A test run reached the real database by accident (my direct-call runner lacked `conftest.py`'s network guard; it deleted nothing, because it used a case id that doesn't exist) and the database answered `permission denied for table case_analysis_runs` (42501). Until the privilege exists, **deleting a case will fail**. Run `grant delete on public.case_analysis_runs, public.flags, public.langgraph_trace to service_role;` in Supabase (it is harmless if the privilege is already there), and check `risk_scores` too, since the case delete touches it first. The README now lists the required privileges.
2. **Run the full backend suite** (`pytest` in `backend/`) **and Playwright** (`npm run test:e2e` in `frontend/`). The tests in this file were changed in steps 1–6 below and checked only by calling them directly. Send me any failures and I'll fix them.
3. **Commit and push**, then check that CI is green. The test changes from steps 1–6 are not committed yet.
4. **Decide what to do with the six ignored test files** (about 140 tests that never run): delete them, move them out of `tests/`, or turn them back on. I'd move them out: today they look like coverage but aren't. They test the synthetic-data tool in `app/injection/`.
5. **Optional:** install `exiftool` locally. A few tests are skipped, or fail in my direct runs, without it. CI already handles this.
6. **Optional:** change the Jira wording for SCRUM-109. The code is right: "confidences sum to 1" holds whenever there is at least one candidate.

## 2. How healthy is the suite?

About 1,670 backend test functions in 80 files (many are parametrised, so they run as well over 2,000 cases), and 105 Playwright tests in 5 specs.

| Group | Share | What to do |
|---|---|---|
| **Solid:** tests of the detection logic, the real-fixture `test_regression_*` files, case match, identifiers, company identity, cross-document checks, case risk, the external-call guard | about 65% | Keep |
| **Brittle:** router and repository tests that check exactly which mock was called, so small refactors break them (about 60 tests use four or more `patch`es) | about 25% | Rewrite to check results instead (plan below) |
| **Low value:** four "graph compiles" tests, two tests that only echo a mock, the ignored injection tests | about 10% | Merge, trim or decide |

A few specific problems:

- **Tests that prove little:** many of the repository and router tests still only assert that a mock was called (`test_document_repository`, `test_extracted_fields_repository`, `test_case_repository` and others). Step 7 below addresses this.
- **Modules no test names:** `line_shifts.py` (the largest), `query_stats.py`, `vision_tool.py`, `telemetry.py`, `sg_reference/banks.py`, `giro.py`, `categories.py`, `cpu_pool.py`, `pdf_pages.py`, `supabase_rows.py`. Another test may cover them indirectly; a coverage run will tell. The same run should confirm the agent's checks in `cross_document/agents.py` and `verify.py`.
- **Slowest file:** `test_ai_generation.py`, about 8 s (it was about 13 s; the shared test image is now cached). What's left is the detector itself.
- **Noise:** some tests (for example in `test_ws`) log swallowed "real Supabase call" errors. They pass but are noisy.

## 3. Playwright: what's missing

Not covered by any spec: the case analysis card, login failure and the redirect when logged out, clicking **Cancel detection** (only that the button shows), detection error and retry, document retry or cancel during extraction, the move-documents dialog, websocket reconnect, and the **case-match triage chip** (a document from another company shows "Sent to manual review" while Approve stays enabled).

Tests that prove less than they look like:

- `DI-025` only checks a button is enabled; `CM-009` checks a placeholder.
- `CM-026` sets the answer in the mock itself, so it tests the re-render, not the backend.
- `DI-045` forces a case-match state the backend no longer produces for that document.
- `FD-015`, `FD-017`, `FD-020` assert on the JSON fixture, not on the UI.

Overlap to merge (keep every test ID as a tag): upload `DI-007/011/015`, `DI-008` into `DT-031`, `DI-002 – DI-006`, the three fill-UEN-and-save tests, `DI-032/034`, `CM-003/005`, `MA-005/007/009`, `CM-025/028/029`.

Other notes: `mockBackend.ts` re-implements some backend rules, so it can drift from the real behaviour (two fixtures are kept in step by backend tests). `CM-024` shows the raw API error on the not-found page, a small UX leak. Selectors are CSS classes, with no `data-testid`. There are no frontend unit tests (no vitest), so helpers like `useCaseSnapshot` and `canApprove` are only tested through the browser.

## 4. Decisions already made

- **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (legal suffix and `&` ignored, never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
- **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.

## 5. Plan

### Done (steps 1-6)

Across 13 test files: 798 lines added, 1,146 removed (net 348 fewer), with no app code changed. Checked by calling the tests directly (about 1,900 cases pass; the failures that remain also fail without my changes and come from `exiftool`, the missing `ijson` package and fixtures my runner doesn't provide).

| Step | What changed | Result |
|---|---|---|
| 1 | **Router tests:** one parametrised matrix in `test_detection.py` (502 on fetch failure, 404 not found, 409 before a full run, 409 untyped, storage 404 and 502) over 11 check endpoints; the same for nine `{document_id}` routes in `test_documents.py`. It also asserts nothing was written | 23 near-identical tests replaced by 8 parametrised ones that run as about 76 cases |
| 2 | **Folded repeated cases:** 22 "is not flagged" metadata tests, 16 document-fulfilment tests, 14 prompt-content tests and three pairs in `test_langgraph.py` became tables with an id per case | Same checks in far fewer lines; the metadata cases also assert `unable_to_assess` is false |
| 3 | **Graph tests:** the four "compiles" tests are now one test over all six graphs asserting their real node names; every `importorskip` is gone, so a broken langgraph install fails | |
| 4 | **Supabase client tests:** kept, but tightened. One now proves the secret key (not the publishable one) is used; the "builds once" test now calls twice and proves the cache. I did not delete them because they check real behaviour | |
| 5 | **Behaviour instead of mock calls:** `test_realtime_listener` now runs the real debounce and a real broadcaster and asserts what a viewer receives (a burst gives one snapshot; I broke the coalescing on purpose to confirm the test fails); `test_storage_service` runs on an in-memory bucket; the case-flag tests assert the stored flag; `test_health` asserts the query that ran | Fewer tests that can only fail when the implementation changes |
| 6 | **SCRUM-70 scenarios ported:** the misspelt-company-name case (Harbor Light "SHIPING") and "cannot be dropped by the agent" now run in `test_cross_document_deterministic.py`; a spelling test confirms a company name printed on the document isn't a spelling finding | No skipped tests left from this item |

### Still to do

| Step | Change | Why | Size |
|---|---|---|---|
| 7 | **Repository tests on an in-memory fake Supabase client**, asserting stored state instead of mock call chains | Makes refactors safe; the biggest brittleness left | L |
| 8 | **Playwright:** the triage-chip test and a faithful `DI-045`; login failure and redirect; the analysis card; cancel and retry detection; merge the overlaps; add `data-testid`s | Fills the biggest UI gaps | M-L |
| 9 | **Vitest** for the pure frontend helpers (`useCaseSnapshot`, `canApprove`) | Logic with no test below the browser | M |
| 10 | **Coverage run** on the unreferenced modules, starting with `line_shifts.py` and the `cross_document` verify paths, then add tests where there are holes | Turns "no test names it" into facts | M |

Suggested order: 8, 7, 9, 10. Step 10 needs a coverage run from you first.
