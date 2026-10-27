# Tests: where we are and what's next

Backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), and the checks CI runs. Updated 2026-10-26 against `main` (`4542eac`).

This comes from reading every test file and measuring them with static scripts (test counts, mocks, assertions, which `app` module and which numerical rule code each test names). It does **not** come from a full run, and CI's coverage report hasn't been read yet.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.

## 1. How healthy is the suite?

**Backend:** 1,644 test functions in 82 files (recounted 2026-10-24, after the never-collected injection tests were deleted). 105 are parametrised, so the suite runs well over 1,900 cases. There are 3,339 plain `assert`s, 235 mock-call assertions and 778 `patch(...)` calls. Every file is collected.

| Group | Share | What to do |
|---|---|---|
| **Solid:** the detection checks (layout 154 tests, numerical 84, image 51, metadata 37, fonts 35), the `test_regression_*` files on real fixtures, case match, identifiers, company identity, cross-document checks, case risk, the external-call guard, events and the sweeper | about 70% | Keep |
| **Brittle:** router and repository tests that check exactly which mock was called, so a refactor breaks them even when behaviour is unchanged | about 20% | Rewrite to check results (step 6) |
| **Low value:** the tests that only assert a mock was called | about 10% | Trim or rewrite |

**Where the brittleness is** (patches, mock-call assertions):

| File | Tests | Patches | Mock asserts | Tests with 4+ patches |
|---|---|---|---|---|
| `test_documents.py` | 74 | 192 | 47 | 15 |
| `test_detection.py` | 29 | 94 | 19 | 15 |
| `test_document_repository.py` | 31 | 60 | 37 | 1 |
| `test_cases.py` | 19 | 40 | 11 | 0 |
| `test_extracted_fields_repository.py` | 16 | 14 | 17 | 0 |
| `test_case_service_repository.py` | 9 | 14 | 8 | 0 |

**16 tests assert only that a mock was called**, with no plain `assert`: in `test_extracted_fields_repository` (4), `test_document_repository` (2), `test_doc_worker` (2), `test_health` (2), `test_ws` (2), `test_classifier_extractor_output` (2), `test_supabase_client` (1) and `test_case_service_repository` (1). They pass as long as the code calls the mock, whatever it does with the result.

**Noise:** some `test_ws` tests reach the snapshot code's trace reads, which the guard turns into logged "real Supabase call" errors. They pass, but the log looks like failures. Mock `langgraph_trace` in those tests too.

**Need pytest, not a direct-call script:** `test_auth` (its autouse `_real_auth` undoes the auth bypass; without it the two missing-token and garbage-token 401 tests fail), `test_layout_profiles` and `test_layout_reference` (`monkeypatch`, `tmp_path`, parametrize), `test_land_use` and `test_registry_lookups` (conftest's land-use index and URA key blocks), `test_ai_generation` (`exiftool`, skipped without it). Their direct-run failures are expected to be runner limits; confirm with a pytest run of those six files.

## 2. Income documents and bank statements

The sponsor weights these highest. Every bank statement and payslip rule code is named by some test, but several only once:

| Rule | Tests now | Gap |
|---|---|---|
| `PAY.NET` (`numerical_validation/records.py`) | 2 | **Silent pass:** with earnings and deduction lines but no stated totals, nothing is compared, and the check reports neither a finding nor a skip. Fix (compute net from the lines, or skip with a reason) and test |
| `PAY.DEDUCTIONS_SUM` | 1 (does not flag) | A flagged case; lines printed as negatives against a positive total |
| `STAT.ROUND_NUMBERS`, `STAT.LAST_TWO_DIGITS` (`statements.py`) | 0 | Flag, no flag, skip under 100 amounts; a genuine fee-heavy statement (cents bunched on .50) for the cents test's false-positive rate |
| `BS.TOTALS`, `BS.OVERDRAFT_LIMIT`, `BS.NARRATIVE_AMOUNT` | 1 each (flag only) | The clean case and the skip; overdraft limit of zero |
| Cross-document transaction tools (`cross_document/tools/transactions.py`, 219 lines) | 0 | The salary-credit evidence the agent checks payslips against: direction and date filters (including year-less dates), counterparty, refs issued, unknown document. `tools/comparisons.py` and `tools/reading.py` have none either |

Also untested, lower weight: `CR.TENOR`, `FATCA.TIN_FORMAT`, `case_pipeline/risk/summary.py` (the summary written without a model).

## 3. Service code: what's tested

| Code | Tested by | Gap |
|---|---|---|
| Event envelope, registry, in-process dispatch, the push adapter (role routing, malformed message 400, handler error not acked) | `test_events.py` | **Push token verification** (`_verify_push_token`): missing, invalid and wrong-account tokens should each give 401. Untested |
| `subscriptions.json` matches the code; filters within Pub/Sub's 256 bytes | `test_events.py`, `test_sweeper.py` | |
| Doc-worker and detection-worker runs, announcements, an officer cancel in the same process | `test_doc_worker.py`, `test_events.py` | |
| Sweeper thresholds, attempt counting, owner routing, each owner's recovery, the cross-instance cancel watch | `test_sweeper.py` (11) | Sweeper's local `--once` mode |
| Signed WebSocket tickets (forged, wrong purpose, single use, expiry) | `test_sweeper.py`, `test_ws.py` | |
| Claude cost from usage, cache savings, the per-case cost summary | `test_pricing.py` | The `/audit/cases/{id}/cost` **route**, and `GET /audit/events` |
| Notes: add, reply, edit, delete, audit events | `test_notes.py` | |
| **Approve undoes an unpublished approval** | none | A test that makes `events.publish` fail on approve and asserts the document went back to analyzed |
| **Trace propagation** (traceparent out on publish, continued on push; the agent label) | none (checked once by a scratch script) | Two small tests with OTel's in-memory exporter |
| **`/metrics` and the LLM callback** (agent and model labels, cost, latency, work histograms) | none (checked once by a scratch script) | One test driving `query_stats.llm_callback` and reading the exposition |
| `flush_pending_syncs` | none | One test: a scheduled refresh runs on flush, not later |
| `emulator_setup.py` | none | Fine: a local dev tool, exercised by the compose walkthrough |

**Infrastructure in CI:** Terraform `fmt` and `validate` run in the `terraform` job, Checkov scans Terraform, Dockerfiles and workflows, and the deploy's gateway check is the post-deploy smoke test. These have no CI check, so a broken change can still merge:

| Check | Command |
|---|---|
| Kong's rendered config | build `deploy/kong`, render with sample env, `kong config parse` |
| Compose files | `docker compose --profile observability --profile frontend config -q` |
| Grafana dashboards | every PromQL query parses (`promtool` or a Prometheus container); provisioning loads (`/api/search` lists four dashboards) |
| Images build | `docker build` per target (catches a broken Dockerfile before the deploy does) |
| Generated API types | `python scripts/export_openapi.py --check` and `npm run check:api` exist but aren't in CI |

## 4. Playwright

107 tests in 5 specs, plus main's `notes` spec:

| Spec | Tests |
|---|---|
| `case-management` | 41 |
| `upload` | 24 |
| `document-review` | 17 |
| `detection` | 15 |
| `checklist` | 10 |

They run the real frontend against `e2e/support/mockBackend.ts`; CI runs them on every push.

**Not covered by any spec:**
- the **case analysis card** ("Run analysis", running, result, out of date);
- **clicking Cancel detection** (only that the button shows) and **Retry detection**;
- **document retry and cancel** during extraction. The mock backend already answers `/retry` and `/cancel`, but no spec calls them;
- **login failure and the redirect when logged out.** The mock answers a bad login with 401, but no spec tries one;
- the move-documents dialog;
- WebSocket reconnect;
- the case-match triage chip (a document from another company shows "Sent to manual review" while Approve stays enabled);
- the **Claude Cost** tab.

**Tests that prove less than they look like:**
- **`CM-009`** checks that the Audit Trail card shows "No audit events available yet". That's a placeholder; the card isn't wired to `/audit/events` (`REFACTORING_AUDIT.md`, frontend 2.1). Rewrite it when the card is wired.
- **`DI-025`** (one of its four tests) only checks that a button is enabled.
- **`CM-026`** sets the checklist answer in the mock itself, so it tests the re-render, not the rule.
- **`DI-045`** forces a case-match state the backend no longer produces for that document.

**Overlap to merge** (keep every test ID as a tag): upload `DI-007/011/015`, `DI-008` into `DT-031`, `DI-002 – DI-006`, the three fill-UEN-and-save tests, `DI-032/034`, `CM-003/005`, `MA-005/007/009`, `CM-025/028/029`.

**Structure:**
- `mockBackend.ts` re-implements some backend rules, so it can drift. Two fixtures are kept in step by backend tests (`test_regression_fixtures.py`).
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
| 4 | **Tests for the service-code gaps** in section 3: push token 401s, the approve rollback, trace propagation, the LLM callback and `/metrics`, `flush_pending_syncs`, the two `/audit` routes | The cloud behaviour is otherwise checked only by hand | M |
| 5 | **Playwright:** the case analysis card; cancel and retry for detection and documents (the mock already supports them); login failure and redirect; the triage chip and a faithful `DI-045`; merge the overlaps; add `data-testid`s | Fills the biggest UI gaps | M-L |
| 6 | **Repository tests on an in-memory fake Supabase client,** asserting stored state instead of mock call chains. Start with `test_document_repository` and `test_extracted_fields_repository` (most mock asserts) | The biggest brittleness left | L |
| 7 | **Vitest** for the pure frontend helpers (`useCaseSnapshot`, `canApprove`) | Logic with no test below the browser | M |
| 8 | **Read CI's coverage report** (the "Show coverage report" step lists the least-covered modules first), then add tests where it shows holes (start with `query_stats`, `telemetry`, `vision_tool`, `metrics`, `case_pipeline/risk/summary`) | Turns "no test names it" into facts | M |
