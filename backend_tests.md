# Tests: where we are and what's next

Backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), and the checks CI runs. Updated 2026-10-24.

This comes from reading every test file and measuring them with a static script (test counts, mocks, assertions). It does **not** come from a full run, and CI's coverage report hasn't been read yet.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.

## 1. How healthy is the suite?

**Backend:** 1,762 test functions in 87 files. 105 are parametrised, so the suite runs well over 2,000 cases. There are 3,695 plain `assert`s, 222 mock-call assertions and 972 patches. 6 files (about 137 tests) are ignored.

| Group | Share | What to do |
|---|---|---|
| **Solid:** the detection checks (layout 154 tests, numerical 84, image 51, metadata 37, fonts 35), the `test_regression_*` files on real fixtures, case match, identifiers, company identity, cross-document checks, case risk, the external-call guard, events and the sweeper | about 70% | Keep |
| **Brittle:** router and repository tests that check exactly which mock was called, so a refactor breaks them even when behaviour is unchanged | about 20% | Rewrite to check results (step 7) |
| **Low value or not running:** the ignored injection tests, and the remaining tests that only assert a mock was called | about 10% | Move, trim or rewrite |

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

**The six ignored files** (`conftest.collect_ignore`) all test `app/injection/`, the synthetic-data tool, which no image ships. Move them next to the tool (for example `backend/app/injection/tests/`, run by hand) so they stop looking like coverage (step C).

## 2. Service code: what's tested

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
| Frontend types | `npx tsc --noEmit`. Vite's build doesn't type-check, so `CaseOverview.tsx:72` fails today unnoticed (`PENDING_WORK.md`) |

## 3. Playwright

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
- **`CM-009`** checks that the Audit Trail card shows "No audit events available yet". That's a placeholder; the card isn't wired to `/audit/events` (`REFACTORING_AUDIT.md`, frontend 2.5). Rewrite it when the card is wired.
- **`DI-025`** (one of its four tests) only checks that a button is enabled.
- **`CM-026`** sets the checklist answer in the mock itself, so it tests the re-render, not the rule.
- **`DI-045`** forces a case-match state the backend no longer produces for that document.

**Overlap to merge** (keep every test ID as a tag): upload `DI-007/011/015`, `DI-008` into `DT-031`, `DI-002 – DI-006`, the three fill-UEN-and-save tests, `DI-032/034`, `CM-003/005`, `MA-005/007/009`, `CM-025/028/029`.

**Structure:**
- `mockBackend.ts` re-implements some backend rules, so it can drift. Two fixtures are kept in step by backend tests (`test_regression_fixtures.py`).
- Selectors are CSS classes and roles; there are no `data-testid`s.
- There are no frontend unit tests (no Vitest), so helpers like `useCaseSnapshot` and `canApprove` are only tested through the browser.
- `/dashboard` is mock data, so there's nothing real to test there yet.

## 4. Plan

| Step | Change | Why | Size |
|---|---|---|---|
| A | **Tests for the service-code gaps** in section 2: push token 401s, the approve rollback, trace propagation, the LLM callback and `/metrics`, `flush_pending_syncs`, the two `/audit` routes | The cloud behaviour is otherwise checked only by hand | M |
| B | **More infrastructure checks in CI:** Kong config parse, compose config, dashboard PromQL and provisioning, image builds, `tsc --noEmit` (the table in section 2) | Each has broken silently once and been caught by hand | S-M |
| C | **Move the injection tests** out of `tests/` (or turn them back on) | 137 tests that look like coverage but never run | S |
| 7 | **Repository tests on an in-memory fake Supabase client,** asserting stored state instead of mock call chains. Start with `test_document_repository` and `test_extracted_fields_repository` (most mock asserts) | The biggest brittleness left | L |
| 8 | **Playwright:** the case analysis card; cancel and retry for detection and documents (the mock already supports them); login failure and redirect; the triage chip and a faithful `DI-045`; merge the overlaps; add `data-testid`s | Fills the biggest UI gaps | M-L |
| 9 | **Vitest** for the pure frontend helpers (`useCaseSnapshot`, `canApprove`) | Logic with no test below the browser | M |
| 10 | **Read CI's coverage report** (the "Show coverage report" step lists the least-covered modules first), then add tests where it shows holes (start with `query_stats`, `telemetry`, `vision_tool`, the `cross_document` tools, `case_pipeline/risk/summary`) | Turns "no test names it" into facts | M |

**Suggested order:** B and C (small, and they protect deploys), A, then 8, 7, 9 and 10.
