# Tests: where we are and what's next

Test audit at the end of Sprint 5 (2026-11-01, `main`), for Sprint 6. Backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), and the checks CI runs.

**How it was measured:**
- line coverage from CI's backend job (run `41288486211`, 2026-11-01: 2,669 passed, 91% of 20,998 statements);
- a static scan of the test files (test counts, imports, assertions);
- reading the code behind each gap.

No tests were run locally.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.
> - **The regression suites test code, not AI accuracy.** Claude is scripted (same reply every run, no cost); whether it picks the right tools, hypotheses and ratings on real documents is checked by the manual live run at the end of each sprint. The case analysis's evidence-request and summary calls are refused, so only their no-Claude fallbacks run.

## 1. Health

| | Now | 28 Oct |
|---|---|---|
| Backend test functions | 1,996 in 105 files | 1,783 in 93 |
| Backend cases on CI | 2,669 pass, 0 skipped | 2,657 pass, 2 skipped |
| Backend line coverage | 91% | not read |
| Playwright test definitions | 184 in 12 specs | 155 in 9 |
| Frontend unit tests | none | none |

**The detection checks are solid.** Layout, numerical, image, fonts and dates are 86–100% covered, and the regression files run on real fixtures. **The gaps are in the code around them:** repositories, the case agent's tools, and failure paths.

**Brittleness hasn't moved.** Router and repository tests still assert which mock was called rather than what was stored. About 20 tests assert *only* that a mock was called, mostly in:
- `test_document_repository` (6);
- `test_case_stage` (3);
- `test_health` and `test_realtime_listener` (2 each).

They pass as long as the code calls the mock, whatever it does with the result.

## 2. Gaps, highest risk first

### 2.1 Sprint 5's review feature: the repository is half covered

`findings_repository.py` is at **51%**. Routers reach it only through mocks, so the code that writes to Supabase has no test of its own:

| Function | What isn't tested |
|---|---|
| `set_verdict`, `add_missed`, `update_missed`, `delete_missed` | The officer's verdicts and missed issues |
| `sign_off`, `list_sign_offs` | Review sign-off (no Playwright test either) |
| `list_reviews` (date and stage filters) | The data behind the Accuracy tab (`DetectionAccuracyPanel` has no Playwright test either) |
| `find_current`, `move_with_documents`, `delete_case_findings_citing` | Keeping verdicts on the right finding after a rerun, a move or a delete |

These rows are the record of what an officer decided, so a wrong one is a wrong audit answer.

### 2.2 Idempotency (STABILITY_AUDIT findings 1–3)

**Nothing tests a second delivery of the same event.** The fix for each finding should come with a test that delivers it twice and asserts the work runs once:
- `document.approved` to the detection worker;
- `case.analysis.requested` to the case worker;
- two approvals at once.

The conditional writes those fixes will rely on are also untested:
- `case_pipeline/repository.py` is at 64%: `fail_if_running`, `finish_run`'s guard, `running_started_before`;
- `detection_results.py` is at 51%.

### 2.3 Income documents and bank statements (sponsor's highest risk)

| Gap | Where |
|---|---|
| **`PAY.NET` silent pass, still open:** with earning and deduction lines but no stated totals, nothing is compared, and the check reports neither a finding nor a skip. Fix (compute from the lines, or skip with a reason), then test | `numerical_validation/records.py:68` |
| `STAT.ROUND_NUMBERS` and `STAT.LAST_TWO_DIGITS`: no test names them. Needed: flag; no flag; skip under 100 amounts; a genuine fee-heavy statement | `numerical_validation/statements.py` |
| The case agent's transaction tools: the payslip-to-bank-credit evidence. Needed: direction and date filters (including year-less dates), counterparty, an unknown document | `cross_document/tools/transactions.py` **17%**, `comparisons.py` 28%, `reading.py` 32% |
| `PAY.DEDUCTIONS_SUM`: only the no-flag case | `test_sg_reference_checks` |

Lower weight: `CR.TENOR` and `FATCA.TIN_FORMAT` have no test.

### 2.4 The case analysis agent

- `cross_document/agents.py` is at 58%: the hypothesis loop's branches, a judge reply that fails validation, and the run cap.
- `verify.py` is at 73% and `evidence.py` at 77%.

Code verifies every verdict, so the uncovered branches in `verify.py` come first.

### 2.5 Failure and recovery paths

| Code | Coverage | Untested |
|---|---|---|
| `events/bus.py` | 60% | The Pub/Sub publish path, and `publish` raising on failure (a design rule) |
| `events/push.py` | 76% | Push token verification: missing, invalid and wrong-account tokens should each give 401 |
| `workers/detection.py` | 78% | Recovery giving up after the maximum attempts (error saved, earlier report kept, case snapshot pushed) |
| `document_service.py` | 83% | **`bulk_move` has no test** (lines 270–309: finalised target, locked and in-flight documents, both cases resynced); also some `retry` and `cancel` branches |
| `routers/upload.py` | 78% | Lines 88–103 (the upload's failure handling) |
| Approve rollback | none | `events.publish` failing on approve should put the document back to analyzed |

### 2.6 Smaller holes

| Code | Coverage | Note |
|---|---|---|
| `case_pipeline/risk/store.py` | 44% | `save`, `latest_many` (the dashboard's risk column) |
| `notes_repository.py` | 68% | Edit, delete with replies (the placeholder), move |
| `query_stats.py`, `telemetry.py`, `metrics.py` | 57–79% | The LLM callback's labels and cost, trace propagation (traceparent out on publish, continued on push) |
| `routers/audit.py` | 73% | The `/audit/cases/{id}/cost` and `/audit/events` routes |
| `document_repository.flush_syncs` | | A scheduled refresh runs on flush |

`events/emulator_setup.py` (0%) is a local dev tool. Fine to leave.

### 2.7 Frontend

- **No unit tests (no Vitest):** `useCaseSnapshot`, `canApprove`, `useDetection` and the dashboard filters are tested only through the browser.
- **No Playwright test** for review sign-off or the Accuracy tab.
- **Selectors are CSS classes:** 7 `data-testid`s in the whole app, so restyling breaks specs.

### 2.8 Infrastructure checks CI doesn't run

| Check | Command |
|---|---|
| Kong's rendered config | build `deploy/kong`, render with sample env, `kong config parse` |
| Compose files | `docker compose --profile observability --profile frontend config -q` |
| Grafana dashboards | every PromQL query parses (`promtool`) |
| Images build on a PR | `docker build` per target. Today a broken Dockerfile is found by the deploy, after the merge |

### 2.9 Leftovers from Sprint 4

- **Check codes that don't exist** in `test_regression_case_risk.critical_case`: `PS.CPF_RATE` (the rule is `PAY.CPF_RATE`), `TAX.YEAR_OF_ASSESSMENT`, `REG.ISSUER`. Use the real codes and regenerate `case-risk-sprint4.json`.
- **Harnesses reused by importing other test modules** (`from tests import ...` in `test_regression_ai_generation` and `test_regression_invalid_output`). With no `tests/__init__.py`, pytest imports each file under two names. Move them to `tests/support/`.
- `test_regression_case_analysis` waits on `case_pipeline.trace._writer`, a private name.
- **REG-014** (the detection panel stuck on "Cancelling…"), `MA-013` and `RS-001`, from `docs/regression/REGRESSION_REPORT_SPRINT4.md`: not re-checked in this audit.

## 3. Sprint 6 plan

| # | Change | Why | Size |
|---|---|---|---|
| 1 | **The review repository** (2.1), on an in-memory fake Supabase client that asserts stored rows, plus Playwright for sign-off and the Accuracy tab | Sprint 5's main feature; the officer's record | M |
| 2 | **Idempotency fixes with twice-delivered tests** (2.2) | Duplicate runs cost tokens and overwrite verdicts | S–M |
| 3 | **Income and bank statement rules** (2.3): the `PAY.NET` fix, the statistical rules, the transaction tools | The sponsor's highest-risk documents | M |
| 4 | **Failure paths** (2.5): `bulk_move`, push 401s, publish raising, recovery giving up, the approve rollback | The cloud's behaviour is otherwise checked only by hand | M |
| 5 | **`verify.py` and the agent's branches** (2.4) | Code is what makes the agent's verdicts trustworthy | M |
| 6 | **Move the rest of the repository tests to the fake client** (`document_repository`, `extracted_fields_repository`, `case_pipeline/repository`, `notes_repository`); delete the mock-only tests they replace | The biggest brittleness left | L |
| 7 | **Sprint 4 leftovers** (2.9) | Small, closes the regression story | S |
| 8 | **CI infrastructure checks** (2.8) | Each has broken silently once | S–M |
| 9 | **Vitest and `data-testid`s** (2.7) | Logic with no test below the browser | M |

Steps 1 and 6 share the fake client: build it once in step 1.

## 4. Progress (5 Nov, tests only)

The test-only parts of steps 1, 3, 4 and 5 are done, and the smaller holes in 2.6. Backend: 2,925 pass, 2 skipped, 4 expected failures; line coverage 91% → 93%.

| Gap | Now | Tests |
|---|---|---|
| 2.1 Review repository | `findings_repository` 58% → 98%, on an in-memory Supabase client (`tests/support/fake_supabase.py`) that asserts stored rows | `test_findings_repository` |
| 2.2 Conditional writes | `case_pipeline/repository` 64% → 95% (`fail_if_running`, the superseded guard, `running_started_before`); `detection_results` 51% → 100% | `test_case_analysis_repository`, `test_detection_results` |
| 2.3 Statements and payslips | `STAT.ROUND_NUMBERS`, `STAT.LAST_TWO_DIGITS`, `PAY.DEDUCTIONS_SUM` flag; transaction tools 17% → 91%, `reading` 32% → 96%, `comparisons` 28% → 80% | `test_numerical_validation`, `test_case_agent_transaction_tools`, `test_case_agent_evidence_tools` |
| 2.4 Case analysis agent | `verify.py` 73% → 100%; `agents.py` 57% → 97% (plan filtering, a reply asked again, text replies, an unreadable judge reply, the run cap, a failing or hanging check) | `test_verify_verdict`, `test_case_analysis_agent` |
| 2.5 Failure paths | `bulk_move`, push 401s (missing, invalid, wrong account), `publish` raising and the Pub/Sub path, recovery giving up, the approve rollback, the upload's failure handling | `test_bulk_move`, `test_events`, `test_sweeper`, `test_documents` |
| 2.6 Smaller holes | `risk/store` 46% → 100%, `notes_repository` 68% → 93%, `routers/audit` 72% → 100%, `flush_syncs`, the LLM callback's labels and cost, traceparent out on publish and continued on push | `test_risk_store`, `test_notes_repository`, `test_audit_routes`, `test_document_repository`, `test_query_stats`, `test_telemetry` |
| 2.7 Frontend | Vitest set up (`npm test`, run in CI after the type check): `canApproveDocument` (pulled out of `DocumentDetailsPage`), `useCaseSnapshot`, `useDetection`, the dashboard's date periods and status filter. Test ids on the Documents card, its groups and rows, the audit entries, case rows, notes and risk badges, and the specs moved off those CSS classes. The Accuracy tab is covered by the Sprint 5 regression; review sign-off now checks what is sent, who signed and that it survives a reload (the mock keeps sign-offs) | `src/**/*.test.ts(x)`, `case-analysis.spec.ts` |
| 2.9 Sprint 4 leftovers | Real check codes in `critical_case` (`PAY.CPF_CEILING`; the passport typo as the spelling tool's finding; `NOA.DATE_LOGIC`), fixture regenerated, still Critical 89/100. No test module imports another: the harnesses and builders are in `tests/support/` (`pipeline_harness`, `detection_harness`, `detection_documents`, `statements`, `ai_images`, `guard_payslips`). `test_regression_case_analysis` no longer waits on `trace._writer`. REG-014, MA-013 and RS-001 re-checked: unchanged | |

**Defects the new tests found**, each pinned as `xfail(strict=True)` with its reason, so the test fails (and the marker comes off) once fixed:

| Defect | Where | Test |
|---|---|---|
| `PAY.NET` silent pass (known): lines but no totals give neither a finding nor a skip | `numerical_validation/records.py` | `test_payslip_net_with_lines_but_no_totals_is_compared_or_skipped` |
| `STAT.LAST_TWO_DIGITS` counts bank fees, so a genuine statement with many identical charges is flagged. `STAT.BENFORD_1D` already sets fees aside | `numerical_validation/statements.py` | `test_a_genuine_fee_heavy_statement_is_not_flagged_for_its_cents` |
| The 90th percentile is taken at `int(0.9 * (n - 1))`, so for two debits of 250 and 1,800 it reads 250, below the median | `cross_document/evidence.py` `transaction_summary` | `test_the_90th_percentile_is_never_below_the_median` |
| The officer's text reads "The the check" for a named tool after an article ("The compare_identifiers result"): the article only binds to the first half of `_TOOL_RE` | `cross_document/verify.py` `plain_text` | `test_a_named_tool_after_an_article_reads_as_one_article` |
| REG-014, now also at unit level: the cancel-pending flag outlives the run when the cancel answers after the run stopped | `document-details/useDetection.ts` | `useDetection.test.ts` (`it.fails`) |

**Not done (needs code changes or is outside the backend):** the idempotency fixes and their twice-delivered tests (2.2), the `PAY.NET` fix (2.3), `CR.TENOR` and `FATCA.TIN_FORMAT`, step 6 (moving the remaining repository tests to the fake client and deleting the mock-only ones), CI infrastructure checks (2.8), and test ids for the remaining class selectors (the documents table's cells, the dashboard panels).
