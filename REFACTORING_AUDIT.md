# Refactoring audit

Code smells, cleanup and optimisations across the repo: `backend/app`, `frontend/src`, and the deployment (`deploy/`, `.github/workflows`). Tests are covered in `backend_tests.md`.

Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-22, by reading the code and running static measurements; updated 2026-10-24. Nothing was profiled against production data. Only open items are listed: remove one once it's done.

## Where we are

| | |
|---|---|
| Backend | 43,500 lines in 191 files under `app/` (4,300 of them in `injection/`); 58 route handlers |
| Frontend | 10,300 lines of TS/TSX in 75 files; 5,800 lines of CSS in 15 files |
| Deployment | Terraform 1,000 lines in 12 files, 2 workflows, 7 compose files, Kong and Grafana images |
| Tests | 1,644 backend test functions in 82 files; 107 Playwright tests plus the notes spec |
| Backend gates | ruff clean on `app/` (16 old issues in `doc_gen_scripts/`, `scripts/` and one test, outside CI's scope), pyright clean, 0 `type: ignore`, 14 `noqa` (all re-export `F401`) |
| Frontend gates | lint 0 errors, 0 warnings; `tsc` fails on `CaseOverview.tsx:72` (a teammate's `info` severity missing from a sort table; `PENDING_WORK.md`), and CI doesn't run `tsc` |

---

## 1. Duplicated code

### Frontend

1. **105 one-off hex colours** are still written inline in the stylesheets (each appears once, so they were not merged into a token). Every repeated colour is a token in `styles/tokens.css`. When you restyle a component, move its one-offs into the palette (or onto the nearest existing token). S, as you go

   | Group | Count | What they are |
   |---|---|---|
   | **Dashboard page** | 45 | The mock `/dashboard` (teammates' page): about 23 slate greys and blues (sidebar, nav, icons, avatars), its own red, orange and green markers, a lilac avatar. Leave until the dashboard is rebuilt |
   | **Medium risk (orange)** | 22 | "Medium" on the case page (`case-summary`, `case-overview`, `upload`, `manual-review`, `document-details`): pale backgrounds, gauge bands, borders, dark text. A light-to-dark scale, but almost no two files use the same shades |
   | **High risk and errors (red)** | 16 | The same for "high": gauge band, score text, error borders, danger hover states |
   | **Low risk and success (green)** | 8 | Success messages, the toast border, approve-button greens |
   | **Greys and small blues** | 8 | Scattered border and text greys, a blue focus outline and hover blue, a disabled-input grey |
   | **Outliers** | 6 | Two vivid pink-reds in `.approval-warning` that match nothing else, two sand colours, a lilac avatar, leftovers |

   The risk groups (46 colours) are one risk scale (low, medium, high) written out per file. Replace them with semantic tokens (`--risk-medium-bg`, `--risk-medium-text`, ...) when the risk cards are restyled, so the whole scale changes in one place.

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

### Frontend

1. **Placeholder screens that look real:**
   - **The case page's Audit Trail card** (`AuditTrailCard.tsx`) always says "No audit events available yet". It never calls `GET /audit/events`, which now serves every officer and agent action, and `GET /audit/cases/{id}/cost`, which serves the case's Claude spend.
   - **`/dashboard`** (`DashboardPage.tsx`, 349 lines) is built on hard-coded `MockCase` data.

   Wire the card to the two endpoints (M). Either back the dashboard with audit-metrics (the case dashboard in `CLOUD_ARCHITECTURE_PLAN.md` section 9) or hide it until then (S), so nobody demos fake numbers by mistake. The dashboard belongs to teammates.

### Deployment and repo

1. **Nothing checks that the generated files match their generators.** Three sources write files:
   - `deploy/grafana/build_dashboards.py` writes the dashboard JSON;
   - `docs/architecture/build_diagrams.py` writes the draw.io file;
   - the frontend API types come from `backend/scripts/export_openapi.py` (`frontend/openapi.json`) and `npm run gen:api` (`src/services/api.gen.ts`).

   For the API types, `python scripts/export_openapi.py --check` and `npm run check:api` already exist; they just aren't in CI yet. A CI step that reruns all three and fails on a diff (as `subscriptions.json` has) would stop hand edits drifting from the source. S
2. **Python dependencies are ranges with no lock file** (`requirements.txt`). Two builds a week apart can install different versions, and a bad release reaches production unannounced. Lock with `uv pip compile` (or pip-tools), install from the lock in the Dockerfile and CI, and let Snyk scan it. M
3. **The database schema isn't in the repo.** Everything else is code; the schema lives only in Supabase. Export it as versioned migrations (the first step of staging, `CLOUD_ARCHITECTURE_PLAN.md` section 9). M (reading the schema needs your go-ahead)
4. **Keep each doc to one job:** the plan for cloud design and its to-do, HOW_IT_WORKS for behaviour, the Terraform README for operations, PENDING_WORK for open items, `CLAUDE.md` for orientation. Condensed on 2026-10-24; keep status out of the others. S, ongoing

---

## 3. Optimisations

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|
| 1 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume; if it grows, push cancels to the run instead (a Realtime subscription on its document row) | S, later |

**Considered and left alone:**
- **The snapshot payload** (every document on each push): pushes are debounced and built only for open cases. Measure a real case before designing deltas.
- **The document page subscribes to the whole case snapshot**, but only while the document is in flight; a per-document stream would add backend work for almost no gain.
- **`useCaseSnapshot`'s 12 state setters** are batched by React into one render. Cleaner as one object, but not faster.
- **Row memoization on the case page:** the rows are now their own components (`ExtractedDocumentsCard`, `ApprovedDocumentsCard`), so `React.memo` is possible, but the hooks return new objects each render. Measure before adding it.
- **Splitting long pages and long functions** (`DocumentDetailsPage`, `UploadPage`, `App.tsx`; `image_manipulation.py` and its long checks, `check_layout_anomalies`, the cross-document tool builders), splitting `case_match.py` into a package, and renaming `app/langgraph` (it shares the library's name but works through relative imports): decided against for now.
- **Keeping per-page OCR text for the spelling check:** detection runs long after upload, so an in-memory copy would mostly miss.

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The Agents and Cost dashboards now give the data (cost and calls per agent and per unit of work). It changes detection quality, so test against the regression set first.

---

## Suggested order

1. **The generator checks in CI** (repo 1), S.
2. **Lock the Python dependencies** (repo 2). It protects every deploy, and keeps the OpenAPI export stable between machines.
3. **Rules out of `routers/documents.py`** (backend 2.1), the biggest structural win left.
4. **Public field-value helpers** (backend 2.2).
5. **The Audit Trail card and the mock dashboard** (frontend 2.1) before any demo.
6. **The one-off colours** (frontend 1.1), as you restyle each component.
7. **Decide `submission_finalized`** (backend 2.4) as part of automatic case analysis.
