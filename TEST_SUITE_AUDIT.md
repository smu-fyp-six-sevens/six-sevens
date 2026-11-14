# Test suite audit

Open items only: delete a row once it's fixed. Read 2026-11-13. Tests owed for fixes already made are in `PENDING_TESTS.md`. Bugs: `CODEBASE_AUDIT.md`.

## 1. Background

**Keep in mind:**
- All test documents are synthetic (GLDB can't share real ones), so real fraud may look different.
- Two company-name matchers on purpose: `identifiers.company_names_match` is lenient (does this document belong to the case?); `company_identity.cross_document_company_names_match` is strict (a fraud signal).
- Case match is triage, not a fraud signal.
- Regression suites test code, not AI accuracy: Claude is scripted. Real AI behaviour is checked by the manual live run each sprint.
- A known bug is pinned with `xfail(strict=True, raises=...)` (`it.fails` in Vitest, `test.fail()` in Playwright). None today.
- Guards that keep the suite honest: every route but the public ones must answer 401 without a login (`test_auth.py`); every backend route is either mocked for Playwright or listed as not mocked (`mockCoverage.test.ts`); coverage below 85% fails CI (`pyproject.toml`).

**Health (2026-11-13):** backend 3,289 cases pass locally (CI 2026-11-11: 93% line coverage). Playwright: 276 pass in 15 specs. Vitest: 76 tests in 9 files (of ~160 source files).

## 2. Most important

| Item | In plain words |
|---|---|
| 5.1 | The Playwright mock uses hand-written types, so a backend change can leave the specs passing against a shape the backend no longer sends |

## 3. Tests that check the code against itself

None open. The regression fixtures are regenerated from the code, so they catch changes, not mistakes (accepted: the tests themselves are what's read).

## 4. Weak tests

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 4.1 | Medium | Some tests only check which mock was called, so they pass whatever the code stores | Move `test_sweeper`, `test_case_stage`, `test_doc_worker`, `test_case_auto_analysis`, `test_health`, `test_ws`, `test_realtime_listener` to the in-memory client (`tests/support/fake_supabase.py`); extend the fake (it lacks `or_`) rather than mock | `backend/tests/` | L |

## 5. Playwright

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 5.1 | Medium | The mock's responses use hand-written types, so they don't break when the API changes | Type them with `api.gen.ts`'s `components['schemas']` | `frontend/e2e/support/mockBackend.ts:5` | M |
| 5.3 | Low | The page calls four routes that no spec exercises and the mock doesn't serve: missing an issue, editing and deleting a review, the agent prompt view | Mock them and add a spec each; then delete their lines from `NOT_MOCKED` | `frontend/src/services/uploadService/mockCoverage.test.ts` | M |

## 6. Missing tests

| # | In plain words | Detail | Size |
|---|---|---|---|
| 6.1 | Feedback counts only other cases | No test, including a document with no case (SQL: needs a real database, so a manual check) | S |
| 6.2 | Low coverage | `cross_document/tools/consistency.py` 68% (lines 61–82); `routers/agents.py` 53% and `agent_prompts.py` 48% (the prompt view) | M |
| 6.3 | The important frontend logic has no unit tests | Vitest for `caseValidation`, `fileValidation`, `request.ts` and `caseSocket.ts` only (the rest is covered by Playwright) | S |
| 6.4 | Load tests are described but not in the repo; the agent sensibility checks have no tests yet | Load tests: `CLOUD_ARCHITECTURE_PLAN.md` §10; checks: `PENDING_TESTS.md` 34–39 | — |

## 7. Suggested order

1. Type the mock (5.1), then mock the four unexercised routes (5.3).
2. Move mock-only tests to the in-memory client (4.1).
3. Frontend unit tests and the coverage gaps (6.3, 6.2).
