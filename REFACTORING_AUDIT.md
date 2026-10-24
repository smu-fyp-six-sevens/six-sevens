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

### Backend

1. **The 12 on-demand detection endpoints** in `routers/detection.py` (527 lines) still repeat gate, download, run. A table of check name to function would remove most of the file. M
2. **`unable_to_assess` and `unable_to_assess_reason`** are declared in 13 result models across the fraud checks. One shared base model. M
3. **Two decoders for JSON-encoded field values:** `numerical_validation/common._decode` and `shared/field_values._decoded` (whose docstring mentions the other). Keep one, in `shared/field_values.py`. S
4. **The doc-worker and the detection-worker repeat run tracking.** Each has its own `_active` task map, `_officer_cancels` set, cancel watcher, and recovery-report pattern (`workers/documents.py`, `workers/detection.py`). A small `RunTracker` in `workers/recovery.py` would hold it once. S-M

### Frontend

1. **Two hooks named `useDocumentActions`** (`pages/document-details/` and `pages/upload/`, with different shapes). Rename one. S
2. **CSS colours:** 587 hard-coded hex values against 368 `var(--...)` uses. The share got worse as new cards were added. Define tokens, then replace. L
3. **Hand-written API types.** `services/uploadService/types.ts` (375 lines) restates the backend's Pydantic models by hand, so a backend field change can drift silently. Generate them from FastAPI's OpenAPI (`openapi-typescript`) and check in CI that they're current. M

### Deployment

1. **Four Cloud Run definitions repeat the same blocks** (scaling, resources, `lifecycle`, the collector sidecar): `run.tf` (roles, Kong, frontend) and `observability.tf` (Grafana). Kong's collector duplicates the roles' dynamic block. A local module would shrink this, but it isn't worth it before Stage 2 replaces Cloud Run with Helm charts. M, low priority

---

## 2. Wrong place, or doesn't make sense

### Backend

1. **Business rules in the routers.** `routers/documents.py` (903 lines): `approve_document` (101 lines, longer now that it undoes an unpublished approval), `update_extracted_fields` (99) and `update_document_type`. Move them to a document service so the router only translates HTTP. M
2. **`skips_detection` lives in the documents router** and the detection router imports it from there (`documents_router.skips_detection`). Move it to the models or the document service. S
3. **Private helpers shared across packages.** `case_pipeline/*` (8 modules) and the external validation checks (9 modules) import `_decode`, `_as_number`, `_rows` and `_parse_field_date` from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
4. **`case_service.py` (516 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), the snapshot builder, and `submission_finalized`. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
5. **`submission_finalized` machinery.** A debounce with timer threads (`document_repository`), a flush before each request returns, and a per-process "last written" cache (`case_service._synced_finalized`), all for a flag nothing reads yet. Decide its shape when automatic case analysis is built: keep it stored, or compute it inside the gate and delete the machinery. Decision pending this sprint
6. **`models.py` (665 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
7. **`case_match.py` (552 lines) at the app root,** with long functions (`_match_document` 97). Split into a `case_match/` package. S-M
8. **`app/langgraph` shares its name with the `langgraph` library.** It works through relative imports, but confuses people and tools. M
9. **The sweeper's local mode reads `bus._tasks`** to wait for in-process handlers. Add a public `bus.drain()`. S
10. **Very long functions** (each a chain of decisions, so splitting is optional):
    - `image_manipulation._check_compression` (267 lines), `_check_replacement` (193), `_check_resolution` (134), `_check_covered_regions` (125);
    - `check_layout_anomalies` (188);
    - `cross_document/tools/transactions.build_transaction_tools` (195) and `comparisons.build_comparison_tools` (137), both lists of closures;
    - `ingestion_service.ingest_document` (118), `extraction_prompts.build_extraction_prompt` (120), `addresses._building_use` (112), `specialists._collect_node` (109). M each
11. **`image_manipulation.py` is 2,519 lines,** the largest production module by far. It has six checks that could each be a module, as `layout_anomalies/` already is. M

### Frontend

1. **Long pages:** `DocumentDetailsPage.tsx` (612 lines), `UploadPage.tsx` (495), `App.tsx` (415: routing, auth and case state together), `CaseQueuePage.tsx` (381), `DashboardPage.tsx` (349). M
2. **Too many props:** `UploadPage` passes about 30 to `DocumentListPanel` (465 lines, rows inline). M
3. **Inline `style={{...}}`:** 31 uses, mostly in `DetectionPanel`, `LangGraphTracePanel`, `CaseAnalysisTracePanel` and `CaseRiskCard`. S
4. **Two leftover `mock-*` class names.** S
5. **Placeholder screens that look real:**
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
- **`DocumentListPanel` row memoization** won't help until the props cleanup (frontend 2.2).
- **Keeping per-page OCR text for the spelling check:** detection runs long after upload, so an in-memory copy would mostly miss.

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The Agents and Cost dashboards now give the data (cost and calls per agent and per unit of work). It changes detection quality, so test against the regression set first.

---

## Suggested order

1. **Quick wins** (S, an afternoon):
   - the generator check in CI (repo 1);
   - the two decoders;
   - `skips_detection`;
   - `bus.drain()`;
   - `loadPrefs`.
2. **Lock the Python dependencies** (repo 2). It protects every deploy.
3. **Rules out of `routers/documents.py`** (backend 2.1), then the **detection endpoint table** (1.1). The biggest structural wins.
4. **Public field-value helpers** (backend 2.3) and **`RunTracker`** (1.4).
5. **Snapshot reads in parallel** (opt 1), then the case-list queries (opt 2 and 3; opt 3 needs a view).
6. **The Audit Trail card and the mock dashboard** (frontend 2.5) before any demo.
7. **Generated frontend API types** (frontend 1.3), then CSS tokens (1.2), whenever convenient.
8. **Decide `submission_finalized`** (backend 2.5) as part of automatic case analysis.
