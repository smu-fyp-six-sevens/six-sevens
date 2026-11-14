# six-sevens

Agentic fraud detection for corporate loan documents. SMU IS484 Final Year Project with Green Link Digital Bank (GLDB).

It automates the forgery-check stage of GLDB's loan workflow: officers upload a case's documents; the system classifies and extracts each one, runs a detection agent on the ones the officer approves, then analyses and scores the whole case. Credit scoring and offers are out of scope.

## Layout

- `backend/`: FastAPI. Document pipeline and detection on LangGraph (`app/langgraph/`); case analysis and risk scoring in `app/case_pipeline/`; Supabase for data and files; Claude for the models.
- `frontend/`: React + TypeScript (Vite) for officers. Live updates over a WebSocket.
- `deploy/`: Terraform, Kong, Grafana, compose files. `docs/`: adding a service, architecture diagram and presentation guide, sprint regression reports.

**Docs at the root:**

| File | What it's for |
|---|---|
| `HOW_IT_WORKS.md` | How the system behaves today, settings, cost logs |
| `FRAUD_DETECTION_MAP.md` | How the fraud checks and agents are wired; known detection bugs |
| `CROSS_DOCUMENT_AND_RISK_PLAN.md` | Design of the case analysis and risk score |
| `CLOUD_ARCHITECTURE_PLAN.md` | What's left in the cloud: to-dos, staging, Stage 2, open decisions (§8–11); if and how to move to Kubernetes, planned only (§13–24). The cloud as built: `HOW_IT_WORKS.md` §9 and `deploy/terraform/README.md` |
| `CODEBASE_AUDIT.md` | Open bugs, stability, performance, code structure and security findings |
| `TEST_SUITE_AUDIT.md`, `PENDING_TESTS.md` | Test gaps, and tests owed for recent fixes |
| `PENDING_WORK.md` | Open product and platform work |

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

Cloud Run from GitHub Actions: a green push to `main` deploys, or run "Deploy to Cloud Run" by hand. Terraform (`deploy/terraform`, one-time setup in its README) creates the services, Pub/Sub, secrets and identities; run `terraform apply` before a push that needs it. Adding a service: [`docs/ADDING_A_SERVICE.md`](docs/ADDING_A_SERVICE.md).

## Tests

- Backend (from `backend/`): `pytest`; by sprint: `pytest -m sprint1` … `sprint5`. Tests can't reach real Supabase or Claude: `tests/conftest.py` blocks both.
- Frontend (from `frontend/`): `npm run lint`, `npm run build`, `npm run test:e2e` (Playwright against a mocked backend; `npm run test:e2e:sprint1` … `:sprint5`).

## GitHub Actions

- **`ci-cd.yml`** (PRs and pushes to `main`, not docs-only): backend (ruff, pyright, `openapi.json` up to date, tests with coverage); frontend (lint, `api.gen.ts` up to date, types, build, Playwright); security (Checkov, pip-audit, `npm audit` high, Terraform fmt/validate; Snyk only on lock-file changes, weekly or by hand, non-blocking until its quota resets). All must pass; a push to `main` then deploys.
- **`deploy.yml`**: one image per service, rolled out to Cloud Run, gateway checked, everything rolled back on failure. Signs in by Workload Identity Federation (repo variables `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`); the only repo secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`.
