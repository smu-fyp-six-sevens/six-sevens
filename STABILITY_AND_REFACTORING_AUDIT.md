# Stability and refactoring audit

Fragile code, load and technical debt. Open items only: delete one once done. Updated 2026-11-13. Bugs found later are in `CODEBASE_AUDIT.md`; tests in `backend_tests.md`; product work in `PENDING_WORK.md`.

## 1. Stability

| # | Sev | In plain words | Fix | Where | Size |
|---|---|---|---|---|---|
| 1.1 | Medium | A document the content guard wrongly blocks can never be processed: retry blocks it again and there's no override | An officer "process anyway" action, logged in the audit trail | `langgraph/nodes.py` (guard step) | M |
| 1.2 | Low | The leak check never forgets prompts of cancelled calls: a slow memory leak | Drop entries older than the LLM timeout | `guardrails/leak_check.py` | S |
| 1.4 | Low | Two Supabase clients on the same key: an extra connection pool for nothing | Keep one client | `supabase_client.py` | S |

(A repeated detection retry is CODEBASE_AUDIT 2.11.)

## 2. Database load

Measured 2026-11-07 (24 hours, 8 cases, 80 documents): 2,343 requests, 745 s of database time; an idle hour is ~24 requests, all the sweeper.

| In plain words | Fix | Size |
|---|---|---|
| The sweeper is the biggest load: 25% of requests and 41% of database time. Its document scan is the slowest query | Check with `EXPLAIN`; a partial index on the in-flight statuses; read only the columns it needs; or sweep less often | S |
| A case is re-read hundreds of times a day by live snapshot builds | Recheck once a few cases are worked in a day | — |
| Case search scans every case | A trigram index once there are thousands of cases | S |
| Precision/recall figures read every review | Aggregate in SQL once reviews pass the thousands | M |

## 3. Code that's in the wrong place or repeated

| # | In plain words | Fix | Size |
|---|---|---|---|
| 3.1 | Risk colours mostly use hue tokens, not the risk tokens | Move them when the risk cards are restyled | S |
| 3.2 | Four Cloud Run definitions repeat the same blocks | A local module, only if Cloud Run stays long-term | M |
| 4.1 | "Private" helpers (`_as_number`, `_rows`, `_parse_field_date`, …) are imported all over | Make them public in `fraud_detection/shared/field_values.py`; same for `_result`, `_with_ids` in `detection/tools.py` | M |
| 4.2 | `case_service.py` (674 lines) does three jobs; required-document rules are split over three files | Split | M |
| 4.3 | `models.py` (1,084 lines) mixes enums, rules, API schemas and risk models | Split | M |
| 4.4 | Some router calls turn a Supabase error into a bare 500 with no log | Use `to_thread_or_502` in `cases.py` (`update_case` re-read), `detection.py` (`langgraph_trace.resume`, `list_documents`), `upload.py` (case lookup) | S |
| 4.6 | `DetectionReport` and `LangGraphTrace` types are hand-written in the frontend | A response model for the report | M |
| 4.7 | The schema is a hand-kept snapshot, not migrations | Migrations, when staging needs them | M |
| 5.1 | Cancel checks poll the database every 5 s per run | Fine for now; later, push cancels over Realtime | S |

## 4. Not read yet (flagged by size only)

`DocumentDetailsPage.tsx` (716 lines) and `UploadPage.tsx` (530); `routers/detection.py` (33 imports); `_finding` defined in 7 files.

## 5. Deletes and leftovers

- Deletes rely on cascades (`backend/sql/schema.sql`); `audit_events` is never deleted. Known limits: a delete's audit event is best effort, and a failed file delete leaves the file in storage (logged).
- Unused columns: `risk_scores.scored_at`, `extracted_fields.field_category`, `extracted_fields.extracted_at`.
- Probably dead since the 2026-10-31 wipe: the "older extracted-field data" branch in `frontend/src/pages/document-details/fieldDisplay.ts:40`. Confirm against `GET /documents/{id}`, then delete.
- Rule: a new stored field gets a model default that old rows read correctly.

## Order

4.4 (quick) and FRAUD_DETECTION_MAP §6 bug 4, 1.4, 4.1, the `fieldDisplay` leftover, 1.1 when officer work is next, 3.1 with the risk-card restyle.
