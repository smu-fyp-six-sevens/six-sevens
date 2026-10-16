# six-sevens

Agentic fraud detection for corporate loan documentation. Final Year Project (IS484, SMU) with Green Link Digital Bank (GLDB).

The system automates the forgery-check stage of GLDB's loan origination workflow. Loan officers upload a case's documents, and the system classifies and extracts each one, then runs a detection agent on documents the officer has approved. Credit scoring and offer generation are out of scope.

## Structure

- `backend/` FastAPI app. Document pipeline and detection agent run on LangGraph (`app/langgraph/`), the per-case cross-document analysis and risk scoring live in `app/case_pipeline/` (a case's risk score exists only as the output of an analysis run), with Supabase for data and file storage, and the Claude API for LLM calls.
- `frontend/` React + TypeScript (Vite) app for officers: case upload, document review, and detection results. Live case status comes over a WebSocket.
- `docs/regression/` regression test report.
- `docker-compose.yml` local container setup for both services.

Design and status documents at the root:

- `HOW_IT_WORKS.md` the whole system end to end in plain language: pipeline, detection, flags, case analysis, live updates, configuration and cost logs.
- `CLAUDE.md` project context, decisions, and the planned architecture.
- `FRAUD_DETECTION_MAP.md` how the fraud checks and the detection agent are wired, plus known detection bugs.
- `CROSS_DOCUMENT_AND_RISK_PLAN.md` design of the per-case analysis: hypotheses, investigator tools, verification, risk outlook and guardrails.
- `PENDING_WORK.md` open work only.

## Prerequisites

- Python 3.11 or newer (CI uses 3.13)
- Node.js 22

## Run locally

Backend (from `backend/`):

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

The database is Supabase, and its schema is changed directly there; the repo keeps no SQL migration files. The backend expects these to exist:

| Object | Used for |
|---|---|
| `cases`, `documents`, `extracted_fields`, `langgraph_trace` | the document pipeline, fields and per-document traces |
| `case_analysis_runs` | each case analysis run (its result, risk, supporting documents and trace) |
| `risk_scores` and the `latest_risk_scores` view (newest row per case) | saved scores and the case list |
| `flags` | per-document findings a specialist approved |
| sequence `greenlink_case_id_seq` and trigger `set_greenlink_case_id` on `cases` | readable case ids (`GLDB-<n>`); the trigger skips any id already taken |

The backend role needs `select, insert` on `risk_scores` and `flags` (with row level security policies for insert and read if it is on). Deleting a document's old flags goes through the service key, so no delete policy is required.

Set `CASE_ANALYSIS_ENABLED=false` to switch the case analysis off. `exiftool` should be on the `PATH`; without it the metadata checks report that they could not run.

Optional settings: `QUEUE_WORKER_CONCURRENCY` (documents processed at once, default 4), `DETECTION_CONCURRENCY` (detection runs at once, default 2), `CPU_WORKER_CONCURRENCY` (PDF, OCR and forensic threads, default 1), `DOCUMENT_MAX_MODEL_CALLS` (model calls one document's classify and extract run may make, default 24), `DETECTION_MAX_MODEL_CALLS` (model calls one detection run may make, default 30), `LLM_MAX_RETRIES` (default 1), `LLM_TIMEOUT_SECONDS` (default 300), `TOOL_TIMEOUT_SECONDS` and `SLOW_TOOL_TIMEOUT_SECONDS` (per detection tool, default 60 and 120), `LOG_HTTP_REQUESTS` (log every Supabase call, default off).

The backend logs one `perf` line per request, snapshot and background job (Supabase calls, Claude calls, tokens, cache hits); see `HOW_IT_WORKS.md` §8.

The API runs on `http://localhost:8000`. The interactive docs are at `/docs`.

Frontend (from `frontend/`):

```bash
npm install
npm run dev
```

## Docker

From the repo root:

```bash
docker compose up
```

The frontend is served on `http://localhost:5173` and the backend on `http://localhost:8000`.

## Tests

Backend unit and integration tests (from `backend/`):

```bash
pytest
```

Tests never call real Supabase or Claude. `tests/conftest.py` blocks both, so a test that tries to reach them fails.

Regression tests are tagged by sprint: `pytest -m sprint1` and `pytest -m sprint2`.

Frontend lint, build and end-to-end tests (from `frontend/`):

```bash
npm run lint
npm run build
npm run test:e2e
```

The Playwright suite runs against a mocked backend (`frontend/e2e/support/mockBackend.ts`). The sprint filters are `npm run test:e2e:sprint1` and `npm run test:e2e:sprint2`.

## GitHub Actions

Defined in `.github/workflows/`:

- `ci-cd.yml` runs on pushes to `main` and on pull requests. It runs the backend tests with coverage, a pyright type check (warning only), frontend lint, build and the Playwright suite. On a manual run it also publishes the backend and frontend images to GitHub Container Registry.
- `deploy.yml` deploys both services to Cloud Run. It is manual only (`workflow_dispatch`), so pushes do not deploy.
