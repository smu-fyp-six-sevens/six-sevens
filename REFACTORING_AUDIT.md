# Refactoring audit

Code smells, cleanup and optimisations across the repo: `backend/app`, `frontend/src`, and the deployment (`deploy/`, `.github/workflows`). Tests are covered in `backend_tests.md`.

Effort: S (minutes), M (an hour or two), L (a day or more). Reviewed 2026-10-22, by reading the code and running static measurements; updated 2026-11-01 against `main`. Nothing was profiled against production data. Only open items are listed: remove one once it's done.

## Where we are

| | |
|---|---|
| Backend | 50,500 lines in 217 files under `app/` (4,400 of them in `injection/`); 64 route handlers |
| Frontend | 15,900 lines of TS/TSX in 118 files (not counting `api.gen.ts`); 8,500 lines of CSS in 19 files |
| Deployment | Terraform 1,000 lines in 11 files, 2 workflows, 8 compose files, Kong and Grafana images |
| Tests | 1,996 backend test functions in 105 files; 183 Playwright test definitions in 12 specs |
| Backend gates | ruff and pyright clean on `app/`, 0 `type: ignore`, 15 `noqa` |
| Frontend gates | `tsc` (app and e2e) and eslint clean |

---

## 1. Duplicated code

### Frontend

1. **The risk scale is written per hue, not by meaning.** Every colour is now a token (`styles/tokens.css`), but "medium risk" is still `--orange-92`, `--orange-27` and the like in each file. Semantic tokens (`--risk-medium-bg`, `--risk-medium-text`, ...) would let the whole scale change in one place. Do it when the risk cards are restyled. S

### Deployment

1. **Four Cloud Run definitions repeat the same blocks** (scaling, resources, `lifecycle`, the collector sidecar): `run.tf` (roles, Kong, frontend) and `observability.tf` (Grafana). Kong's collector duplicates the roles' dynamic block. A local module would shrink this, but it isn't worth it before Stage 2 replaces Cloud Run with Helm charts. M, low priority

---

## 2. Wrong place, or doesn't make sense

### Backend

1. **Private helpers shared across packages.** `case_pipeline/*` (6 modules), `dates/date_anomalies.py`, `giro.py`, the `reference_checks` and two `registry_lookups` modules import `_as_number`, `_rows`, `_parse_field_date` (and `_money`, `_CURRENCY_WORDS`, `_fields_by_name`, …) from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`, as `decode_field_value` already is. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
2. **`case_service.py` (674 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), and the snapshot builder with its caches. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
3. **`models.py` (1,006 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
4. **Blocking calls in routers without the 502 wrapper.** A Supabase error at these becomes a bare 500 with no log line, instead of `to_thread_or_502` / `bad_gateway_on_error`'s logged 502: `cases.py` (the summary re-read in `update_case`); `detection.py` (`langgraph_trace.resume`, and `list_documents` in the company-name check); `upload.py` (the case lookup in single and bulk upload). S

### Frontend (types)

1. **Hand-written API types:** `DetectionReport`, `LangGraphTrace`. Their routes return `dict`, so nothing is generated. A response model for the detection report is the larger job. M

### Deployment and repo

1. **The schema is a hand-kept snapshot** (`backend/sql/schema.sql`), not versioned migrations. Staging (`CLOUD_ARCHITECTURE_PLAN.md` section 9) would want migrations it can replay. M

---

## 3. Optimisations

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|

| 2 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume. If it grows, push cancels to the run over Realtime. The existing subscription (`realtime_listener.py`) can't simply be reused: it runs on the api role only and pushes case snapshots, while the polls run on doc-worker and detection-worker. Each worker instance would open its own channel (a Realtime connection each), filtered to cancelled rows, and keep a slow poll as the fallback, since Realtime delivery isn't guaranteed | S, later |



---

## 4. Deletes, the audit trail and the schema

The delete rules, RLS policies and grants are all in `backend/sql/schema.sql` (every table keyed by a document or a case cascades from it).

**What a delete removes:**
- A document: by cascade, `extracted_fields`, `findings`, `langgraph_trace`, `detection_results` and its `case_notes`; the code removes its stored file (best effort) and the cross-document findings resting on it (`document_repository._delete_document_children`).
- A case: its documents as above, then the row, which cascades to `risk_scores`, `case_analysis_runs`, `findings` and `case_notes` (`case_repository.delete_case`).
- `audit_events` is never deleted: it has no foreign keys, so events outlive what they name. Intended.

| # | Finding | Fix | Effort |
|---|---|---|---|
| 2 | **Delete audit events are fire-and-forget** (`events.record`): if publishing fails, the delete stands with no audit row. A case delete logs one `case.deleted` with a count, not a `document.deleted` per document | `publish` for deletes (fail the request), or accept; per-document events on case delete | S |

| 4 | **A delete's steps outside the database aren't in its transaction:** the cross-document findings citing a document go first, then the stored files, then the row (whose cascades are atomic). A failure partway leaves the row (a retry finishes the job); a stored file that fails to delete is only logged, so it stays in storage | Accept | - |

**Unused schema** (read 2026-10-30):
- Written, never read: `risk_scores.scored_at`, `extracted_fields.field_category` and `extracted_at`.

---

## 5. Backwards compatibility (technical debt)

The database was wiped on 2026-10-31 and the fallbacks for older rows were removed. What remains tolerates a missing value for reasons that also apply to new rows, so it stays:
- `document_repository.get_guard_result` and `tools._guard_result` (the guard run again at detection), and the content-guard route's `checked=false`: ingest doesn't fail when saving `guard_result` fails.
- `holder.py`, `models.is_personal_bank_statement`, `registry_lookups/addresses.py`: a statement whose extraction leaves out `entity_type` falls back to the names.
- `detection_report.read_report`: defaults for keys a report lacks (a run that stopped partway, or a later schema change).
- `services/uploadService/documentStatus.ts`: `approved` is read as about to scan, skipped (no type) or failed, all live states.

**Rule from here on:** a new stored field gets a default in the model that the old rows read correctly, and an entry here naming the fallback, so it can be removed once the old rows are gone.

---

## 6. Worth a look (not read yet)

Found by the size and coupling in a code graph (2026-11-05), not by reading the code. Each needs a real review before it counts as a problem.

- **`case_report.py`:** 1,298 lines and 88 symbols, and it depends on 17 other files. It's about twice the size of `case_service.py`, so it's the best candidate for a real review.
- **`DocumentDetailsPage.tsx` and `UploadPage.tsx`:** 712 and 510 lines, and each depends on 36–37 files.
- **`routers/detection.py`:** it depends on 33 files, a lot for a router. `documents.py` was split into a service for the same reason.
- **`_finding`:** defined in 7 files. The copies haven't been compared.

---

## Suggested order

1. ~~**Revoke TRUNCATE on the audit trail** (§4.3), one line of SQL.~~
2. **Quick win:** the 502 wrappers (backend 2.4), S.
3. **Public field-value helpers** (backend 2.1).
4. **Semantic risk tokens** (frontend 1.1), when the risk cards are restyled.
