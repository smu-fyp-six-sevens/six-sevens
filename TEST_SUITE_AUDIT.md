# Test suite audit

Open items only: delete a row once it's fixed. Read 2026-11-13; numbers from CI run `42980050902` (2026-11-11). Tests owed for fixes already made are in `PENDING_TESTS.md`. Bugs: `CODEBASE_AUDIT.md`.

## 1. Background

**Keep in mind:**
- All test documents are synthetic (GLDB can't share real ones), so real fraud may look different.
- Two company-name matchers on purpose: `identifiers.company_names_match` is lenient (does this document belong to the case?); `company_identity.cross_document_company_names_match` is strict (a fraud signal).
- Case match is triage, not a fraud signal.
- Regression suites test code, not AI accuracy: Claude is scripted. Real AI behaviour is checked by the manual live run each sprint.
- A known bug is pinned with `xfail(strict=True, raises=...)` (`it.fails` in Vitest, `test.fail()` in Playwright). None today.

**Health (CI, 2026-11-11):** backend 3,161 cases pass, 93% line coverage. Playwright: 276 pass in 15 specs. Vitest: 57 tests in 8 files (of ~160 source files).

## 2. Most important

| Item | In plain words |
|---|---|
| 4.1 | No test checks that every route needs a login: tests switch login off globally |
| 3.1 | The "expected results" files are copies of the code's own output, so they can't catch a rule that was wrong from the start |
| 5.1 | The Playwright mock uses hand-written types, so a backend change can leave the specs passing against a shape the backend no longer sends |
| 6.1 | The bias screen has no test, and half of the feedback scrubber is untested |

## 3. Tests that check the code against itself

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | Medium | The regression fixtures are regenerated from the code, so they catch changes, not mistakes; regenerated diffs get accepted unread | Beside each suite, 2–3 hand-written checks of what matters (level, points, which finding is kept) | `tests/support/regression_fixtures.py`; `scripts/regenerate_regression_fixtures.py` | M |
| 3.2 | Low | The two-digit-year test recomputes the code's own window, so it moves with the code | Fixed dates and a frozen "today" | `tests/test_date_parsing.py:42-47` | S |

## 4. Weak tests

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 4.1 | High | A new route that forgets the login check would pass every test | One test over `app.routes`: every route not on the allow-list (`/health`, `/healthz`, `/health/db`, `/auth/login`, `/push/{role}`, `/metrics`, the socket) answers 401 with no token | `tests/conftest.py:57-67`; `tests/test_auth.py` | S |
| 4.2 | Low | A timing assertion that passes whatever the timings are | Also assert the inner span's name and parent | `tests/test_agent_trace.py:35` | S |
| 4.3 | Medium | Some tests only check which mock was called, so they pass whatever the code stores | Move `test_sweeper`, `test_case_stage`, `test_doc_worker`, `test_case_auto_analysis`, `test_health`, `test_ws`, `test_realtime_listener` to the in-memory client (`tests/support/fake_supabase.py`); extend the fake (it lacks `or_`) rather than mock | `backend/tests/` | L |

## 5. Playwright

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 5.1 | Medium | The mock's responses use hand-written types, so they don't break when the API changes | Type them with `api.gen.ts`'s `components['schemas']` | `frontend/e2e/support/mockBackend.ts:5` | M |
| 5.2 | Low | Nothing notices a backend route the mock doesn't serve (e.g. `GET /agents/{agent_id}/prompt`, `/documents/monitoring/stuck`, `/issues`, some detection checks) | A test that lists `openapi.json` paths and fails for any the mock neither serves nor lists as skipped | `frontend/e2e/support/mockBackend.ts` | S |
| 5.3 | Low | Specs break when CSS is restyled: 133 CSS-class locators, 36 `.nth()`, 50 `.first()` | Add `data-testid`s before a restyle | `frontend/e2e/` | S |

## 6. Missing tests

**Rules the design says code enforces:**

| # | Rule | Gap |
|---|---|---|
| 6.1 | Protected attributes never reach a model (`bias.py`) | Field names are now tested (`test_bias.py`); still none: dropped hypotheses, discarded verdicts |
| 6.2 | Feedback is scrubbed (`feedback_scrub.py`) | `names_in` never runs: a list of names, a non-"name" field, a money amount |
| 6.3 | Feedback counts only other cases | No test, including a document with no case (SQL: needs a real database, so a manual check) |
| 6.4 | Only officers' actions are logged as `user` | No test that an agent event never records `actor_type: user` |

**Income documents and bank statements:**

| # | Behaviour | Why |
|---|---|---|
| 6.5 | First or last row is "balance brought/carried forward" | Off-by-one risk in the running balance |
| 6.6 | Statement crosses a year with year-less dates | Exists only as a scratch script (`row_date`) |
| 6.7 | The registry lookup times out or returns 5xx | `registry_lookups/http.py` error path (lines 42–57) never runs; it should say "unable to assess", not "clean" |

**Other gaps:**

| # | In plain words | Detail | Size |
|---|---|---|---|
| 6.8 | Low coverage | `cross_document/tools/consistency.py` 68% (lines 61–82); `snapshots.py` 75% (live-update pushes); `routers/agents.py` 53% and `agent_prompts.py` 48% (the prompt view) | M |
| 6.9 | Frontend logic is barely unit-tested | No Vitest for `caseSearch`, `caseValidation`, `fileValidation`, `errorText`, `caseStages`, `request.ts`, `caseSocket.ts`, `useFieldEditing`, `useDocumentSelection`, `useFindingReviews`; no coverage tool | M |
| 6.10 | Coverage can drop unnoticed | No `--cov-fail-under` | S |
| 6.11 | Evals and load tests are described but not in the repo | See `PENDING_WORK.md` "Evals and verification" | — |

## 7. Flaky and environment risks

| # | In plain words | Fix | Where |
|---|---|---|---|
| 7.1 | Two tests read the real clock; one sits close to the code's 1-day limit | Freeze "now" | `tests/test_metadata_analysis.py:141`, `:438` |
| 7.2 | A fixed 0.1 s wait can fail on a slow runner, and late tasks can run after the patches are gone | Wait on the queue with a timeout; cancel pending tasks in `finally` | `tests/test_realtime_listener.py:108-119` |
| 7.3 | Local Python differs from CI | Local venv 3.12.8; CI and Dockerfile 3.13 | `backend/Dockerfile`, `.github/workflows/ci-cd.yml` |

## 8. Suggested order

1. Route-wide login test (4.1).
2. Bias and scrub tests (6.1, 6.2).
3. Statement and registry tests (6.5–6.7).
4. Type the mock, then the path test (5.1, 5.2).
5. Move mock-only tests to the in-memory client (4.3), then `data-testid`s before any restyle (5.3).
6. Hand-written checks beside the fixtures (3.1).
7. Freeze clocks (3.2, 7.1, 7.2).

## 9. Decision needed

**How much should we trust the saved "expected results" files?** They agree with whatever the code did when they were saved, mistakes included. Options: keep as is; a second person reviews every regenerated file; or add a few hand-written checks beside each. *Recommended: the hand-written checks: the only option that catches a wrong rule.*
