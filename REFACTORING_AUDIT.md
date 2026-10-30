# Refactoring audit

Code smells, cleanup and optimisations across the repo: `backend/app`, `frontend/src`, and the deployment (`deploy/`, `.github/workflows`). Tests are covered in `backend_tests.md`.

Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-22, by reading the code and running static measurements; updated 2026-10-26 against `main` (`4542eac`). Nothing was profiled against production data. Only open items are listed: remove one once it's done.

## Where we are

| | |
|---|---|
| Backend | 43,500 lines in 191 files under `app/` (4,300 of them in `injection/`); 58 route handlers |
| Frontend | 10,300 lines of TS/TSX in 75 files; 5,800 lines of CSS in 15 files |
| Deployment | Terraform 1,000 lines in 12 files, 2 workflows, 7 compose files, Kong and Grafana images |
| Tests | 1,783 backend test functions in 93 files; 154 Playwright test definitions in 9 specs |
| Backend gates | ruff clean on `app/` (16 old issues in `doc_gen_scripts/`, `scripts/` and one test, outside CI's scope), pyright clean, 0 `type: ignore`, 14 `noqa` (all re-export `F401`) |
| Frontend gates | lint 0 errors, 0 warnings; CI runs `npm run typecheck` (not re-run for this update) |

---

## 1. Duplicated code

### Frontend

1. **60 one-off hex colours** are still written inline in the stylesheets (each appears once, so they were not merged into a token). Every repeated colour is a token in `styles/tokens.css`. When you restyle a component, move its one-offs into the palette (or onto the nearest existing token). S, as you go

   | Group | Count | What they are |
   |---|---|---|
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

1. **Private helpers shared across packages.** `case_pipeline/*` (6 modules), `dates/date_anomalies.py`, `giro.py`, the `reference_checks` and two `registry_lookups` modules import `_as_number`, `_rows`, `_parse_field_date` (and `_money`, `_CURRENCY_WORDS`, `_fields_by_name`, …) from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`, as `decode_field_value` already is. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
2. **`case_service.py` (516 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), the snapshot builder, and `submission_finalized`. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
3. **`submission_finalized` machinery.** A debounce with timer threads (`document_repository`), a flush before each request returns, and a per-process "last written" cache (`case_service._synced_finalized`), all for a flag nothing reads yet. Decide its shape when automatic case analysis is built: keep it stored, or compute it inside the gate and delete the machinery. Decision pending this sprint
4. **`models.py` (665 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
5. **Blocking calls in routers without the 502 wrapper.** A Supabase error at these becomes a bare 500 with no log line, instead of `to_thread_or_502` / `bad_gateway_on_error`'s logged 502: `cases.py` (the summary re-read in `update_case`); `detection.py` (`langgraph_trace.load` in the check gate, the report and retry; `list_documents` in the company-name check; `langgraph_trace.resume`); `upload.py` (the case lookup in single and bulk upload). S

### Frontend (types)

1. **Hand-written API types:** `DetectionReport`, `LangGraphTrace`. Their routes return `dict`, so nothing is generated. A response model for the detection report is the larger job. M

### Deployment and repo

1. **The database schema isn't in the repo.** Everything else is code; the schema lives only in Supabase. Export it as versioned migrations (the first step of staging, `CLOUD_ARCHITECTURE_PLAN.md` section 9). M (reading the schema needs your go-ahead)

---

## 3. Optimisations

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|
| 1 | Spelling dictionary warm-up (`main.py`) | Built in the background on detection-worker only; the api's first on-demand spelling check waits about 6 s | Warm it on api too (costs its memory), or accept it | S |
| 2 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume. If it grows, push cancels to the run over Realtime. The existing subscription (`realtime_listener.py`) can't simply be reused: it runs on the api role only and pushes case snapshots, while the polls run on doc-worker and detection-worker. Each worker instance would open its own channel (a Realtime connection each), filtered to cancelled rows, and keep a slow poll as the fallback, since Realtime delivery isn't guaranteed | S, later |

**Needs measuring first:** whether rule-confident classifications could skip the Sonnet call, or the detection lead could use Haiku. The Agents and Cost dashboards now give the data (cost and calls per agent and per unit of work). It changes detection quality, so test against the regression set first.

---

## 4. Deletes, the audit trail and the schema

Checked 2026-10-27 by reading the code and PostgREST's table listing (no rows read). The listing doesn't show grants, RLS or foreign-key delete rules; the two queries below do (Supabase SQL editor).

**What a delete removes:**
- A document: `extracted_fields`, `flags`, `langgraph_trace`, its `case_notes` (replies carry the note's `document_id`), `finding_reviews`, the stored file (best effort), then the row (`document_repository._delete_document_children`).
- A case: its documents as above, then `risk_scores`, `case_analysis_runs`, `flags`, `case_notes`, `finding_reviews`, then the row (`case_repository.delete_case`).
- `audit_events` is never deleted: it has no foreign keys, so events outlive what they name. Intended.

| # | Finding | Fix | Effort |
|---|---|---|---|
| 1 | **`case_status_history` blocks a case delete** if it has rows: it has a foreign key to `cases` and nothing clears it (no code on any branch uses the table; it belongs to other developers) | Check the delete rule; clear it in `delete_case`, or cascade | S |
| 2 | **Delete audit events are fire-and-forget** (`events.record`): if publishing fails, the delete stands with no audit row. A case delete logs one `case.deleted` with a count, not a `document.deleted` per document | `publish` for deletes (fail the request), or accept; per-document events on case delete | S |
| 3 | **`audit_events` can't be edited or removed only if grants say so.** `audit.py` says the service role has INSERT and SELECT only; Supabase's default gives `service_role` everything, so this holds only if someone revoked it by hand. The dashboard owner can always delete. No retention policy | Run the grants query; revoke UPDATE and DELETE if missing | S |
| 4 | **Deletes aren't a transaction:** a failure partway leaves the rest (a retry finishes the job). A stored file that fails to delete is only logged, so it stays in storage | Accept; or a Postgres function per delete once the schema is in the repo | M, later |

```sql
select grantee, privilege_type from information_schema.role_table_grants where table_name = 'audit_events';
select conrelid::regclass as tbl, conname, confdeltype from pg_constraint where contype = 'f';  -- c = cascade, a/r = blocks
```

**Unused schema on `main`** (rows read on 2026-10-30; STABILITY_AUDIT §2 has the rest):
- Not used at all: `case_status_history` (no code), `flags.reviewed_by` and `reviewed_at` (never set; `status` is only filtered on in the delete), `flags.related_document_ids` (null on every row).
- Written by an unmerged branch, unknown to `main`: every `cases.status` value except `open` (STABILITY_AUDIT D1).
- Written, never read: the whole `flags` table (risk and the UI read the detection report), `risk_scores.flag_ids` and `scored_at`, `extracted_fields.field_category` and `extracted_at`.

---

## Suggested order

1. **The two SQL queries** (§4.1, 4.3): whether a case delete can be blocked, and whether the audit trail can be edited.
2. **Quick win:** the 502 wrappers (backend 2.5), S.
3. **Public field-value helpers** (backend 2.1).
4. **The one-off colours** (frontend 1.1), as you restyle each component.
5. **Decide `submission_finalized`** (backend 2.3) as part of automatic case analysis.
