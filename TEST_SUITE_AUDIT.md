# Test suite audit

Open items only: delete a row once it's fixed. Read 2026-11-13; numbers from CI run `42980050902` (2026-11-11). Tests for fixes already made are in `PENDING_TESTS.md`; older test work is in `backend_tests.md`.

## 1. Most important

| Item | In plain words |
|---|---|
| 3.1 | No test checks that every route needs a login: tests switch login off globally |
| 2.1 | The "expected results" files are copies of the code's own output, so they can't catch a rule that was wrong from the start |
| 4.1 | The Playwright mock uses hand-written types, so a backend change can leave the specs passing against a shape the backend no longer sends |
| 5.2 | The bias screen has no test, and half of the feedback scrubber is untested |

## 2. Tests that check the code against itself

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 2.1 | Medium | The regression fixtures are regenerated from the code, so they catch changes, not mistakes; regenerated diffs get accepted unread | Beside each suite, 2–3 hand-written checks of what matters (level, points, which finding is kept) | `tests/support/regression_fixtures.py`; `scripts/regenerate_regression_fixtures.py` | M |
| 2.3 | Low | The two-digit-year test recomputes the code's own window, so it moves with the code | Fixed dates and a frozen "today" | `tests/test_date_parsing.py:42-47` | S |

## 3. Weak tests

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | High | A new route that forgets the login check would pass every test | One test over `app.routes`: every route not on the allow-list (`/health`, `/healthz`, `/health/db`, `/auth/login`, `/push/{role}`, `/metrics`, the socket) answers 401 with no token | `tests/conftest.py:57-67`; `tests/test_auth.py` | S |
| 3.2 | Low | A timing assertion that passes whatever the timings are | Also assert the inner span's name and parent | `tests/test_agent_trace.py:35` | S |

## 4. Playwright mock drift

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 4.1 | Medium | The mock's responses use hand-written types, so they don't break when the API changes | Type them with `api.gen.ts`'s `components['schemas']` | `frontend/e2e/support/mockBackend.ts:5` | M |
| 4.2 | Low | Nothing notices a backend route the mock doesn't serve (e.g. `GET /agents/{agent_id}/prompt`, `/documents/monitoring/stuck`, `/issues`, some detection checks) | A test that lists `openapi.json` paths and fails for any the mock neither serves nor lists as skipped | `frontend/e2e/support/mockBackend.ts` | S |

## 5. Missing tests

**Rules the design says code enforces:**

| Rule | Gap |
|---|---|
| Protected attributes never reach a model (`bias.py`) | No test at all: camelCase field names, dropped hypotheses, discarded verdicts |
| Feedback is scrubbed (`feedback_scrub.py`) | `names_in` never runs: a list of names, a non-"name" field, a money amount |
| Feedback counts only other cases | No test, including a document with no case (SQL: needs a real database, so a manual check) |
| Only officers' actions are logged as `user` | No test that an agent event never records `actor_type: user` |

**Income documents and bank statements:**

| Behaviour | Why |
|---|---|
| First or last row is "balance brought/carried forward" | Off-by-one risk in the running balance |
| Statement crosses a year with year-less dates | Exists only as a scratch script (`row_date`) |
| The registry lookup times out or returns 5xx | `registry_lookups/http.py` error path (lines 42–57) never runs; it should say "unable to assess", not "clean" |

**Low coverage:** `cross_document/tools/consistency.py` 68% (lines 61–82); `snapshots.py` 75% (live-update pushes); `routers/agents.py` 53% and `agent_prompts.py` 48% (the prompt view).

## 6. Flaky risks

| # | In plain words | Fix | Where |
|---|---|---|---|
| 6.2 | Two tests read the real clock; one sits close to the code's 1-day limit | Freeze "now" | `tests/test_metadata_analysis.py:141`, `:438` |
| 6.3 | A fixed 0.1 s wait can fail on a slow runner, and late tasks can run after the patches are gone | Wait on the queue with a timeout; cancel pending tasks in `finally` | `tests/test_realtime_listener.py:108-119` |

## 7. Suggested order

1. Route-wide login test (3.1).
2. Bias and scrub tests (5.2).
3. Statement and registry tests (5).
4. Type the mock, then the path test (4.1, 4.2).
5. Hand-written checks beside the fixtures (2.1).
6. Freeze clocks (2.3, 6.2, 6.3).

## 8. Decision needed

**How much should we trust the saved "expected results" files?** They agree with whatever the code did when they were saved, mistakes included. Options: keep as is; a second person reviews every regenerated file; or add a few hand-written checks beside each. *Recommended: the hand-written checks: the only option that catches a wrong rule.*
