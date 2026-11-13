# Tests: what's still open

Open test work only: delete an item once done. See also `TEST_SUITE_AUDIT.md` (newer findings) and `PENDING_TESTS.md` (tests owed for recent fixes).

**Keep in mind:**
- All test documents are synthetic (GLDB can't share real ones), so real fraud may look different.
- Two company-name matchers on purpose: `identifiers.company_names_match` is lenient (does this document belong to the case?); `company_identity.cross_document_company_names_match` is strict (a fraud signal).
- Case match is triage, not a fraud signal.
- Regression suites test code, not AI accuracy: Claude is scripted. Real AI behaviour is checked by the manual live run each sprint.
- A known bug is pinned with `xfail(strict=True, raises=...)` (`it.fails` in Vitest, `test.fail()` in Playwright). None today.

## Health (CI, 2026-11-11)

Backend: 3,161 cases pass, 93% line coverage. Playwright: 276 pass in 15 specs. Vitest: 57 tests in 8 files (of ~160 source files).

## Open items

| # | In plain words | Detail | Size |
|---|---|---|---|
| 1 | Some tests only check which mock was called, so they pass whatever the code stores | Move `test_sweeper`, `test_case_stage`, `test_doc_worker`, `test_case_auto_analysis`, `test_health`, `test_ws`, `test_realtime_listener` to the in-memory client (`tests/support/fake_supabase.py`); extend the fake (it lacks `or_`) rather than mock | L |
| 2 | The approval rules exist three times and can drift | Backend `document_service.approve`, frontend `canApproveDocument`, Playwright mock `missingRequiredFields`; only `required_fields.json` is checked | M |
| 3 | Global test stubs switch off automatic analysis, stage sync and the finalised check, so the real approve → stage → analysis chain never runs in regression suites | `tests/conftest.py`; only `test_case_auto_analysis` and `test_case_stage` run the real ones | M |
| 4 | Small coverage gaps | No test names `CR.TENOR` or `FATCA.TIN_FORMAT`; `telemetry.record_usage` with usage; `cross_document/tools/comparisons.py:131-159`; `events/bus.py:65-76`, `:94-103` | S |
| 5 | Frontend logic is barely unit-tested | No Vitest for `caseSearch`, `caseValidation`, `fileValidation`, `errorText`, `caseStages`, `request.ts`, `caseSocket.ts`, `useFieldEditing`, `useDocumentSelection`, `useFindingReviews`; no coverage tool | M |
| 6 | Coverage can drop unnoticed | No `--cov-fail-under` | S |
| 7 | Evals and load tests are described but not in the repo | See PENDING_WORK "Evals" | — |
| 8 | Specs break when CSS is restyled | 133 CSS-class locators, 36 `.nth()`, 50 `.first()`: add `data-testid`s before a restyle | S |
| 9 | Some infrastructure is never checked in CI | Kong config (`kong config parse`), compose files (`docker compose config -q`), Grafana PromQL (`promtool`), image builds on PRs, `ruff`/`pyright` on `tests/` | S–M |
| 10 | Local Python differs from CI | Local venv 3.12.8; CI and Dockerfile 3.13 | S |

**Order:** 1, then 2–4, then 9, then 8 before any restyle.
