# Refactoring audit

Code smells, cleanup and optimisations across the repo: `backend/app`, `frontend/src`, and the deployment (`deploy/`, `.github/workflows`). Tests are covered in `backend_tests.md`.

Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-22, by reading the code and running static measurements; updated 2026-10-26 against `main` (`4542eac`). Nothing was profiled against production data. Only open items are listed: remove one once it's done.

## Where we are

| | |
|---|---|
| Backend | 43,500 lines in 191 files under `app/` (4,300 of them in `injection/`); 58 route handlers |
| Frontend | 10,300 lines of TS/TSX in 75 files; 5,800 lines of CSS in 15 files |
| Deployment | Terraform 1,000 lines in 12 files, 2 workflows, 7 compose files, Kong and Grafana images |
| Tests | 1,733 backend test functions in 89 files; 154 Playwright tests in 7 specs |
| Backend gates | ruff clean on `app/` (16 old issues in `doc_gen_scripts/`, `scripts/` and one test, outside CI's scope), pyright clean, 0 `type: ignore`, 14 `noqa` (all re-export `F401`) |
| Frontend gates | lint 0 errors, 0 warnings; CI runs `npm run typecheck` (not re-run for this update) |

---

## 1. Duplicated code

### Backend

1. **Two copies of `decode_field_value`.** `font_consistency._field_search_text` and `shared/field_catalogue.array_rows` each `json.loads` a field value themselves. Call `shared/field_values.decode_field_value` instead. S

### Frontend

1. **Repeated `rgba()` shadows and tints aren't tokens.** The token pass covered hex only: 14 `rgba()` values are used more than once (about 45 uses), mostly slate `rgba(61, 90, 128, …)` and navy `rgba(18, 32, 61, …)` at a few alphas, across `base`, `case-queue`, `upload`, `login`, `create-case`, `document-details` and `documents-table`. Add shadow and overlay tokens to `tokens.css` (`--shadow-sm`, `--slate-tint-08`, …). S
2. **105 one-off hex colours** are still written inline in the stylesheets (each appears once, so they were not merged into a token). Every repeated colour is a token in `styles/tokens.css`. When you restyle a component, move its one-offs into the palette (or onto the nearest existing token). S, as you go

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
2. **Private helpers shared across packages.** `case_pipeline/*` (6 modules), `dates/date_anomalies.py`, `giro.py`, the `reference_checks` and two `registry_lookups` modules import `_as_number`, `_rows`, `_parse_field_date` (and `_money`, `_CURRENCY_WORDS`, `_fields_by_name`, …) from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`, as `decode_field_value` already is. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
3. **`case_service.py` (516 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), the snapshot builder, and `submission_finalized`. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
4. **`submission_finalized` machinery.** A debounce with timer threads (`document_repository`), a flush before each request returns, and a per-process "last written" cache (`case_service._synced_finalized`), all for a flag nothing reads yet. Decide its shape when automatic case analysis is built: keep it stored, or compute it inside the gate and delete the machinery. Decision pending this sprint
5. **`models.py` (665 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
6. **Blocking calls in routers without the 502 wrapper.** A Supabase error at these becomes a bare 500 with no log line, instead of `to_thread_or_502` / `bad_gateway_on_error`'s logged 502: `cases.py` (the summary re-read in `update_case`); `documents.py` (the target-case read in move and bulk move, the `update_status` and re-read in `cancel_document`, the re-read after retry); `detection.py` (`langgraph_trace.load` in the check gate, the report and retry; `list_documents` in the company-name check; `get_case_match_for_document`; `langgraph_trace.resume`); `upload.py` (the case lookup in single and bulk upload). S

### Frontend (types)

1. **Hand-written API types.**
   - `CaseCost`, `CaseCostRun`, `CaseCostTiming` (`types.ts`): `GET /audit/cases/{id}/cost` returns a plain `dict`, so nothing is generated, and `timings` drifted to optional. Give the route a Pydantic response model, regenerate, and alias it. S
   - `CaseSort`, `CaseRiskFilter`: copies of the backend's `Literal`s. Read them from `api.gen.ts`'s `operations` (the `list_cases` query parameters). S
   - `DetectionReport`, `LangGraphTrace`: their routes return `dict` too. A response model for the detection report is the larger job. M

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
| 1 | `GET /documents/{id}/detection/case-match` (runs after each field edit) | 6 queries in sequence: the route's document read, then `get_case_match_for_document` reads the same document again with its fields, then the case, then the case's documents and their fields | Pass the route's document in instead of re-reading it, and read the case and its documents side by side (`case_service._submit`): 4 queries in 2 rounds | S |
| 2 | `GET /cases/{id}/analysis` (each card reload) | 5 queries in 4 rounds: the case (dependency), the run, the documents and fields, the traces. The fingerprint hashes the fields, so none can be dropped | Read the run and the documents side by side: one round fewer | S |
| 3 | Spelling dictionary warm-up (`main.py`) | Built in the background on detection-worker only; the api's first on-demand spelling check waits about 6 s | Warm it on api too (costs its memory), or accept it | S |
| 4 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume; if it grows, push cancels to the run instead (a Realtime subscription on its document row) | S, later |

**Considered and left alone:**
- **The snapshot payload** (every document on each push): pushes are debounced and built only for open cases. Measure a real case before designing deltas.
- **The document page subscribes to the whole case snapshot**, but only while the document is in flight; a per-document stream would add backend work for almost no gain.
- **`useCaseSnapshot`'s 12 state setters** are batched by React into one render. Cleaner as one object, but not faster.
- **Row memoization on the case page:** the rows are now their own components (`ExtractedDocumentsCard`, `ApprovedDocumentsCard`), so `React.memo` is possible, but the hooks return new objects each render. Measure before adding it.
- **Splitting long pages and long functions** (`DocumentDetailsPage`, `UploadPage`, `App.tsx`; `image_manipulation.py` and its long checks, `check_layout_anomalies`, the cross-document tool builders), splitting `case_match.py` into a package, and renaming `app/langgraph` (it shares the library's name but works through relative imports): decided against for now.
- **Keeping per-page OCR text for the spelling check:** detection runs long after upload, so an in-memory copy would mostly miss.

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The Agents and Cost dashboards now give the data (cost and calls per agent and per unit of work). It changes detection quality, so test against the regression set first.

---

## 4. Deletes, the audit trail and the schema

Checked 2026-10-27 by reading the code and PostgREST's table listing (no rows read). The listing doesn't show grants, RLS or foreign-key delete rules; the two queries below do (Supabase SQL editor).

**What a delete removes:**
- A document: `extracted_fields`, `flags`, `langgraph_trace`, its `case_notes` (replies carry the note's `document_id`), the stored file (best effort), then the row (`document_repository._delete_document_children`).
- A case: its documents as above, then `risk_scores`, `case_analysis_runs`, `flags`, `case_notes`, then the row (`case_repository.delete_case`).
- `audit_events` is never deleted: it has no foreign keys, so events outlive what they name. Intended.

| # | Finding | Fix | Effort |
|---|---|---|---|
| 1 | **Flag inserts may be failing.** The listing marks `flags.related_document_ids` NOT NULL with no default, and `flags_repository.build_rows` never sets it. The failure would be invisible: `graph._store_flags` catches it into the trace as `flags_error`, and nothing reads `flags`. Unconfirmed (needs a look at real data) | Write `"related_document_ids": []`, or give the column a default | S |
| 2 | **`case_status_history` blocks a case delete** if it has rows: it has a foreign key to `cases` and nothing clears it (no code on any branch uses the table; it belongs to other developers). `finding_reviews` (branch `SCRUM-284-precision-recall`) declares `on delete cascade` and its branch adds it to both delete paths | Check the delete rule; clear it in `delete_case`, or cascade | S |
| 3 | **Delete audit events are fire-and-forget** (`events.record`): if publishing fails, the delete stands with no audit row. A case delete logs one `case.deleted` with a count, not a `document.deleted` per document | `publish` for deletes (fail the request), or accept; per-document events on case delete | S |
| 4 | **`audit_events` can't be edited or removed only if grants say so.** `audit.py` says the service role has INSERT and SELECT only; Supabase's default gives `service_role` everything, so this holds only if someone revoked it by hand. The dashboard owner can always delete. No retention policy | Run the grants query; revoke UPDATE and DELETE if missing | S |
| 5 | **Deletes aren't a transaction:** a failure partway leaves the rest (a retry finishes the job). A stored file that fails to delete is only logged, so it stays in storage | Accept; or a Postgres function per delete once the schema is in the repo | M, later |

```sql
select grantee, privilege_type from information_schema.role_table_grants where table_name = 'audit_events';
select conrelid::regclass as tbl, conname, confdeltype from pg_constraint where contype = 'f';  -- c = cascade, a/r = blocks
```

**Unused schema on `main`:**
- Not used at all: `finding_reviews` (branch `SCRUM-284-precision-recall`), `documents.guard_result` (branch `scrum-288-guardrails`), `case_status_history` (no code), `flags.reviewed_by` and `reviewed_at` (`status` is only filtered on in the delete).
- Written, never read: the whole `flags` table (risk and the UI read the detection report), `risk_scores.flag_ids` and `scored_at`, `extracted_fields.field_category` and `extracted_at`.

---

## Suggested order

1. **Confirm the flag insert** (§4.1) and the two SQL queries (§4.2, 4.4): a possibly silent data bug.
2. **Quick wins, one at a time:** the 502 wrappers (backend 2.6), the two queries (optimisations 1 and 2), the `decode_field_value` copies (backend 1.1), the `CaseCost` model and the filter types (frontend types 1). Each S and separately testable.
3. **The generator checks in CI** (repo 1), S.
4. **Lock the Python dependencies** (repo 2). It protects every deploy, and keeps the OpenAPI export stable between machines.
5. **Rules out of `routers/documents.py`** (backend 2.1), the biggest structural win left.
6. **Public field-value helpers** (backend 2.2).
7. **The Audit Trail card and the mock dashboard** (frontend 2.1) before any demo.
8. **The `rgba()` tokens and the one-off colours** (frontend 1.1, 1.2), as you restyle each component.
9. **Decide `submission_finalized`** (backend 2.4) as part of automatic case analysis.
