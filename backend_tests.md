# Test suite review: status and plan (as of 2026-10-15)

Static review only. The suites were not run end to end; only the test files edited below
were run.

## Findings: backend (1,445 test functions, 60 files)

Roughly 13-16% (190-230 tests) can be cut or merged with no loss of detection coverage.

| Tier | What | Action |
|---|---|---|
| Strong (~65%) | Detection-logic tests (numerical, layout, metadata, date, spelling, image), real-fixture `test_regression_*`, `test_external_guard` canary | Keep |
| Brittle (~25%) | Router tests (`test_documents`, `test_detection`, `test_cases`, `test_ws`) and repository tests that assert MagicMock call chains | Refactor (see below) |
| Low value (~10%) | `test_pdf.py` (CI already ignores it), `*_graph_compiles` (`is not None` + `importorskip`), mock-echo tests in `test_supabase_client` | Trim / merge |

Validity caveats:
- Detection tests build the tamper themselves and assert it is found (circular). A small
  corpus of genuine documents asserting no false positives would strengthen them most.
- Possibly untested modules (check with coverage later; indirect coverage not traced):
  `line_shifts.py`, `vision_tool.py`, `telemetry.py`, `cpf_checks`, `gst_checks`,
  `bank_checks`, `payslip_inputs`, `giro_dates`.

## Findings: Playwright (72 tests, 4 specs)

Not too many; needs additions, not trimming.
- Merge ~8-12 overlaps, keeping every ID as a tag: upload `DI-007/011/015`, `DI-008` into
  `DT-031`, `DI-002..006`, the three fill-UEN-and-save tests, `DI-032/034`, `CM-003/005`.
- Weak: `DI-025` credit report (never clicks Approve), `CM-009` (asserts a placeholder).
- Validity: `mockBackend.ts` re-implements backend rules (approve gating, confidence bump,
  routing), so some tests check the mock, not the backend.
- Missing: detection panel (core output), login, document retry/cancel/reject, case-match,
  MoveDocumentsDialog, websocket reconnect. The mock already supports most of these.
- Cleanup: `test.ts` references a `login.spec.ts` that never existed; CSS-class selectors,
  no `data-testid`.

## Decisions made

- `company_names_match` exists twice on purpose, so it was renamed by role:
  - `case_match.case_company_names_match`: does a document match the case / registry.
  - `company_identity.cross_document_company_names_match`: fraud signal across documents.
- SCRUM-109: current behaviour is correct. "Confidences sum to 1" holds whenever there is at
  least one candidate. No candidates returns `[]`, and the document ends up
  `unrecognised` / confidence 0.0 / manual review. The Jira wording should be amended to match.

## Done

- Renames above (definitions, call sites in `case_match.py`, `acra.py`,
  `company_identity.py`, and both test files).
- `test_regression_top3.py`: docstring and comment state the sum-to-1 rule applies only when
  candidates exist; the no-match test is renamed `..._returns_no_candidates_and_needs_review`
  and now also asserts `needs_manual_review is True`.
- Parametrized (same case counts, fewer functions):
  - `test_date_anomalies.py`: 39 cases, 39 -> 14 functions.
  - `test_numerical_validation.py`: 121 cases, 121 -> 79 functions (44 tests folded into
    `_FLAGGED_ONCE` / `_NOT_FLAGGED`).
  - The generated tables use mixed quote styles and some long lines; run the formatter.

## Still to do (suggested order)

1. `test_detection.py` router boilerplate (~30-40 tests): one `detection_env` fixture
   replacing the 5-patch `with` blocks, and one parametrized gate test over the endpoints
   (requires-full-detection-first, document-missing, fetch-failure -> 502). Assert on the
   response, not `call_args`. Keep one or two endpoints running a real check on a tiny PDF.
   Same pattern in `test_documents.py` for not-found / 502.
2. `test_metadata_analysis.py`: fold the "does not flag X" clusters (5 + 4). Different helper
   from the other two files, so the fold script needs a small variant.
3. `*_graph_compiles` in `test_langgraph.py`: one parametrized test; make the langgraph
   import a hard failure instead of `importorskip`.
4. `test_supabase_client.py`: drop the two mock-echo tests (`builds_with_url_and_secret_key`,
   the echo half of `builds_once_and_caches`). Keep the missing-config and caching tests.
5. Repository tests: replace MagicMock chains with an in-memory fake Supabase client and
   assert state. Optionally a schema check or `@pytest.mark.integration` tests to catch
   wrong column names.
6. `test_pdf.py`: no action in CI (ignored). If it is ever re-enabled, delete the 7 no-assert
   tests and the `in {True, False}` tautology.
7. False-positive corpus of genuine documents for the detection checks.
8. Playwright: detection-panel spec, login spec, fix `DI-025`, merge overlaps, `data-testid`s.
9. Coverage run to confirm the possibly-untested modules.

## Pre-existing failures (also fail at clean HEAD, unrelated to the changes above)

In `test_case_match.py` / `test_company_identity.py`:
- `test_case_snapshot_includes_case_match_computed_from_plain_fields` (incumbency
  'missing' instead of 'verified')
- `test_tool_reports_each_discrepancy_as_a_finding`
- `test_tool_without_case_has_nothing_to_compare`
- `test_content_validation_specialist_owns_the_tool`

The last three hit the conftest "real Supabase call" guard. Worth checking, since "all pass"
was the assumption.
