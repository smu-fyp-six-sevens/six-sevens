# Refactoring audit

Code smells, cleanup and optimisations across the repo: `backend/app`, `frontend/src`, and the deployment (`deploy/`, `.github/workflows`). Tests are covered in `backend_tests.md`.

Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-22, by reading the code and running static measurements; updated 2026-10-24. Nothing was profiled against production data. Only open items are listed: remove one once it's done.

## Where we are

| | |
|---|---|
| Backend | 43,500 lines in 191 files under `app/` (4,300 of them in `injection/`); 58 route handlers |
| Frontend | 10,300 lines of TS/TSX in 75 files; 5,700 lines of CSS in 14 files |
| Deployment | Terraform 1,000 lines in 12 files, 2 workflows, 7 compose files, Kong and Grafana images |
| Tests | 1,760 backend test functions in 87 files; 107 Playwright tests plus the notes spec |
| Backend gates | ruff clean on `app/` (16 old issues in `doc_gen_scripts/`, `scripts/` and one test, outside CI's scope), pyright clean, 0 `type: ignore`, 14 `noqa` (all re-export `F401`) |
| Frontend gates | lint 0 errors, 0 warnings; `tsc` fails on `CaseOverview.tsx:72` (a teammate's `info` severity missing from a sort table; `PENDING_WORK.md`), and CI doesn't run `tsc` |

---

## 1. Duplicated code

### Frontend

1. **CSS colours:** 587 hard-coded hex values against 368 `var(--...)` uses. The share got worse as new cards were added. Define tokens, then replace. L
2. **Hand-written API types.** `services/uploadService/types.ts` (409 lines) restates the backend's Pydantic models by hand, so a backend field change can drift silently. **In progress:**
   - Done: `backend/scripts/export_openapi.py` writes `frontend/openapi.json`. Every field of a response-only schema is marked required there, because responses always carry them (no route excludes unset or None fields); otherwise every defaulted field would come out optional. `openapi-typescript` 7.4.4 is a devDependency.
   - Left:
     - an `npm run gen:api` script writing `src/services/api.gen.ts`;
     - `types.ts` as aliases of the generated schemas for the ~20 types that have a backend model. Keep hand-written the ones from dict endpoints or only on the frontend (`DetectionReport`, `LangGraphTrace`, `CaseCost`, `IndividualCheckId`, `PreviewKind`). Narrow where the backend is looser: `ContributingFinding.severity` is `str`, `CaseAnalysis.trace` is a list of dicts, and `DocumentRecord` gains the WebSocket-only `has_detection_*` flags;
     - CI: `python scripts/export_openapi.py --check` in the backend job, and `openapi-typescript openapi.json -o src/services/api.gen.ts --check` in the frontend job. Confirm the `--check` flag exists in 7.4.4; the Playwright container may have no git for a `git diff`.

     M

### Deployment

1. **Four Cloud Run definitions repeat the same blocks** (scaling, resources, `lifecycle`, the collector sidecar): `run.tf` (roles, Kong, frontend) and `observability.tf` (Grafana). Kong's collector duplicates the roles' dynamic block. A local module would shrink this, but it isn't worth it before Stage 2 replaces Cloud Run with Helm charts. M, low priority

---

## 2. Wrong place, or doesn't make sense

### Backend

1. **Business rules in the routers.** `routers/documents.py` (903 lines): `approve_document` (101 lines, longer now that it undoes an unpublished approval), `update_extracted_fields` (99) and `update_document_type`. Move them to a document service so the router only translates HTTP. M
2. **Private helpers shared across packages.** `case_pipeline/*` and the external validation checks import `_as_number`, `_rows` and `_parse_field_date` from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`, as `decode_field_value` already is. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
3. **`case_service.py` (516 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), the snapshot builder, and `submission_finalized`. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
4. **`submission_finalized` machinery.** A debounce with timer threads (`document_repository`), a flush before each request returns, and a per-process "last written" cache (`case_service._synced_finalized`), all for a flag nothing reads yet. Decide its shape when automatic case analysis is built: keep it stored, or compute it inside the gate and delete the machinery. Decision pending this sprint
5. **`models.py` (665 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
6. **`case_match.py` (552 lines) at the app root,** with long functions (`_match_document` 97). Split into a `case_match/` package. S-M
7. **`app/langgraph` shares its name with the `langgraph` library.** It works through relative imports, but confuses people and tools. M
8. **The sweeper's local mode reads `bus._tasks`** to wait for in-process handlers. Add a public `bus.drain()`. S

### Frontend

1. **Placeholder screens that look real:**
   - **The case page's Audit Trail card** (`AuditTrailCard.tsx`) always says "No audit events available yet". It never calls `GET /audit/events`, which now serves every officer and agent action, and `GET /audit/cases/{id}/cost`, which serves the case's Claude spend.
   - **`/dashboard`** (`DashboardPage.tsx`, 349 lines) is built on hard-coded `MockCase` data.

   Wire the card to the two endpoints (M). Either back the dashboard with audit-metrics (the case dashboard in `CLOUD_ARCHITECTURE_PLAN.md` section 9) or hide it until then (S), so nobody demos fake numbers by mistake. The dashboard belongs to teammates.

### Deployment and repo

1. **Nothing checks that the generated files match their generators.** `deploy/grafana/build_dashboards.py` writes the dashboard JSON and `docs/architecture/build_diagrams.py` the draw.io file. A CI step that reruns them and fails on a diff (as `subscriptions.json` has) would stop hand edits drifting from the source. S
2. **Python dependencies are ranges with no lock file** (`requirements.txt`). Two builds a week apart can install different versions, and a bad release reaches production unannounced. Lock with `uv pip compile` (or pip-tools), install from the lock in the Dockerfile and CI, and let Snyk scan it. M
3. **The database schema isn't in the repo.** Everything else is code; the schema lives only in Supabase. Export it as versioned migrations (the first step of staging, `CLOUD_ARCHITECTURE_PLAN.md` section 9). M (reading the schema needs your go-ahead)
4. **Keep each doc to one job:** the plan for cloud design and its to-do, HOW_IT_WORKS for behaviour, the Terraform README for operations, PENDING_WORK for open items, `CLAUDE.md` for orientation. Condensed on 2026-10-24; keep status out of the others. S, ongoing

---

## 3. Optimisations

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|
| 1 | `case_service._build_case_snapshot` | About five sequential reads per snapshot (case, documents, fields, analysis, traces) | Two parallel rounds: case, documents and analysis; then fields and traces. Saves about three round trips per push | S-M |
| 2 | `case_service.list_case_summaries` | Reads every document in the database (all cases, with `evidence`) on each queue page load | Select only the columns the rollup uses, filtered with `.in_("case_id", ids)` | S-M |
| 3 | `analysis_repository.get_latest_many` | Now live on the case list (it wasn't at the last review). It fetches every past run of every listed case and keeps the newest per case, so the cost grows with each "Run again" | A `latest_case_analysis` view (`DISTINCT ON (case_id)`), as `latest_risk_scores` already is: a schema change | S |
| 4 | Frontend `CaseQueuePage` | `loadPrefs()` runs three times in the initial state (three `localStorage` reads) | One lazy initializer, three values derived from it | S |
| 5 | `langgraph_trace._write_row` | Update then insert, because `document_id` has no unique key | A unique constraint, then one upsert (schema change) | S |
| 6 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume; if it grows, push cancels to the run instead (a Realtime subscription on its document row) | S, later |
| 7 | Detection CPU work | One thread for all PyMuPDF and forensic work (`CPU_WORKER_CONCURRENCY=1`), so the detection-worker's 2 vCPUs are half idle (also in `PENDING_WORK.md`) | A process pool | M |

**Considered and left alone:**
- **The snapshot payload** (every document on each push): pushes are debounced and built only for open cases. Measure a real case before designing deltas.
- **The document page subscribes to the whole case snapshot**, but only while the document is in flight; a per-document stream would add backend work for almost no gain.
- **`useCaseSnapshot`'s 12 state setters** are batched by React into one render. Cleaner as one object, but not faster.
- **Row memoization on the case page:** the rows are now their own components (`ExtractedDocumentsCard`, `ApprovedDocumentsCard`), so `React.memo` is possible, but the hooks return new objects each render. Measure before adding it.
- **Splitting long pages and long functions** (`DocumentDetailsPage`, `UploadPage`, `App.tsx`; `image_manipulation.py` and its long checks, `check_layout_anomalies`, the cross-document tool builders): decided against for now.
- **Keeping per-page OCR text for the spelling check:** detection runs long after upload, so an in-memory copy would mostly miss.

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The Agents and Cost dashboards now give the data (cost and calls per agent and per unit of work). It changes detection quality, so test against the regression set first.

---

## Suggested order

1. **Finish the generated frontend API types** (frontend 1.2), which is in progress.
2. **Quick wins** (S, an afternoon):
   - the generator check in CI (repo 1);
   - `bus.drain()`;
   - `loadPrefs`.
3. **Lock the Python dependencies** (repo 2). It protects every deploy.
4. **Rules out of `routers/documents.py`** (backend 2.1), the biggest structural win left.
5. **Public field-value helpers** (backend 2.2).
6. **Snapshot reads in parallel** (opt 1), then the case-list queries (opt 2 and 3; opt 3 needs a view).
7. **The Audit Trail card and the mock dashboard** (frontend 2.1) before any demo.
8. **CSS tokens** (frontend 1.1), whenever convenient.
9. **Decide `submission_finalized`** (backend 2.4) as part of automatic case analysis.
