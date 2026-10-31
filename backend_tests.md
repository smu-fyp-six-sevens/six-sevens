# Tests: where we are and what's next

Backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), and the checks CI runs. Updated 2026-10-28 against `SCRUM-296-Sprint-4-Regression-Testing` (`0afb6a6`, with `main` merged in, including the finding reviews work).

This comes from reading the test files and measuring them with static scripts (test counts, mocks, assertions, which `app` module and which numerical rule code each test names), plus the Sprint 4 developer's own full run (below). CI's coverage report hasn't been read yet.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.
> - **The regression suites test code, not AI accuracy.** Claude is scripted (same reply every run, no cost); whether it picks the right tools, hypotheses and ratings on real documents is checked by the manual live run at the end of each sprint. The case analysis's evidence-request and summary calls are refused, so only their no-Claude fallbacks run.

## 1. How healthy is the suite?

**Backend:** 1,783 test functions in 93 files (recounted 2026-10-28; `main` added `test_finding_reviews` and `test_value_locations`). 106 are parametrised. The full pytest run on `integration` (2026-10-31) collected 2,659 cases: 2,657 pass, 2 skipped (AI-generation provenance, which needs `exiftool`; CI installs it). There are 3,639 plain `assert`s, 252 mock-call assertions and 802 `patch(...)` calls. 163 tests carry their own sprint marker (sprint 1: 10, 2: 41, 3: 60, 4: 52); `conftest.py` tags existing tests centrally on top (sprint 4 tagged: 381 cases; sprints 1–3: 931).

| Group | Share | What to do |
|---|---|---|
| **Solid:** the detection checks (layout 154 tests, numerical 84, image 51, fonts 35), the `test_regression_*` files on real fixtures, case match, identifiers, company identity, cross-document checks, case risk, the audit trail's event content, the external-call guard, events and the sweeper | about 70% | Keep |
| **Brittle:** router and repository tests that check exactly which mock was called, so a refactor breaks them even when behaviour is unchanged | about 20% | Rewrite to check results (step 5) |
| **Low value:** the tests that only assert a mock was called | about 10% | Trim or rewrite |

**Where the brittleness is** (patches, mock-call assertions):

| File | Tests | Patches | Mock asserts | Tests with 4+ patches |
|---|---|---|---|---|
| `test_documents.py` | 74 | 190 | 47 | 16 |
| `test_detection.py` | 29 | 94 | 19 | 15 |
| `test_document_repository.py` | 34 | 64 | 37 | 1 |
| `test_cases.py` | 27 | 48 | 18 | 0 |
| `test_case_service_repository.py` | 21 | 46 | 19 | 0 |

**25 tests assert only that a mock was called**, with no plain `assert`: in `test_document_repository` (6), `test_extracted_fields_repository` (5), `test_doc_worker` (3), `test_classifier_extractor_output` (2), `test_health` (2), `test_realtime_listener` (2), `test_ws` (2), and one each in `test_case_service_repository`, `test_supabase_client` and `test_sweeper`. They pass as long as the code calls the mock, whatever it does with the result.

**Noise:** some `test_ws` tests reach the snapshot code's trace reads, which the guard turns into logged "real Supabase call" errors. They pass, but the log looks like failures. Mock `langgraph_trace` in those tests too.

**Sprint 4 regression files, still to tidy:**
- **Check codes that don't exist** in `test_regression_case_risk.critical_case`: `PS.CPF_RATE` (the rule is `PAY.CPF_RATE`), `TAX.YEAR_OF_ASSESSMENT`, `REG.ISSUER`. Scores are unaffected today (a code only matters when the same check and stated value repeat across documents), but the fixture is meant to reproduce the live run exactly. Use the real codes and regenerate `case-risk-sprint4.json`.
- **Harnesses are reused by importing other test modules** (`from tests import test_regression_ai_pipeline` in `_invalid_output`, `test_regression_detection` in `_ai_generation`). Reuse is the point, but with no `tests/__init__.py` pytest imports each file by its basename, so `tests.X` is a second copy of the module: harmless while the harnesses only patch `app` modules, wrong once one keeps module-level state. Move them to `tests/support/`.
- `test_regression_case_analysis` waits on `case_pipeline.trace._writer`, a private name.

**Open from the Sprint 4 run** (`docs/regression/REGRESSION_REPORT_SPRINT4.md`):
- **REG-014** (low, open): after a cancel, the detection panel can stay on "Cancelling…" if the worker's update reaches the page before the cancel reply; a later retry then shows a disabled "Cancelling…" (`useDetection.ts`, SCRUM-299). Fix, then add a Playwright test that delivers the update first.
- **Two test cases need a team decision:** `MA-013` (only approved documents count towards readiness, by design in `readiness.py`, while the test case says extraction should hide Run Case Analysis too) and `RS-001` (a clean case's reason reads "detection found nothing worth flagging", not "the combined severity of findings"). Change the code or the sheet.

## 2. Income documents and bank statements

The sponsor weights these highest. Every bank statement and payslip rule code is named by some test, but several only once:

| Rule | Tests now | Gap |
|---|---|---|
| `PAY.NET` (`numerical_validation/records.py`) | 2 | **Silent pass:** with earnings and deduction lines but no stated totals, nothing is compared, and the check reports neither a finding nor a skip. Fix (compute net from the lines, or skip with a reason) and test |
| `PAY.DEDUCTIONS_SUM` | 1 (does not flag) | A flagged case; lines printed as negatives against a positive total |
| `STAT.ROUND_NUMBERS`, `STAT.LAST_TWO_DIGITS` (`statements.py`) | 0 | Flag, no flag, skip under 100 amounts; a genuine fee-heavy statement (cents bunched on .50) for the cents test's false-positive rate |
| `BS.TOTALS`, `BS.OVERDRAFT_LIMIT`, `BS.NARRATIVE_AMOUNT` | 1 each (flag only) | The clean case and the skip; overdraft limit of zero |
| Cross-document transaction tools (`cross_document/tools/transactions.py`, 219 lines) | 0 | The salary-credit evidence the agent checks payslips against: direction and date filters (including year-less dates), counterparty, refs issued, unknown document. `tools/comparisons.py` and `tools/reading.py` have none either |

Also untested, lower weight: `CR.TENOR`, `FATCA.TIN_FORMAT`, `case_pipeline/risk/summary.py` (its fallback runs inside `test_regression_case_analysis`, but no test asserts the text, and the grounding check on the model's summary has no test).

## 3. Service code: what's tested

| Code | Tested by | Gap |
|---|---|---|
| Event envelope, registry, in-process dispatch, the push adapter (role routing, malformed message 400, handler error not acked) | `test_events.py` | **Push token verification** (`_verify_push_token`): missing, invalid and wrong-account tokens should each give 401. Untested |
| `subscriptions.json` matches the code; filters within Pub/Sub's 256 bytes | `test_events.py`, `test_sweeper.py` | |
| Doc-worker and detection-worker runs, announcements, an officer cancel in the same process | `test_doc_worker.py`, `test_events.py` | |
| Sweeper thresholds, attempt counting, owner routing, each owner's recovery, the cross-instance cancel watch | `test_sweeper.py` (11) | Sweeper's local `--once` mode |
| Signed WebSocket tickets (forged, wrong purpose, single use, expiry) | `test_sweeper.py`, `test_ws.py` | |
| What each event records: the officer behind every run, field changes from and to, check runs, case creation | `test_audit_trail.py` | |
| Claude cost from usage, cache savings, the per-case cost summary | `test_pricing.py` | The `/audit/cases/{id}/cost` and `GET /audit/events` **routes** (Playwright's audit trail spec runs against the mock) |
| Notes: add, reply, edit, delete, audit events | `test_notes.py`, `test_regression_notes.py` | |
| **Approve undoes an unpublished approval** | none | A test that makes `events.publish` fail on approve and asserts the document went back to analyzed |
| **Trace propagation** (traceparent out on publish, continued on push; the agent label) | none (checked once by a scratch script) | Two small tests with OTel's in-memory exporter |
| **`/metrics` and the LLM callback** (agent and model labels, cost, latency, work histograms) | none (checked once by a scratch script) | One test driving `query_stats.llm_callback` and reading the exposition |
| `document_repository.flush_syncs` | none | One test: a scheduled refresh runs on flush, not later |
| `emulator_setup.py` | none | Fine: a local dev tool, exercised by the compose walkthrough |

**Infrastructure in CI:** Terraform `fmt` and `validate` run in the `terraform` job, Checkov scans Terraform, Dockerfiles and workflows, and the deploy's gateway check is the post-deploy smoke test. These have no CI check, so a broken change can still merge:

| Check | Command |
|---|---|
| Kong's rendered config | build `deploy/kong`, render with sample env, `kong config parse` |
| Compose files | `docker compose --profile observability --profile frontend config -q` |
| Grafana dashboards | every PromQL query parses (`promtool` or a Prometheus container); provisioning loads (`/api/search` lists four dashboards) |
| Images build | `docker build` per target (catches a broken Dockerfile before the deploy does) |

## 4. Playwright

155 tests in 9 specs in the developer's run (a few are generated in loops); `main` has since added 4 definitions (finding reviews, the audit trail):

| Spec | Test definitions |
|---|---|
| `case-management` | 55 |
| `case-analysis` | 21 |
| `upload` | 17 |
| `detection` | 16 |
| `document-review` | 16 |
| `audit-trail` | 11 |
| `checklist` | 9 |
| `notes` | 5 |
| `login` | 4 |

They run the real frontend against `e2e/support/mockBackend.ts`; CI runs them on every push. The mock serves what backend code produced (fixtures in `backend/tests/fixtures/regression/`, kept in step by backend tests; regenerate with `REGENERATE_REGRESSION_FIXTURES=1`) and labels each copied backend rule with the function it mirrors.

**Structure:**
- Selectors are CSS classes and roles; there are no `data-testid`s.
- There are no frontend unit tests (no Vitest), so helpers like `useCaseSnapshot` and `canApprove` are only tested through the browser.
- `/dashboard` is mock data, so there's nothing real to test there yet.

## 5. Plan

In the suggested order: 1 and 2 cover the documents the sponsor weights highest; 3 is small and protects deploys.

| Step | Change | Why | Size |
|---|---|---|---|
| 1 | **Income and bank statement rules** (section 2): the `PAY.NET` silent pass (fix and test), `PAY.DEDUCTIONS_SUM`, the two statistical statement rules, the clean and skip cases of `BS.TOTALS`, `BS.OVERDRAFT_LIMIT`, `BS.NARRATIVE_AMOUNT` | Highest-impact documents; one rule can pass without checking anything | S-M |
| 2 | **Cross-document transaction tools** (section 2), on a `ToolKit` built from `build_context` as `test_company_identity` does | The payslip-to-bank-credit evidence has no test | M |
| 3 | **More infrastructure checks in CI:** Kong config parse, compose config, dashboard PromQL and provisioning, image builds, the generated API type checks (the table in section 3) | Each has broken silently once and been caught by hand | S-M |
| 4 | **Tests for the service-code gaps** in section 3: push token 401s, the approve rollback, trace propagation, the LLM callback and `/metrics`, `flush_syncs`, the two `/audit` routes | The cloud behaviour is otherwise checked only by hand | M |
| 5 | **Repository tests on an in-memory fake Supabase client,** asserting stored state instead of mock call chains. Start with `test_document_repository` and `test_extracted_fields_repository` (most mock-only tests) | The biggest brittleness left | L |
| 6 | **Finish Sprint 4** (section 1): fix REG-014 and test it; decide `MA-013` and `RS-001`; real check codes in the risk fixture; the shared harnesses in `tests/support/`; no private `_writer` | Closes the regression story's open items | S |
| 7 | **`data-testid`s** on cards and rows the specs find by class | Restyling stops breaking tests | S-M |
| 8 | **Vitest** for the pure frontend helpers (`useCaseSnapshot`, `canApprove`) | Logic with no test below the browser | M |
| 9 | **Read CI's coverage report** (the "Show coverage report" step lists the least-covered modules first), then add tests where it shows holes (start with `query_stats`, `telemetry`, `vision_tool`, `metrics`, `case_pipeline/risk/summary`) | Turns "no test names it" into facts | M |
