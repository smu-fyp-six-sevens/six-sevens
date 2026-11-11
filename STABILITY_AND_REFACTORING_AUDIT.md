# Stability and refactoring audit

What is fragile, what will fail under load or a retry, and what the many merges left behind: code smells, cleanup, optimisations and technical debt across `backend/app`, `frontend/src` and the deployment (`deploy/`, `.github/workflows`). Tests are in `backend_tests.md`; product work is in `PENDING_WORK.md`.

Severity (stability findings): Medium or Low. Effort: S (minutes), M (an hour or two), L (a day or more). Read from the code and static checks (ruff, pyright, tsc, eslint) on `main`; reviewed 2026-10-22 and 2026-10-29, updated 2026-11-11. Nothing was profiled against production data. Only open items are listed: delete one once it's done.

## Where we are

| | |
|---|---|
| Backend | 54,500 lines in 242 files under `app/` (4,400 of them in `injection/`); 69 route handlers |
| Frontend | 22,100 lines of TS/TSX in 160 files (not counting `api.gen.ts`); 11,900 lines of CSS in 22 files |
| Deployment | Terraform 1,000 lines in 11 files, 2 workflows, 8 compose files, Kong and Grafana images |
| Tests | see `backend_tests.md` section 1 |
| Backend gates | ruff and pyright clean on `app/`, 0 `type: ignore`, 15 `noqa` |
| Frontend gates | `tsc` (app and e2e) and eslint clean |

The database was wiped on 2026-10-31. Once real use has built up rows, profile it again (CLAUDE.md, "Profiling").

---

## 1. Stability and security

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| 1 | Medium | **A document the content guard blocks can never be processed.** It's marked `failed`; Retry blocks it again (the scan is deterministic); there's no officer override. One false positive on a real document leaves it permanently failed | `langgraph/nodes.py` (the guard step) | An officer action "process anyway", recorded in the audit trail |
| 2 | Low | **The leak check keeps the prompts of cancelled calls.** It remembers each prompt it sends so it can spot a reply that repeats it, and forgets it in `on_llm_end` or `on_llm_error`. A cancelled call fires neither, so its text stays in the dict: a slow leak that grows with every cancel. The word matching also runs on the event loop | `guardrails/leak_check.py` | Cap the dict (drop entries older than the LLM timeout) |
| 3 | Low | **A repeated `detection.retry_requested` can rerun a finished detection.** A retry may start from `detected`, so a retry event redelivered after the first run finished starts another run. Approvals and recovery can't repeat | `workers/detection.py` `on_requested` | Store the event id with the run. Not worth it until the dead-letter subscription shows it happens |
| 4 | Low | **Two Supabase clients on one key.** `supabase_client` and `service_supabase_client()` are now the same credential, so the second is only a second connection pool and a second guard in `conftest.py` | `supabase_client.py` | Keep one client and one name. S |

## 2. Database load

Measured on 2026-11-07 from Supabase's API logs (`edge_logs`, read-only, ids and timings only): the 24 hours to 08:30 UTC, over 8 cases and 80 documents. Queries run 150–700 ms each, so the number of queries decides the time. The previous read (2026-11-01) found one case analysis at 110 queries and each document at about 11 updates and 17 reads of its row.

| | 24 hours to 7 Nov 08:30 UTC |
|---|---|
| REST requests | 2,343, taking 745 s of database time |
| An idle hour | about 24 requests, all of them the sweeper |
| Per document | 7.8 updates on average (max 11; was about 11) and 20 reads of its row (max 92). The reads now include officers opening the page, so they are not comparable with the 17 before |
| Queries per case analysis | not measured: too few runs in the window |

- **The sweeper is the biggest load, and it is steady.** Its scan of in-flight documents (`status in (uploaded, queued, processing, extracting, detecting)`, ordered by id, up to 1,000 rows, 18 columns) ran 317 times at 723 ms on average, and its scan of running analyses ran 280 times at 276 ms. Together that is 597 requests, 25% of all requests, and 306 s, 41% of the database time. The document scan is the slowest query in the log even though it returns few rows, which points at a full table scan; check with `EXPLAIN`, then add a partial index on `documents (status)` for the in-flight statuses and read only the columns the sweeper uses (id, status and the timestamps). Sweeping less often is the other lever. S
- **Per case:** a case is read 283 times (`cases?id=eq.`), its latest analysis run 138 times and its reviewed findings 96 times in the window. A request already reuses the case within itself (`case_repository.request_scope`); the repeats are across requests, from the live snapshot builds. Look at these again once a few cases have been worked in a day.
- **Case search** matches `*q*` on the name and number, which no ordinary index can serve, so every search scans every case (`case_repository._matching`). Add a trigram index (`pg_trgm`) once cases number in the thousands; not needed before.
- **The precision and recall figures** still read every review when no filter is set (`findings_repository.list_reviews`): fine while reviews number in the thousands. Past that, aggregate in the database.

## 3. Duplicated code

**Frontend**
1. **The risk scale is only partly written by meaning.** Semantic tokens exist (`--risk-medium-tint`, `--risk-medium-text`, ... in `styles/tokens.css`), but only 4 files use them; the rest still style risk with hue tokens such as `--orange-92` and `--orange-27`, which also serve non-risk uses. Move the risk styling over when the risk cards are restyled. S

**Deployment**
2. **Four Cloud Run definitions repeat the same blocks** (scaling, resources, `lifecycle`, the collector sidecar): `run.tf` (roles, Kong, frontend) and `observability.tf` (Grafana). Kong's collector duplicates the roles' dynamic block. A local module would shrink this, but it isn't worth it before Stage 2 replaces Cloud Run with Helm charts. M, low priority

## 4. Wrong place, or doesn't make sense

**Backend**
1. **Private helpers shared across packages.** `case_pipeline/*` (6 modules), `dates/date_anomalies.py`, `giro.py`, the `reference_checks` and two `registry_lookups` modules import `_as_number`, `_rows`, `_parse_field_date` (and `_money`, `_CURRENCY_WORDS`, `_fields_by_name`, …) from `fraud_detection/numerical_validation/common.py`. Those are a public field-value API in practice: make them public in `fraud_detection/shared/field_values.py`, as `decode_field_value` already is. Similarly, `field_checks/*` and `tool_timeout.py` import `_result` and `_with_ids` from `detection/tools.py`. M
2. **`case_service.py` (674 lines) mixes jobs:** the documents-status rollup, pass-throughs (`get_case`, `create_case`, `update_case`), and the snapshot builder with its caches. The required-documents rules are split across `models.py`, `case_service.py` and `mapper.py`. M
3. **`models.py` (1,084 lines)** mixes enums and business rules, API schemas, and risk models that have a twin in `case_pipeline/models.py`. M
4. **Blocking calls in routers without the 502 wrapper.** A Supabase error at these becomes a bare 500 with no log line, instead of `to_thread_or_502` / `bad_gateway_on_error`'s logged 502: `cases.py` (the summary re-read in `update_case`); `detection.py` (`langgraph_trace.resume`, and `list_documents` in the company-name check); `upload.py` (the case lookup in single and bulk upload). S
5. **`metadata_tool` blocks the event loop:** it calls the synchronous `check_metadata_anomalies` inside an `async` function (the other tools use `asyncio.to_thread`) (`langgraph/detection/tools.py`). S

**Frontend**
6. **Hand-written API types:** `DetectionReport`, `LangGraphTrace`. Their routes return `dict`, so nothing is generated. A response model for the detection report is the larger job. M

**Deployment and repo**
7. **The schema is a hand-kept snapshot** (`backend/sql/schema.sql`), not versioned migrations. Staging (`CLOUD_ARCHITECTURE_PLAN.md` section 9) would want migrations it can replay. M

## 5. Optimisations

| # | Where | What happens now | Fix | Effort |
|---|---|---|---|---|
| 1 | Cancel polling (`workers/recovery.cancel_when`) | One Supabase read every 5 s per running pipeline or detection in Pub/Sub mode (at most about 12 a minute per run) | Fine at today's volume. If it grows, push cancels to the run over Realtime. The existing subscription (`realtime_listener.py`) can't simply be reused: it runs on the api role only and pushes case snapshots, while the polls run on doc-worker and detection-worker. Each worker instance would open its own channel (a Realtime connection each), filtered to cancelled rows, and keep a slow poll as the fallback, since Realtime delivery isn't guaranteed | S, later |

## 6. Deletes and the schema

The delete rules, RLS policies and grants are all in `backend/sql/schema.sql` (every table keyed by a document or a case cascades from it).

**What a delete removes:**
- A document: by cascade, `extracted_fields`, `findings`, `langgraph_trace`, `detection_results` and its `case_notes`; the code removes its stored file (best effort) and the cross-document findings resting on it (`document_repository._delete_document_children`).
- A case: its documents as above, then the row, which cascades to `risk_scores`, `case_analysis_runs`, `findings` and `case_notes` (`case_repository.delete_case`). Each document and the case itself get an audit event.
- `audit_events` is never deleted: it has no foreign keys, so events outlive what they name. Intended.

**Accepted limits:**
- Delete audit events are fire-and-forget (`events.record`): if publishing fails, the delete stands with no audit row.
- A delete's steps outside the database aren't in its transaction: the cross-document findings citing a document go first, then the stored files, then the row (whose cascades are atomic). A failure partway leaves the row (a retry finishes the job); a stored file that fails to delete is logged (`storage_service` warns when storage removes fewer files than asked), so it stays in storage.

**Unused schema:** written, never read: `risk_scores.scored_at`, `extracted_fields.field_category` and `extracted_at` (read 2026-10-30).

## 7. Backwards compatibility (technical debt)

The database was wiped on 2026-10-31 and the fallbacks for older rows were removed. What remains tolerates a missing value for reasons that also apply to new rows, so it stays:
- `document_repository.get_guard_result` and `tools._guard_result` (the guard run again at detection), and the content-guard route's `checked=false`: ingest doesn't fail when saving `guard_result` fails.
- `holder.py`, `models.is_personal_bank_statement`, `registry_lookups/addresses.py`: a statement whose extraction leaves out `entity_type` falls back to the names.
- `detection_report.read_report`: defaults for keys a report lacks (a run that stopped partway, or a later schema change).
- `services/uploadService/documentStatus.ts`: `approved` is read as about to scan, skipped (no type) or failed, all live states.

**To decide** (likely dead since the wipe): `frontend/src/pages/document-details/fieldDisplay.ts` ("Fallback for older/simple extracted-field data") accepts a bare value where the API now always sends `{id, value, confidence}`. Confirm against `GET /documents/{id}`, then delete the branch.

**Rule from here on:** a new stored field gets a default in the model that the old rows read correctly, and an entry here naming the fallback, so it can be removed once the old rows are gone.

## 8. Worth a look (not read yet)

Found by the size and coupling in a code graph (2026-11-05), not by reading the code. Each needs a real review before it counts as a problem.

- **`DocumentDetailsPage.tsx` and `UploadPage.tsx`:** 716 and 530 lines, and each depends on 36–37 files.
- **`routers/detection.py`:** it depends on 33 files, a lot for a router. `documents.py` was split into a service for the same reason.
- **`_finding`:** defined in 7 files. The copies haven't been compared.

## Suggested order

1. **Quick wins:** the 502 wrappers (4.4) and `metadata_tool` off the event loop (4.5), both S.
2. **Merge the two Supabase clients** (1.4).
3. **Public field-value helpers** (4.1).
4. **Decide the compatibility leftover** (7).
5. **Content-guard override** (1.1), when the officer-facing work is next.
6. **Semantic risk tokens** (3.1), when the risk cards are restyled.
