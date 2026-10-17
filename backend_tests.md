# Test suite review: status and plan (as of 2026-10-16, re-audited after merging main)

Static review (AST scan plus reading). Numbers come from the user's own full runs and
from `pytest --collect`-equivalent static counts; the audit itself did not run the suites.

## Findings: backend (1,544 test functions, 71 files; 1,671 cases collected)

- **What actually runs:** `conftest.py` has a `collect_ignore` list of six files
  (`test_pdf.py`, `test_store.py`, `test_field_text.py`, `test_fraud_injections.py`,
  `test_injection_router.py`, `test_injectors.py`) = 140 functions that never run, in CI or
  locally. That leaves **~1,404 active functions** expanding to **1,671 cases** (60 functions
  are parametrized).
- **Last full run (CI-equivalent, before the final fixture fix):** 1,665 passed, 4 skipped,
  1 xfailed, 1 failed. The one failure (the shared detection-report fixture) is fixed; the
  other 1,665 are unaffected.
- **Redundancy is down.** Same-shape clusters (identical once literals are stripped) fell from
  53 clusters / 142 tests to 38 / 87 after parametrizing `test_date_anomalies` and
  `test_numerical_validation`. Roughly 8-10% (~110-140 active tests) can still be cut or merged.

| Tier | What | Action |
|---|---|---|
| Strong (~65%) | Detection-logic tests (numerical, layout, metadata, date, spelling, image), real-fixture `test_regression_*`, `test_case_match` / `test_company_identity` / `test_case_risk` / `test_supporting_documents`, `test_external_guard` canary | Keep |
| Brittle (~25%) | Router tests (`test_documents`, `test_detection`, `test_cases`, `test_ws`) and repository tests that assert MagicMock call chains; 56 tests use >= 4 `patch`es (was 45) | Refactor (see Still to do) |
| Low value (~10%) | `*_graph_compiles` (4 tests, `is not None` + `importorskip`), mock-echo tests in `test_supabase_client`, the 140 never-run injection-tooling tests | Merge / trim / decide |

Static smells (active files only; the ignored files hold 12 of the 19 flagged):
- 3 tests with no explicit assertion: two `test_case_broadcaster` "does not raise" contracts
  (fine) and `test_public_holidays::test_only_bank_statements_make_the_holiday_lookup`, which
  is a legitimate "must not be called" test (the patched function raises if called).
- 4 `is not None` graph-compile tests in `test_langgraph.py`.
- 40 tests assert only on mock calls (was 42); 644 are single-assert (normal).

Validity caveats:
- Detection tests build the tamper themselves and assert it is found (circular). A small
  corpus of genuine documents asserting no false positives would strengthen them most.
- **Real bug tracked by a strict xfail:** `test_regression_case_management` (REG-005) shows the
  API saves a company name over 100 characters; only the UI limits it. Fix the API, then the
  xfail flips and must be removed.
- **Two SCRUM-70 regression tests are skipped** (company-name mismatch in detection): that
  tool moved to the case pipeline. Coverage now lives in `test_company_identity.py`; the two
  scenarios still need porting.
- **Modules no test references by name (13)** - treat as leads, indirect coverage not traced:
  `case_pipeline/cross_document/agents.py` (371 lines, the cross-document investigator and the
  largest gap), `fraud_detection/layout_anomalies/line_shifts.py` (637), `query_stats.py`,
  `langgraph/vision_tool.py`, `numerical_validation/payslip_inputs.py`,
  `case_pipeline/risk/outlook.py`, `telemetry.py`, `sg_reference/banks.py`,
  `external_validation/giro.py`, `llm_io.py`, `cross_document/bias.py`,
  `case_pipeline/categories.py`, `cpu_pool.py`.
- **Slowest file:** `test_ai_generation.py` (~16-25s of a ~42-50s suite): 29 detector runs
  (~0.27s each) plus the test's own 768x768 `_scene()` fixture rebuilt ~20 times with identical
  arguments. Caching `_scene()` would save a few seconds.

## Findings: Playwright (102 tests, 5 specs)

Larger and better aimed than at the last audit (72 tests, 4 specs): `case-management` 38,
`upload` 22, `document-review` 17, `detection` 15 (new), `checklist` 10. No `.skip`, `.fixme`
or `waitForTimeout`; chromium only, one CI retry. Not too many; still needs additions.

Gaps closed since the last audit: the detection panel (FD-015 - FD-020, MA-009/MA-011),
approve/reject locking (MA-005 - MA-012), case update and delete (CM-014 - CM-029), and the
login happy path (CM-019, incidentally, with `loggedIn: false`).

Still missing (no spec mentions them): the case analysis run / cross-document validation card,
login failure and the unauthenticated redirect, clicking **Cancel detection** (only its
visibility is asserted), detection error and retry states, document retry/cancel during
extraction, the move-documents dialog, websocket reconnect, and the **case-match triage
chip** (a foreign-company or foreign-person document showing "Sent to manual review" with
Approve still enabled; only backend tests cover it).

Weak or oversold:
- `DI-025` credit report only asserts the button is enabled (never clicks Approve) and uses a
  2s timeout; `CM-009` asserts a "No audit events" placeholder.
- `CM-026` sets `fulfilled_requirements = []` in the mock itself, so it checks the UI
  re-renders, not that the backend recomputes.
- `DI-045` forces a passport's case-match `status` to `mismatch` in the mock. The real backend
  now returns `manual_review` for a person not on the incumbency, so this models a state that
  document no longer reaches. A true pairwise `mismatch` scenario, or a triage assertion,
  would be more faithful.
- `FD-015`, `FD-017`, `FD-020` contain `expect(C028_REPORT...)` assertions on the JSON fixture,
  which test the fixture, not the UI (the backend test already keeps it honest).

Overlap (merge, keeping every ID as a tag): upload `DI-007/011/015`, `DI-008` into `DT-031`,
`DI-002 - DI-006`, the three fill-UEN-and-save tests, `DI-032/034`, `CM-003/005`, plus new
`MA-005/007/009` (all approve the same credit report) and `CM-025/028/029` (repeat the delete-
case flow).

Validity and hygiene:
- `mockBackend.ts` re-implements backend rules (approve gating, confidence bump, routing, now
  also case PATCH/DELETE and document DELETE). Two fixtures are kept in sync by backend tests
  (`required_fields.json`, `detection-report-c028.json`); the rest can drift.
- `CM-024`'s own comment notes the not-found page shows the API error verbatim
  (`Failed to fetch document (404): {"detail":...}`) - a UX leak worth fixing.
- `e2e/support/test.ts` still says "login.spec.ts opts out of the token"; no such file exists
  (the login flow lives in `case-management.spec.ts`). CSS-class selectors throughout, no
  `data-testid`.

## Decisions made

- `company_names_match` exists twice on purpose, so it was renamed by role:
  - `case_match.case_company_names_match`: does a document match the case / registry.
  - `company_identity.cross_document_company_names_match`: fraud signal across documents.
- SCRUM-109: current behaviour is correct. "Confidences sum to 1" holds whenever there is at
  least one candidate. No candidates returns `[]`, and the document ends up
  `unrecognised` / confidence 0.0 / manual review. The Jira wording should be amended.
- **Case match has two roles, and both are kept:**
  1. *Triage* (before approval): a document whose **company or person** is not the case's
     (any mismatch against the certificate of incumbency) gets `manual_review`,
     `case_mismatch=True`, `can_proceed_to_fraud_detection=False`. The officer is flagged and
     can still approve (neither the approve handler nor the frontend `canApprove` reads the
     case match).
  2. *Fraud signal* (after approval): detection adds identity findings for any
     `case_mismatch`, and a mismatch only *between* documents (same person, different ID or
     name) keeps status `mismatch` and proceeds. The case pipeline's cross-document tools are a
     further layer.
  - Note: `can_proceed_to_fraud_detection` is advisory; nothing in the backend reads it.
  - This reconciles commit `30907e4` ("everything proceeds") with main's SCRUM-110 tests and
    jianing's `161bd0b`, which is on `origin/feature/SCRUM-253-identity-fields-matching` but
    not merged. Its three files were applied by hand and extended to persons.

## Done

- Renames above (definitions, call sites in `case_match.py`, `acra.py`,
  `company_identity.py`, and both test files).
- `test_regression_top3.py`: docstring and comment state the sum-to-1 rule applies only when
  candidates exist; the no-match test now also asserts `needs_manual_review is True`.
- Parametrized (same case counts, fewer functions): `test_date_anomalies.py` 39 -> 14
  functions, `test_numerical_validation.py` 121 -> 79 functions (44 tests folded into
  `_FLAGGED_ONCE` / `_NOT_FLAGGED`). The generated tables use mixed quote styles; run the
  formatter.
- Stale tests brought in line with the app (all from the app moving on, not app bugs):
  `test_regression_ai_pipeline` (extraction `max_tokens` 4096 -> 16000, 36 tests),
  `test_regression_approval`, `test_regression_fixtures` (regenerated `required_fields.json`),
  `test_supporting_documents` (recommendations now come from a saved analysis),
  `test_company_identity` (tool moved to the case pipeline), `test_ws`,
  `test_document_repository`, `test_realtime_listener` (and one vacuous test fixed),
  `test_case_risk` (interrupted analysis), `test_case_match` (batch-fetched fields),
  `test_detection_tools`.
- One real app gap fixed along the way: statement-cast paths in
  `numerical_validation/runner.py` now record the specialist's bindings in `bindings_applied`.
- Merged `origin/main`. Its new regression files needed three path fixes
  (`sg_reference` and the binder moved, the holiday stub moved) plus a `Repo` fake that
  returns the record; one `store` fixture stub for the identity step and for flag saving.
- Person triage added to `case_match.py` (see Decisions); `test_case_match`,
  `test_regression_case_match` (DI-042) updated.
- `detection-report-c028.json` regenerated with deterministic `flag_id`s (`flag-1..4`); only
  `ai_accepted` and `flag_id` were added.

## Still to do (suggested order)

1. Commit and push: the merge from main and the work after it are local. CI failed earlier
   because main's tests met this branch's moved modules; pushing resolves it.
2. Run Playwright (`detection.spec.ts` changed with `161bd0b`, and the report fixture was
   regenerated); add the triage-chip test and fix `DI-045`'s mock state.
3. Port the two skipped SCRUM-70 company-name scenarios to the case-pipeline suite, or retire
   them with Jira.
4. `test_detection.py` router boilerplate (~30-40 tests): one `detection_env` fixture replacing
   the 5-patch `with` blocks, and one parametrized gate test over the endpoints
   (requires-full-detection-first, document-missing, fetch-failure -> 502). Assert on the
   response, not `call_args`. Same pattern in `test_documents.py` for not-found / 502.
5. `test_metadata_analysis.py`: fold the "does not flag X" clusters (now 18 tests in clusters
   of 5, 4, 3, 3...); similar smaller clusters in `test_case_service` (10),
   `test_extraction_prompts` (7), `test_langgraph` (6). The fold script needs a variant per
   helper.
6. `*_graph_compiles` in `test_langgraph.py`: one parametrized test; make the langgraph import
   a hard failure instead of `importorskip` (it skips silently if the dependency breaks).
7. `test_supabase_client.py`: drop the two mock-echo tests. Keep the missing-config and
   caching tests, which exercise real logic.
8. Repository tests: replace MagicMock chains with an in-memory fake Supabase client and assert
   state; optionally a schema check or `@pytest.mark.integration` tests for wrong column names.
9. Decide the fate of the six `collect_ignore` files (140 tests): delete, move out of
   `tests/`, or re-enable. Today they are dead weight that still looks like coverage.
10. False-positive corpus of genuine documents for the detection checks.
11. Playwright additions: login failure and unauthenticated redirect, case analysis card,
    cancel/retry detection, merge the overlaps above, `data-testid`s, remove the stale
    `login.spec.ts` comment.
12. Cache `_scene()` in `test_ai_generation.py`.
13. Coverage run to confirm the unreferenced modules, starting with
    `cross_document/agents.py`. Fix REG-005 (API company-name length) and drop its xfail.

## Pre-existing failures

None open at the last full run apart from the detection-report fixture test, which is fixed.
Earlier failures that were stale tests are listed under Done. Tests that log swallowed
"real Supabase call" errors (for example in `test_ws`) still pass but are noisy; patch the
repositories they touch.
