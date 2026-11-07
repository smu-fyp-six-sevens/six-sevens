# Tests: what's still open

Open test work for Sprint 6: backend unit tests (`backend/tests`), Playwright (`frontend/e2e`), Vitest, and the checks CI runs.

**How it was measured:**
- CI run `42235844107` (2026-11-06, `SCRUM-323-Sprint-5-Regression-Testing` at `405a57b`) gave the results and the line coverage;
- a static scan of the test files gave the counts;
- the failing step of the recent failed CI runs showed whether any test is flaky (none failed and then passed unchanged; the risks in 2.3 come from reading the tests);
- each gap comes from reading the code behind it.

> **All test documents are synthetic.** GLDB can't share real loan documents, so every fixture and generated document is synthetic. That limits the whole system, not just this suite: real fraudsters' methods may differ from the ones we inject (`CLAUDE.md`, sponsor-confirmed facts).
>
> **Decisions the tests encode:**
> - **Two company-name matchers, on purpose.** `identifiers.company_names_match` is lenient (it ignores legal suffixes and `&`, but is never typo-tolerant) and decides whether a document matches the case. `company_identity.cross_document_company_names_match` is strict ("A.B.C." differs from "ABC") and is a fraud signal across documents.
> - **Case match is a triage check, not a fraud signal.** A document whose company or person isn't the case's goes to manual review, and the officer can still approve it. Comparing one person across documents is the case analysis's job.
> - **The regression suites test code, not AI accuracy.** Claude is scripted (same reply every run, no cost); whether it picks the right tools, hypotheses and ratings on real documents is checked by the manual live run at the end of each sprint. The case analysis's evidence-request and summary calls are refused, so only their no-Claude fallbacks run.
> - **Known defects are pinned as `xfail(strict=True)`** (`it.fails` in Vitest, `test.fail()` in Playwright), with the reason. A fix makes the test pass, which fails the run until the marker comes off. What the markers don't pin is in 2.5.

## 1. Health

| | Now (6 Nov) |
|---|---|
| Backend test functions | 2,242 in 127 files |
| Backend cases on CI | 2,956 pass, 4 xfail |
| Backend line coverage | 93% |
| Playwright | 253 pass (248 definitions in 14 specs) |
| Vitest | 33 pass, 1 expected fail, in 5 files (of about 144 source files) |
| `data-testid`s in the app | 24 |

## 2. Open items, highest risk first



### 2.2 Idempotency (STABILITY_AUDIT findings 1–3)


The conditional writes those fixes rely on (`fail_if_running`, the superseded guard, `running_started_before`, `detection_results`) are now tested.


### 2.4 Brittle and weak tests

Router and repository tests still assert which mock was called rather than what was stored. 35 tests (6 Nov) assert *only* that a mock was called: 7 in `test_document_repository`, 3 each in `test_sweeper`, `test_case_stage`, `test_doc_worker` and `test_case_auto_analysis`, and the rest across `test_health`, `test_ws` and `test_realtime_listener`. `test_document_repository` and `test_case_repository` also chain mocks 21 and 14 levels deep (`return_value.update.return_value.eq…`), so any query reshuffle breaks them. Only 6 test files use the fake client. Move `document_repository`, `extracted_fields_repository`, `case_pipeline/repository` and `notes_repository` tests to the fake client (`tests/support/fake_supabase.py`) and delete the mock-only tests they replace.

Assertions too weak to fail:
- `test_health.py::test_lifespan_tolerates_db_check_failure_at_startup`, `test_output_validation.py::test_outside_a_run_there_is_no_ceiling` and `test_claude_calls.py::test_an_ordinary_reply_passes_the_refusal_check` only check that nothing raises;
- `test_content_guard.py::test_the_exif_software_payload_is_caught` and `test_cross_document_leads.py::test_the_check_code_is_also_read_from_nested_details` assert only `is not None`;
- `test_case_stage.py::test_finalising_is_permanent_there_is_no_reopen` accepts either 404 or 405;
- `HeaderFilter.test.tsx` wraps `getByRole` in `toBeTruthy()`, which can never be false because `getBy` already throws.

Minor: 52 `@pytest.mark.asyncio` markers are redundant under `asyncio_mode = "auto"`.

ok please fix these

### 2.5 Fixtures and pinned defects



### 2.6 Copies that can drift

leave these for now

- **The approval rules exist three times:** the backend (`document_service.approve`), the frontend (`canApproveDocument`) and the Playwright mock (`missingRequiredFields`). Only `required_fields.json` is checked against the backend; the rest stays in step by convention.
- **Global autouse stubs** in `conftest.py` turn off `start_if_due`, `sync_case_stage` and `is_case_finalised` for every test. Only `test_case_auto_analysis.py` and `test_case_stage.py` run the real ones, so the regression suites never run the real approve → stage → automatic analysis chain.
- **Stale comments:** `playwright.config.ts` and `mockBackend.ts` still say the mock replaces Dify, which is gone.

### 2.7 Smaller holes

leave for now

| Code | Coverage | Untested |
|---|---|---|
| `CR.TENOR`, `FATCA.TIN_FORMAT` | | No test names them |
| `telemetry.record_usage` | `telemetry.py` 76% | Tokens and cost set on the exported span (only the no-usage branch runs). The exporter setup (lines 51–94) is fine to leave |
| `cross_document/tools/comparisons.py` | 80% | Lines 131–159 |
| `events/bus.py` | 83% | Lines 65–76, 94–103 |
| Frontend logic | Vitest covers 5 files of about 144; no coverage tooling | `caseSearch`, `caseValidation`, `fileValidation`, `errorText`, `caseStages`, `request.ts`, `caseSocket.ts`, and hooks such as `useFieldEditing`, `useDocumentSelection`, `useFindingReviews` |
| Coverage gates | `pytest --cov` has no `--cov-fail-under` | The 93% can erode unnoticed |
| Evals and load tests | | `CLAUDE.md` describes them, but none is tracked in the repo |

`events/emulator_setup.py` (0%) is a local dev tool. Fine to leave.

### 2.8 Frontend selectors

The documents table's cells and the dashboard panels are still selected by CSS class, so restyling breaks those specs: 133 CSS-class locators remain, plus 36 `.nth()` and 50 `.first()` calls that depend on order. Give them `data-testid`s.
its ok

### 2.9 Infrastructure checks CI doesn't run
leave for now

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

| 2 | **Idempotency fixes with twice-delivered tests** (2.2) | Duplicate runs cost tokens and overwrite verdicts | S–M |

| 5 | **Move the remaining repository tests to the fake client; strengthen the weak assertions** (2.4) | The biggest brittleness left | L |
| 6 | **Smaller holes and copies that can drift** (2.6, 2.7) | Small | S–M |
| 7 | **CI infrastructure checks** (2.9) | Each has broken silently once | S–M |
| 8 | **Remaining `data-testid`s** (2.8) | Specs break on restyling | S |
