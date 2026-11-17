# Test suite audit

Open items only: delete a row once it's fixed, and this file once it's empty. Read 2026-11-13, renumbered 2026-11-17. How the suite is built, what to keep in mind and its health: `HOW_IT_WORKS.md` §10. Tests owed for fixes already made are in `PENDING_TESTS.md`. Bugs: `CODEBASE_AUDIT.md`.

## 1. Most important

The Playwright mock uses hand-written types, so a backend change can leave the specs passing against a shape the backend no longer sends (5.1).

## 2. Tests that check the code against itself

None open. The regression fixtures are regenerated from the code, so they catch changes, not mistakes (accepted: the tests themselves are what's read).

## 3. Weak tests

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 3.1 | Medium | Some tests only check which mock was called, so they pass whatever the code stores | Move `test_sweeper`, `test_case_stage`, `test_doc_worker`, `test_case_auto_analysis`, `test_health`, `test_ws`, `test_realtime_listener` to the in-memory client (`tests/support/fake_supabase.py`); extend the fake (it lacks `or_`) rather than mock | `backend/tests/` | L |

## 4. Overlap (checked 2026-11-16)

Every backend and Playwright test compared by structure, with and without its literal values: no two check the same thing with the same inputs (the one exact copy is deleted). What's left is optional tidying:

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 4.1 | Low | The approval route tests check, one rule at a time, refusals the approval-rules fixture already pins (19 cases); the route only needs one refused case (400, nothing written) | Fold the rule-by-rule route tests into one parametrised test, or keep only the refusal-to-400 one | `backend/tests/test_documents.py` (the `test_approve_document_*` refusals) | S |
| 4.2 | Low | About 50 pairs of tests are the same code with different inputs (CPF and GST rates, NOA chains, identity progress, the "unable to assess" guards) | `pytest.mark.parametrize` where it reads better; not a gap or a duplicate | `test_sg_reference_checks.py`, `test_numerical_validation.py`, `test_mapper.py`, `test_image_*.py` | M |

## 5. Playwright

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 5.1 | Medium | The mock's responses use hand-written types, so they don't break when the API changes | Type them with `api.gen.ts`'s `components['schemas']` | `frontend/e2e/support/mockBackend.ts:5` | M |
| 5.2 | Low | The page calls four routes that no spec exercises and the mock doesn't serve: missing an issue, editing and deleting a review, the agent prompt view | Mock them and add a spec each; then delete their lines from `NOT_MOCKED` | `frontend/src/services/uploadService/mockCoverage.test.ts` | M |

## 6. Missing tests

| # | In plain words | Detail | Size |
|---|---|---|---|
| 6.1 | Feedback counts only other cases | No test, including a document with no case (SQL: needs a real database, so a manual check) | S |
| 6.2 | Low coverage | `cross_document/tools/consistency.py` 68% (lines 61–82); `routers/agents.py` 53% and `agent_prompts.py` 48% (the prompt view) | M |
| 6.3 | The important frontend logic has no unit tests | Vitest for `caseValidation`, `fileValidation`, `request.ts` and `caseSocket.ts` only (the rest is covered by Playwright) | S |
| 6.4 | Load tests are described but not in the repo; the agent sensibility checks are partly tested (`tests/test_evals.py`) | Load tests: `CLOUD_ARCHITECTURE_PLAN.md`, "Stage 2"; checks: `PENDING_TESTS.md` 29, 30 and 31 | — |

## 7. Suggested order

1. Type the mock (5.1), then mock the four unexercised routes (5.2).
2. Move mock-only tests to the in-memory client (3.1).
3. Frontend unit tests and the coverage gaps (6.3, 6.2).
4. Fold the overlapping tests (4.1, then 4.2 when a file is next touched).
