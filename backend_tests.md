# Tests: what's still open

Open test work for Sprint 6: backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), Vitest, and the checks CI runs. Only open items are listed: delete one once it's done.

**How it was measured:**
- CI run `42980050902` (2026-11-11, `main` at `d83a8a6`, after the agent refinements merged) gave the results and the line coverage;
- a static scan of the test files gave the counts;
- the failing step of the recent failed CI runs showed whether any test is flaky (none failed and then passed unchanged);
- each gap comes from reading the code behind it.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.
> - **The regression suites test code, not AI accuracy.** Claude is scripted (same reply every run, no cost); whether it picks the right tools, hypotheses and ratings on real documents is checked by the manual live run at the end of each sprint. The case analysis's evidence-request and summary calls are refused, so only their no-Claude fallbacks run.
> - **A known defect is pinned with `xfail(strict=True)`** (`it.fails` in Vitest, `test.fail()` in Playwright) and a reason, with `raises=` so only the known failure counts. None is pinned today.

## 1. Health

| | 11 Nov |
|---|---|
| Backend test functions | 2,419 in 136 files |
| Backend cases on CI | 3,161 pass, none xfail |
| Backend line coverage | 93% |
| Playwright | 276 pass (in 15 specs) |
| Vitest | 36 in 8 files (of about 160 source files) |
| `data-testid`s in the app | 27 |

## 2. Open items, highest risk first

### 2.1 Mock-only tests

Some tests assert only which mock was called, not what was stored, so they pass whatever the code does with the result. The repositories with the deepest mock chains (`document_repository`, `case_repository`, `extracted_fields_repository`) now run on the in-memory client (`tests/support/fake_supabase.py`). Still mock-only: `test_sweeper`, `test_case_stage`, `test_doc_worker`, `test_case_auto_analysis`, `test_health`, `test_ws` and `test_realtime_listener`. Move what stores something to the fake client and delete the mock-only tests it replaces.

The fake answers only what the repositories use (`select` with a count, `eq`, `neq`, `in_`, `order`, `range`, `update`, `delete`, `upsert`, `rpc`); a repository that uses more fails loudly, so extend the fake instead of going back to mocks. `or_` isn't covered, which is why the case search test keeps a mock.

### 2.2 Copies that can drift (parked)

- **The approval rules exist three times:** the backend (`document_service.approve`), the frontend (`canApproveDocument`) and the Playwright mock (`missingRequiredFields`). Only `required_fields.json` is checked against the backend; the rest stays in step by convention.
- **Global autouse stubs** in `conftest.py` turn off `start_if_due`, `sync_case_stage` and `is_case_finalised` for every test. Only `test_case_auto_analysis.py` and `test_case_stage.py` run the real ones, so the regression suites never run the real approve → stage → automatic analysis chain.

### 2.3 Smaller holes (parked)

| Code | Coverage | Untested |
|---|---|---|
| `CR.TENOR`, `FATCA.TIN_FORMAT` | | No test names them |
| `telemetry.record_usage` | `telemetry.py` 76% | Tokens and cost set on the exported span (only the no-usage branch runs). The exporter setup (lines 51–94) is fine to leave |
| `cross_document/tools/comparisons.py` | 80% | Lines 131–159 |
| `events/bus.py` | 83% | Lines 65–76, 94–103 |
| Frontend logic | Vitest covers 8 files of about 160; no coverage tooling | `caseSearch`, `caseValidation`, `fileValidation`, `errorText`, `caseStages`, `request.ts`, `caseSocket.ts`, and hooks such as `useFieldEditing`, `useDocumentSelection`, `useFindingReviews` |
| Coverage gates | `pytest --cov` has no `--cov-fail-under` | The 93% can erode unnoticed |
| Evals and load tests | | `CLAUDE.md` describes them, but none is tracked in the repo |

`events/emulator_setup.py` (0%) is a local dev tool. Fine to leave.

### 2.4 Frontend selectors (optional)

The documents table's cells and the dashboard panels are still selected by CSS class, so restyling breaks those specs: 133 CSS-class locators remain, plus 36 `.nth()` and 50 `.first()` calls that depend on order. Give them `data-testid`s when a restyle is planned.

### 2.5 Infrastructure checks CI doesn't run (parked)

| Check | Command |
|---|---|
| Kong's rendered config | build `deploy/kong`, render with sample env, `kong config parse` |
| Compose files | `docker compose --profile observability --profile frontend config -q` |
| Grafana dashboards | every PromQL query parses (`promtool`) |
| Images build on a PR | `docker build` per target. Today a broken Dockerfile is found by the deploy, after the merge |
| Lint and types for the tests | CI runs `ruff check app` and `pyright app` only. `ruff check tests` is clean today, but nothing keeps it so |
| Same Python as CI | Local venv is 3.12.8; CI and the Dockerfile use 3.13 |

## 3. Sprint 6 plan

| # | Change | Why | Size |
|---|---|---|---|
| 1 | **Move the remaining mock-only tests to the fake client** (2.1) | The biggest brittleness left | L |
| 2 | **Smaller holes and copies that can drift** (2.2, 2.3) | Small | S–M |
| 3 | **CI infrastructure checks** (2.5) | Each has broken silently once | S–M |
| 4 | **Remaining `data-testid`s** (2.4) | Specs break on restyling | S |
