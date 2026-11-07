# six-sevens

Agentic fraud detection for corporate loan documentation. Final Year Project (IS484, SMU) with Green Link Digital Bank (GLDB).

The system automates the forgery-check stage of GLDB's loan origination workflow. Loan officers upload a case's documents, and the system classifies and extracts each one, then runs a detection agent on documents the officer has approved. Credit scoring and offer generation are out of scope.

## Structure

- `backend/` FastAPI app. Document pipeline and detection agent run on LangGraph (`app/langgraph/`), the per-case cross-document analysis and risk scoring live in `app/case_pipeline/` (a case's risk score exists only as the output of an analysis run), with Supabase for data and file storage, and the Claude API for LLM calls.
- `frontend/` React + TypeScript (Vite) app for officers: case upload, document review, and detection results. Live case status comes over a WebSocket.
- `docs/regression/` regression test report.
- `docker-compose.yml` local container setup for both services.

Documents at the root:

- `HOW_IT_WORKS.md` the whole system end to end in plain language: pipeline, detection, flags, case analysis, live updates, configuration and cost logs.
- `FRAUD_DETECTION_MAP.md` how the fraud checks and the detection agent are wired, plus known detection bugs.
- `CROSS_DOCUMENT_AND_RISK_PLAN.md` design of the per-case analysis: deterministic checks, hypotheses, verification, risk outlook and guardrails.
- `REFACTORING_AUDIT.md` code smells and optimisations, with effort estimates.
- `backend_tests.md` state of the test suites and what to add.
- `PENDING_WORK.md` open product and platform work.
- `CLAUDE.md` project context and planned architecture (kept locally; git-ignored).

## Prerequisites

- Python 3.11 or newer (CI uses 3.13)
- Node.js 22

## Run locally

Backend (from `backend/`):

```bash
pip install -r requirements-dev.lock
pip install --no-deps -e .
uvicorn app.main:app --reload --reload-dir app
```

`--reload-dir app` restarts the server only for changes under `app/`, not for tests, scripts or generated documents elsewhere in `backend/`.

**Dependencies:** `requirements.txt` holds the allowed ranges and is the file to edit. `requirements.lock` (the image) and `requirements-dev.lock` (CI and local, adding the dev tools) pin every version, so a build never picks up a release nobody tested. After changing `requirements.txt` or `pyproject.toml`'s dev list, or to take newer releases, regenerate both (from `backend/`, with `pip install uv`), then run the tests:

```bash
uv pip compile requirements.txt --universal --python-version 3.12 -o requirements.lock
uv pip compile pyproject.toml --extra dev -c requirements.lock --universal --python-version 3.12 -o requirements-dev.lock
```

Add `--upgrade` to the first command to move every package to its newest allowed release, or `--upgrade-package <name>` for one.

The database is Supabase, and its schema is changed directly there; the repo keeps no SQL migration files. The backend expects these to exist:

| Object | Used for |
|---|---|
| `cases`, `documents`, `extracted_fields`, `langgraph_trace` | the document pipeline, fields and per-document traces |
| `case_analysis_runs` | each case analysis run (its result, risk, supporting documents and trace) |
| `risk_scores` and the `latest_risk_scores` view (newest row per case) | saved scores and the case list |
| `detection_results` | each document's latest detection report, or its last error |
| `findings` | every finding, the agents' decision and the officer's verdict, and missed issues (written through the `sync_findings` function) |
| `case_notes` | loan officers' notes on a case or one of its documents (a reply is a row with `parent_id`); only a note's author can edit (sets `edited_at`) or delete it, and a deleted note with replies stays as a placeholder (`deleted_at`, text erased) |
| sequence `greenlink_case_id_seq` and trigger `set_greenlink_case_id` on `cases` | readable case ids (`GLDB-<n>`); the trigger skips any id already taken |
| `audit_events` | the audit trail: every domain event, keyed by the event's id (the service role needs `select, insert`) |
| the `supabase_realtime` publication with `documents`, `cases` and `case_analysis_runs` | live updates, including a case analysis finished by the case-worker |

The full schema, with every grant and policy, is in `backend/sql/schema.sql`. The backend role needs `select, insert` on `risk_scores` (with row level security policies for insert and read). `detection_results`, `findings`, `case_notes` and `audit_events` are reached only through the service key, as are the `sync_findings` and `replace_extracted_fields` functions (`grant execute ... to service_role`). Every delete goes through the service key, so no delete *policy* is required, but the service role still needs the `DELETE` privilege on the tables the backend deletes from: `documents`, `extracted_fields`, `detection_results`, `findings`, `case_notes`, `langgraph_trace`, `case_analysis_runs`, `risk_scores` and `cases` (for example `grant delete on public.case_analysis_runs to service_role;`). Without it a delete fails with Postgres error 42501 ("permission denied for table ..."). Deleting a document removes its `extracted_fields`, `findings`, `detection_results`, `case_notes` and `langgraph_trace` rows (by cascade) and its stored file; deleting a case removes its documents, then (by cascade) its `risk_scores`, `case_analysis_runs`, `findings` and `case_notes` rows. On `case_notes` the service role needs `select, insert, delete` and `update (body, edited_at, deleted_at, case_id)`: an author's edit, a deleted note kept as a placeholder above its replies, and a moved document taking its notes to the new case. Every table keyed by a document or a case cascades from it in the database; the backend removes only the stored files and the cross-document findings resting on a deleted document. `audit_events` is never deleted. Deleting one document leaves the case's analysis runs and scores in place; the run is marked out of date.

A case's status lives in the existing `cases.status` column (see `CaseStage` in `backend/app/models.py`); at startup the api moves every case to its current status, so cases still at an older status name catch up. The stage is derived from the case's documents and analysis, so no history is stored.

`exiftool` should be on the `PATH`; without it the metadata checks report that they could not run. Every setting and its default is in `HOW_IT_WORKS.md` §7, including the public-registry keys (a registry check that can't run is skipped, never a finding) and `CASE_ANALYSIS_ENABLED`. `python -m scripts.build_land_use_index` builds the URA zoning index ahead of time. The backend logs one `perf` line per request, snapshot and background job (§8).

The API runs on `http://localhost:8000`. The interactive docs are at `/docs`.

Frontend (from `frontend/`):

```bash
npm ci
npm run dev
```

The two commands above are all a developer needs: one process runs every role (`ROLE=all`) and passes events between them in memory (`EVENT_TRANSPORT=inprocess`). The split below is optional.

## Docker: the services split locally (optional)

From the repo root, with uvicorn stopped (Kong takes port 8000):

```bash
docker compose up --build                     # Pub/Sub emulator, Kong :8000, api, workers, audit-metrics
docker compose --profile frontend up --build  # also the nginx frontend on :5173 (otherwise use npm run dev)
```

Settings come from `backend/.env`, as for uvicorn. The three workers share one container to save memory (about 3 GB in total). `CLOUD_ARCHITECTURE_PLAN.md` section 12 has the walkthrough, how to debug the workers in the IDE, and the emulator's limits.

The backend image has one target per role (`backend/Dockerfile`). The `detection-worker` and `all` targets download URA's land-use map and build the zoning index into the image (about a minute, needs internet, fails if it can't). To build without it, set `ALLOW_MISSING_LAND_USE=true` in the environment before `docker compose build`. Export `DATA_GOV_SG_API_KEY` first to pass it as a build secret.

## Deploying

Cloud Run, from GitHub Actions: every push to `main` deploys once CI passes, and "Deploy to Cloud Run" can also be run by hand. Terraform in `deploy/terraform` creates the services, Pub/Sub, secrets and identities; its `README.md` has the one-time setup. `CLOUD_ARCHITECTURE_PLAN.md` describes the design.

**Adding a service?** Follow [`docs/ADDING_A_SERVICE.md`](docs/ADDING_A_SERVICE.md). Run Terraform first, then push.

## Tests

Backend unit and integration tests (from `backend/`):

```bash
pytest
```

Tests never call real Supabase or Claude. `tests/conftest.py` blocks both, so a test that tries to reach them fails.

Regression tests are tagged by sprint: `pytest -m sprint1` (also `sprint2`, `sprint3`).

Frontend lint, build and end-to-end tests (from `frontend/`):

```bash
npm run lint
npm run build
npm run test:e2e
```

The Playwright suite runs against a mocked backend (`frontend/e2e/support/mockBackend.ts`). Sprint filters: `npm run test:e2e:sprint1` and `:sprint2`.

## GitHub Actions

Defined in `.github/workflows/`:

- `ci-cd.yml` runs on pull requests and on pushes to `main` (not docs-only ones). Jobs:
  - **backend:** ruff, pyright (a type error fails the build; `# type: ignore` and a weakened pyright config are rejected), `openapi.json` matching the models, the tests with coverage;
  - **frontend:** lint, `api.gen.ts` matching `openapi.json`, type-check, build and the Playwright suite (in Microsoft's Playwright container);
  - **security:** Checkov, pip-audit and `npm audit --audit-level=high` (every run), Snyk (only when `backend/requirements.lock` or `frontend/package*.json` change, every Monday, or by hand: the free plan has a monthly quota; non-blocking until it resets), and Terraform `fmt` and `validate`.

  All three must pass. On a push to `main` it then calls `deploy.yml`.
- `deploy.yml` builds one image per service, rolls them out to Cloud Run, checks the gateway, and rolls everything back if a check fails. It also runs by hand. It signs in through Workload Identity Federation (repository *variables* `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`); the only repository secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`. The services read their own keys from Secret Manager.
