# six-sevens

Agentic fraud detection for corporate loan documents. SMU IS484 Final Year Project with Green Link Digital Bank (GLDB).

It automates the forgery-check stage of GLDB's loan workflow: officers upload a case's documents; the system classifies and extracts each one, runs a detection agent on the ones the officer approves, then analyses and scores the whole case. Credit scoring and offers are out of scope.

## Layout

- `backend/`: FastAPI. Document pipeline and detection on LangGraph (`app/langgraph/`); case analysis and risk scoring in `app/case_pipeline/`; Supabase for data and files; Claude for the models.
- `frontend/`: React + TypeScript (Vite) for officers. Live updates over a WebSocket.
- `deploy/`: Terraform, Kong, Grafana, compose files. `docs/`: adding a service, architecture diagram and presentation guide, sprint regression reports.

**Docs at the root.** Two describe the system; the rest list open work only and are deleted once empty.

| File | What it's for |
|---|---|
| `README.md` | Setup, running, tests and CI (this file) |
| `HOW_IT_WORKS.md` | Everything as built: behaviour, how detection and the case analysis are wired and why, settings, cost logs, the cloud and its design, how it's tested |
| `PENDING_WORK.md` | Open product and platform work |
| `CLOUD_ARCHITECTURE_PLAN.md` | Open cloud work: next steps, to-dos, Stage 2, agent evaluation |
| `CODEBASE_AUDIT.md` | Open bugs (detection included), performance, code structure and security findings |
| `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md` | Test gaps, and tests owed for recent fixes |

Cloud operations and every problem met: `deploy/terraform/README.md`.

## Run locally

Needs Python 3.11+ (CI uses 3.13), Node.js 22, and `exiftool` on the `PATH` (without it the metadata checks say they couldn't run).

Backend (from `backend/`):

```bash
pip install -r requirements-dev.lock
pip install --no-deps -e .
uvicorn app.main:app --reload --reload-dir app
```

API on `http://localhost:8000`, interactive docs at `/docs` (local runs only). Frontend (from `frontend/`): `npm ci`, then `npm run dev`.

That's all a developer needs: one process runs every role and passes events in memory. Settings are in `HOW_IT_WORKS.md` §7. `python -m scripts.build_land_use_index` pre-builds the URA zoning index.

**Dependencies:** edit `requirements.txt`. The lock files pin every version; regenerate both after a change (from `backend/`, with `pip install uv`), then run the tests:

```bash
uv pip compile requirements.txt --universal --python-version 3.12 -o requirements.lock
uv pip compile pyproject.toml --extra dev -c requirements.lock --universal --python-version 3.12 -o requirements-dev.lock
```

`--upgrade` (all) or `--upgrade-package <name>` (one) takes newer releases.

## Database

Supabase. `backend/sql/schema.sql` is a hand-kept copy of the live schema (tables, functions, grants, RLS), not a migration; a change the live database needs comes as a dated SQL file in `backend/sql/`.

- The backend uses only `SUPABASE_SECRET_KEY`; the browser never talks to Supabase. Every table has RLS on with no policies.
- Deletes rely on cascades: everything keyed by a document or case goes with it. The code removes only stored files and cross-document findings on a deleted document. `audit_events` is never deleted.
- A missing `DELETE` grant shows as Postgres error 42501 ("permission denied for table ...").
- A case's stage is `cases.status`, recomputed from its documents and analysis at startup.

## Services split with Docker (optional)

From the repo root, with uvicorn stopped (Kong takes port 8000):

```bash
docker compose up --build                     # Pub/Sub emulator, Kong :8000, api, workers, audit-metrics
docker compose --profile frontend up --build  # also nginx on :5173
```

Settings come from `backend/.env`. The workers share one container (~3 GB). Walkthrough and debugging: `deploy/terraform/README.md`. The `detection-worker` and `all` images build the URA zoning index (needs internet; set `ALLOW_MISSING_LAND_USE=true` to skip; export `DATA_GOV_SG_API_KEY` first).

## Deploying

Production runs on GKE Autopilot at `sixsevens.me`: a green push to `main` builds and signs the images and releases the Helm chart through Cloud Deploy (`deploy.yml`'s `gke` job); "Deploy to GKE" can also be run by hand. Cloud Run was removed on 2026-11-18 (`HOW_IT_WORKS.md` §9; next steps in `CLOUD_ARCHITECTURE_PLAN.md`). Terraform (`deploy/terraform`, one-time setup in its README) creates the cluster, Pub/Sub, secrets and identities; run `terraform apply` before a push that needs it. Adding a service: [`docs/ADDING_A_SERVICE.md`](docs/ADDING_A_SERVICE.md).

## Tests

- Backend (from `backend/`): `pytest`; by sprint: `pytest -m sprint1` … `sprint5`. Tests can't reach real Supabase or Claude: `tests/conftest.py` blocks both.
- Frontend (from `frontend/`): `npm run lint`, `npm run build`, `npm run test:e2e` (Playwright against a mocked backend; `npm run test:e2e:sprint1` … `:sprint5`).

## GitHub Actions

- **`ci-cd.yml`** (PRs and pushes to `main`, not docs-only): backend (ruff, pyright, `openapi.json` up to date, tests with coverage); frontend (lint, `api.gen.ts` up to date, types, build, Playwright); security (Checkov, pip-audit, `npm audit` high, Terraform fmt/validate; Snyk only on lock-file changes or by hand, non-blocking until its quota resets). All must pass; a push to `main` then deploys.
- **`deploy.yml`**: builds and scans one image per service; with the repo variable `GKE_ENABLED=true` (and `SUPABASE_URL`), its `gke` job signs every image and releases the Helm chart to GKE through Cloud Deploy, which checks the gateway and rolls back a failed rollout (`deploy/terraform/README.md`, "GKE"). Signs in by Workload Identity Federation (repo variables `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`); the only repo secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`.
- **`helm.yml`** (only changes to the chart, the role table, Kong, the event code or the backend Dockerfile; or by hand). Most pushes don't run it. Two jobs:
  - **Values, render and Checkov** (about 2 minutes): the chart's role values match `roles.json`, it lints and renders, and Checkov passes on the rendered chart.
  - **The kind run** (about 8–10 minutes with a warm image cache): only when the templates, the smoke files, Kong, the event code or the Dockerfile changed, or by hand. It runs the chart on kind (whose network plugin enforces network policies) against the Pub/Sub emulator and a stub Supabase: must-pass and must-block calls, one pulled event per worker.
  - No Claude and no Google credentials in either.
