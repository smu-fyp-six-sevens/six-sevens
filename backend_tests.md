# Tests: where we are and what's next

Updated 2026-10-19. This comes from reading the tests, not from a full run: recent changes were checked by calling test functions directly, never with `pytest`. So the first job is a real run (below).

> **Note: all test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document here, and in the manual test sets, is synthetic. That is a limitation of the whole system, not of this suite: real fraudsters' methods may differ from the ones we inject. It is recorded under the sponsor-confirmed facts in `CLAUDE.md`.

## 1. What you need to do next

1. **Run the full backend suite** (`pytest` in `backend/`) **and Playwright** (`npm run test:e2e` in `frontend/`). A lot changed since the last real run: the shared `identifiers.py`, the router error helpers, the performance changes, the delete clean-up, the 100-character name limit. Send me any failures and I'll fix them.
2. **Commit and push**, then check that CI is green. Nothing from this work is committed yet.
3. **Decide what to do with the six ignored test files** (about 140 tests that never run): delete them, move them out of `tests/`, or turn them back on. I'd move them out: today they look like coverage but aren't. They test the synthetic-data tool in `app/injection/`.
4. **Optional:** install `exiftool` locally. A few tests are skipped, or fail in my direct runs, without it. CI already handles this.
5. **Optional:** change the Jira wording for SCRUM-109. The code is right: "confidences sum to 1" holds whenever there is at least one candidate.

## 2. How healthy is the suite?

About 1,740 backend tests in 80 files, and 105 Playwright tests in 5 specs.

| Group | Share | What to do |
|---|---|---|
| **Solid:** tests of the detection logic, the real-fixture `test_regression_*` files, case match, identifiers, company identity, cross-document checks, case risk, the external-call guard | about 65% | Keep |
| **Brittle:** router and repository tests that check exactly which mock was called, so small refactors break them (about 60 tests use four or more `patch`es) | about 25% | Rewrite to check results instead (plan below) |
| **Low value:** four "graph compiles" tests, two tests that only echo a mock, the ignored injection tests | about 10% | Merge, trim or decide |

A few specific problems:

- **Skipped tests:** two SCRUM-70 regression tests (company-name mismatch, in `test_regression_detection.py`). That check moved to the case pipeline and `test_company_identity.py` covers it, but the two scenarios were never ported.
- **Tests that prove little:** about 45 tests have no plain `assert`, and 42 only assert that a mock was called. The three `test_case_service::test_sync_submission_finalized_*` tests have no assertion of their own, so check they exercise something.
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

## 5. Plan: what I'll change

I'll go one step at a time, you review each, and I won't commit. For steps 1–6 I'll check each change by calling the affected tests directly, and you confirm with a real `pytest` run.

| Step | Change | Why | Size |
|---|---|---|---|
| 1 | **Router tests:** one shared fixture in `test_detection.py` replacing the 5-patch blocks, and one parametrised test over the detection endpoints (needs full detection first, document missing, fetch fails → 502). Same pattern for not-found and 502 in `test_documents.py`. Assert on the response, not on the mock call | Removes 30–40 near-identical tests and the most fragile ones | M |
| 2 | **Fold repeated cases** with `parametrize`: the 18 "does not flag X" tests in `test_metadata_analysis.py`, plus the smaller clusters in `test_case_service`, `test_extraction_prompts` and `test_langgraph` | Same coverage, much less code | S-M |
| 3 | **`*_graph_compiles`:** one parametrised test, and make a broken langgraph import fail instead of silently skipping | A dependency break would currently go unnoticed | S |
| 4 | **Drop the two mock-echo tests** in `test_supabase_client.py`; keep the missing-config and caching tests | They test the mock, not our code | S |
| 5 | **Give the three `test_sync_submission_finalized_*` tests** a real assertion each, and replace mock-only assertions in `test_health`, `test_storage_service` and `test_realtime_listener` with behaviour checks where practical | Tests that can't fail aren't tests | S-M |
| 6 | **Port the two skipped SCRUM-70 scenarios** to the case-pipeline suite (or retire them, if you tell me Jira has) | Closes a coverage gap | S |
| 7 | **Repository tests on an in-memory fake Supabase client**, asserting stored state instead of mock call chains | Makes refactors safe; the biggest brittleness left | L |
| 8 | **Playwright:** the triage-chip test and a faithful `DI-045`; login failure and redirect; the analysis card; cancel and retry detection; merge the overlaps; add `data-testid`s | Fills the biggest UI gaps | M-L |
| 9 | **Vitest** for the pure frontend helpers (`useCaseSnapshot`, `canApprove`) | Logic with no test below the browser | M |
| 10 | **Coverage run** on the unreferenced modules, starting with `line_shifts.py` and the `cross_document` verify paths, then add tests where there are holes | Turns "no test names it" into facts | M |

My suggested order is 1, 2, 3, 4, 5, 6, then 8, 7, 9, 10. Steps 1–4 are quick and safe, step 7 is the biggest, and step 10 needs a coverage run from you first.
