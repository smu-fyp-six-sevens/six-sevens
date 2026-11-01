# Stability audit (main, 2026-10-29)

A white-box read of `main` at `d6794e4`, looking for what is failing now, what will fail under load or a retry, and what the many merges left behind. The findings that `REFACTORING_AUDIT.md` and `PENDING_WORK.md` already list are not repeated here.

**How it was checked:** static checks (ruff, pyright, tsc, eslint, the OpenAPI and generated-types checks), reading the code paths below, and PyPI's release metadata. No pytest or Playwright runs, no database reads, and no Claude calls.

## 1. Findings, most severe first

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| 1 | Medium | **The detection worker isn't idempotent.** `on_requested` runs detection for any delivery of `document.approved` or `detection.retry_requested` without checking status, and `run_detection` calls `mark_detecting` unconditionally. A Pub/Sub redelivery (a run over the 600 s deadline, a lost acknowledgement, a shutdown mid-request) runs the whole detection again. It flips `detected` back to `detecting`, replaces the report and its findings (with the officers' verdicts on them) and spends the Claude tokens again. The design rule is "idempotent compare-and-swap handlers"; the doc-worker follows it (`queued → processing`), this one doesn't | `workers/detection.py:78`, `detection/graph.py:572` | Start with `update_status(DETECTING, expected_current_statuses=[APPROVED])` and return if it matched nothing |
| 2 | Medium | **The case worker isn't idempotent either.** `run_requested` doesn't claim the run, so a redelivered `case.analysis.requested` runs the full analysis again (the planner and judge on Sonnet, the outlook and summary on Haiku) | `case_pipeline/service.py:107` | Claim the run (`running` and not yet started) before executing |
| 3 | Medium | **Approve is check-then-write.** The route checks for `analyzed`, then `approve_document` writes `approved` without a condition. Two approvals at once (a double-click, or two officers) both pass and both publish `document.approved`, so two detection runs start. Fixing 1 fixes the cost; this fixes the double event | `document_service.approve`, `document_repository.approve_document` | `.eq("status", "analyzed")` on the update; 409 if it matched nothing |
| 6 | Medium | **A document the content guard blocks can never be processed.** It's marked `failed`; Retry blocks it again (the scan is deterministic); there's no officer override. One false positive on a real document leaves it permanently failed | `langgraph/nodes.py:254` | An officer action "process anyway", recorded in the audit trail |
| 7 | Medium | **An empty `AUTH_SECRET_KEY` is accepted.** Tokens are then signed with an empty key, which anyone can reproduce. Deployed services read it from Secret Manager, so this is a missing safeguard, not a live hole | `config.py:55`, `main.py` lifespan | Refuse to start the api role when it's empty outside `EVENT_TRANSPORT=inprocess` |
| 8 | Low | **The dashboard hides load failures.** `useDashboardCases` catches every error, so a failed first load shows zeros as if there were no cases | `frontend/src/pages/useDashboardCases.ts:35` | Keep an error state and show it |
| 9 | Low | **The upload trusts the browser's `Content-Type`.** The server doesn't check the file's first bytes (the frontend does), so any file labelled PDF is stored and parsed. Junk fails at ingest, so this is hardening | `routers/upload.py:45` | Check the first bytes (`%PDF`, PNG and JPEG signatures) server-side |
| 10 | Low | **The leak check keeps prompts of cancelled calls.** `on_llm_end` and `on_llm_error` never fire for a cancelled call, so its instruction text stays in the dict. It grows slowly with every cancel; the word matching also runs on the event loop | `guardrails/leak_check.py:79` | Cap the dict (drop entries older than the LLM timeout) |
| 11 | Low | **Duplicated helpers that have drifted** (from separate branches): `formatDuration` ("2s" in the audit trail, "1.5s" in the stage timings), `compareFindings` (case overview vs risk card, different fallbacks), `displayableFieldEntries` (two signatures) | `AuditTrailCard.tsx`, `processingTimes.ts`, `CaseOverview.tsx`, `CaseRiskCard.tsx`, `fieldDisplay.ts`, `uploadHelpers.ts` | One copy each |
| 12 | Low | **Colour tokens are eroding:** 86 hex literals outside the token files (`case-summary.css` 29, `document-details.css` 19, the rest under 10). Selectors defined in two stylesheets: `.section-label`, `.upload-btn`, `.skeleton-bar--pill`, `.pagination-gap`, so load order decides which wins | `frontend/src/styles/` | Move repeated colours to tokens; one home per selector |
| 13 | Low | **A leftover `GET /message`** ("Hello from FastAPI") on every role | `main.py:208` | Delete |
| 14 | Low | **The frontend's base images are old tags with known vulnerabilities** (the editor's image scan: `node:22-alpine` 9 high, `nginx:1.27-alpine` 16 critical and 61 high). The build stage doesn't ship; the nginx stage does | `frontend/Dockerfile` | Move to a current `nginx` alpine release (and `node`), pinned by version |

## 2. The database (read 2026-10-30)

Read-only: PostgREST's schema listing, row counts, the newest and oldest 200 rows of each table (which columns are empty, how big the JSON is), and orphan counts. Scripts in the session scratchpad; nothing was written.

The database was wiped on 2026-10-31, and its findings (a branch writing case stages `main` didn't know, a dead route scanning every trace, the old flags table's empty columns) are fixed or went with the data. Profile it the same way again once real use has built up rows.

## 3. Checked and fine

- **Static checks:** ruff and pyright are clean on `backend/app` (0 errors); `tsc` (app and e2e) and eslint are clean; `openapi.json` matches the backend models.
- **Auth:**
  - every data router is behind `auth.bind_actor`;
  - the WebSocket uses signed single-use tickets;
  - `/push` and `/metrics` are blocked at Kong, which also rate-limits login (20 a minute) and upload (60 a minute);
  - `/audit` is routed to audit-metrics, the only role that serves it.
- **Events:** no duplicate or unused event types. In Pub/Sub mode each push request waits for its handler, so `cpu_idle = true` on the workers doesn't starve work. Only the in-process transport runs handlers in background tasks.
- **Claude replies:** every call site reads text through `llm_io.message_text`, which ignores thinking blocks; unreadable output is repaired or falls back (`llm_io` never raises).
- **Content guard:** a failed scan or a failed save never fails the ingest.

## 4. Not verified, and where I need you

- **Grants:** PostgREST can't show them; run the query in REFACTORING_AUDIT §4 in the SQL editor. `latest_risk_scores` also refused the service key (403) before the wipe.
- **Runtime behaviour:**
  - redeliveries (findings 1 and 2) were found by reading the code, not observed;
  - the dead-letter subscription would show whether any happened;
  - Cloud Run logs for "detection run starting" twice on one document would confirm a duplicate run.

## Suggested order

1. **Idempotency:** findings 1, 2 and 3; small changes, each with a unit test that delivers the event twice.
2. **Officer-facing:** findings 6 and 8.
3. Finding 14 with the next deploy; the rest of the Low ones as time allows.

## 5. Database load (2026-11-01)

**Source:** Supabase's API logs for the 24 hours to 2026-11-01 07:50 UTC, about 7,600 PostgREST requests, grouped by query shape with ids removed. The window includes about 2 hours of testing (04:00–06:00 UTC), plus local runs and the deployed services; all use the same client, so the logs can't tell them apart. The frontend's own calls to the backend aren't in these logs; the figures marked *(earlier trace)* come from the local run log reviewed on 2026-10-31.

**What a query costs:** 140–300 ms each, measured at Supabase's edge (p95 up to 820 ms). Rows are small, so the number of queries decides the time, not their size.

| # | Finding | Evidence | Suggested fix |
|---|---|---|---|
| P1 | **A snapshot rebuild re-reads the whole case:** the case, its documents, the latest run, every extracted field and every detection report, whatever changed. | One case analysis (05:28:43–05:29:04) made 110 queries, about 21 s of query time, for a run that writes 9 rows: `cases` 17, `case_analysis_runs` 18, `documents` 15, `extracted_fields` 15, `detection_results` 13. The case-wide fields read ran about 500 times in 24 h (160–250 ms each), the case-wide reports read about 400. During a 7-document upload: 65 change events, 20 rebuilds *(earlier trace)*. | Rebuild only the part that changed: a status or run change doesn't need fields or reports. Coalesce events over a longer window during a run. |
| P2 | **The case row is read again and again within one action.** | `cases?id=eq.<id>` ran 847 times (494 of them in the 2-hour test window), 17 times in one analysis run. Callers include the routers' case dependency, the snapshot, the stage sync and the analysis readiness check. | Keep the case loaded once per request or run, and pass it to the code that needs it. |
| P3 | **Each document is read and written many times over its life.** | Upload through detection, per document: 11 updates (6–12), 17 reads of its row, 4 more reads of its `guard_result` column alone (`hidden_text_tool` and `prompt_injection_tool` each read it), 3.4 field reads, 2.7 report reads. | Combine the pipeline's updates into 2 or 3. Read `guard_result` with the row in detection's fetch cache. |
| P4 | **The case page fetches the same data from several components.** | Finding-review reads (`findings` where reviewer is set, and `review_sign_offs`) ran about 400 times, against about 100 case opens: roughly 3 per open, 6 in 20 s at worst *(earlier trace)*. The cost endpoint fires in pairs *(earlier trace)*. | One shared fetch per case page (a context or a small cache). |
| P5 | **Opening a case or the case list costs 5 queries per case for its summary.** | Each ran about 113 times for single cases: the documents, accepted detection findings, `latest_risk_scores`, and 2 reads of `audit_events` (who created the case, how long processing took). | Store the creator and the processing time on `cases` when they're known. That removes both `audit_events` reads. |
| P6 | **Approving 7 documents sends 7 requests**, each repeating the case and stage reads (5 to 9 queries). | *(earlier trace)* | A bulk-approve endpoint. |
| P7 | **The sweeper reads all in-flight work every 2 minutes**, whether or not anyone is using the app. | 720 scans of in-flight documents, the slowest query in the log (539 ms average), plus the same number of "analysis running too long" checks: about 19% of all traffic. The slowness is probably a fresh connection on each tick (audit-metrics scales to zero); not verified. | Correct as it is. If the traffic matters, run it every 5 minutes. |
| P8 | **Each API start re-checks every case's stage** (`sync_all_cases`). | 37 starts in 24 h (local reloads and cold starts), about 2–3 queries per case each. Small with 3 cases; it grows with every case. | Skip finalised cases, or drop it now that stage syncs flush within the request. |
| P9 | **A document preview signs its file URL afresh every time.** | One file was signed 34 times in a day. | Reuse a signed URL until shortly before it expires. |
| P10 | **Locally, each detection finish builds the snapshot twice** (the worker's push and the realtime listener). In the cloud the worker's push does nothing. | *(earlier trace)* | Skip the worker's push when the listener runs in the same process. |

**The wipe doesn't explain these.** The counts above are queries per action, set by the code paths, not by how much data there is. With 3 cases and 26 documents, the wipe hides the problems below rather than causing any.

### Queries that grow with the whole database

Everything that reads one case (the snapshot, its fields, findings and notes) grows only with that case's documents, which is fine. These grow with every case or document ever stored:

| # | Query | Cost as the data grows | Where | Suggested fix |
|---|---|---|---|---|
| S1 | **The case queue sorted or filtered by risk** reads every matching case, all their documents' statuses and their saved scores, then slices the page in Python. | Every case, on every page view with that sort or filter. | `case_service.list_cases_page` (the `by_risk or risks` branch) | Keep the latest risk level and score on `cases` when a run saves them, so the database sorts, filters and pages. |
| S2 | **The queue's stage counts** read every case's status on each call. | Every case, every time the queue loads. | `case_service.case_counts` | A grouped count in the database (a view or function), with an index on `cases.status`. |
| S3 | **The startup stage sync** re-checks every case, about 3 queries each. | Every case, on every API start (37 a day now). | `case_stage.sync_all_cases` | Skip finalised cases, or drop it (see P8). |
| S4 | **The precision and recall figures** read every reviewed finding in the database and every case id, then filter in Python. | Every review ever saved, on each load. | `routers/finding_reviews.py` (`list_all_reviews`, `case_ids_matching`) | Filter by date and stage in the query, or aggregate in a view. |
| S5 | **The sweeper and the monitoring page** read documents by status, with no index on `status` and no paging. | A full scan of `documents` every 2 minutes. Past 1,000 matches the result is silently cut short (PostgREST's limit); failed documents only accumulate, so `/monitoring/issues` will miss some. | `document_repository.get_documents_in_status` | An index on `documents.status`, and `fetch_all`. |
| S6 | **The sweeper's "analysis running too long" check** filters runs by status, with no index on it. | A full scan of `case_analysis_runs` every 2 minutes. | `case_pipeline/repository.running_started_before` | A partial index on running runs. |
| S7 | **The case queue's date order and stage filter** have no index. | A sort of every case per page. | `case_repository.list_cases_page` | An index on `cases (created_at, id)` and on `cases (status)`. |
| S8 | **Case search** matches `*q*` on the name and number, which no ordinary index can serve. | A scan of every case per keystroke search. | `case_repository._matching` | A trigram index (`pg_trgm`) once cases number in the thousands; not needed before. |

`audit_events` grows forever by design, but every read filters on a case or document, and both have indexes, so it's fine.

**Corrections to the earlier read:** `review_sign_offs` is in use. It holds an officer's sign-off on a review group with no findings (`findings_repository.SIGN_OFFS_TABLE`). It isn't a leftover from another branch.

## 6. SQL to run (Supabase SQL editor)

Indexes for the unindexed foreign keys the performance advisor lists, plus the risk score lookup (`latest_risk_scores` picks each case's newest row). Then a primary key for `langgraph_trace`, which has only a unique constraint. The key fails if any trace row has no document. Every statement can be rerun safely. After running it, `backend/sql/schema.sql` needs the same changes.

```sql
create index if not exists risk_scores_case_scored_idx on public.risk_scores (case_id, scored_at desc);
create index if not exists case_notes_document_idx on public.case_notes (document_id);
create index if not exists case_notes_parent_idx on public.case_notes (parent_id);
create index if not exists review_sign_offs_document_idx on public.review_sign_offs (document_id);

-- document_id's unique constraint becomes the primary key (skipped once it is one).
do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'langgraph_trace_pkey') then
        alter table public.langgraph_trace drop constraint langgraph_trace_document_id_key;
        alter table public.langgraph_trace add constraint langgraph_trace_pkey primary key (document_id);
    end if;
end $$;

-- S2, S5, S6, S7: the lookups that grow with every case and document.
create index if not exists cases_created_idx on public.cases (created_at desc, id);
create index if not exists cases_status_idx on public.cases (status);
create index if not exists documents_status_idx on public.documents (status);
create index if not exists case_analysis_runs_running_idx on public.case_analysis_runs (started_at) where status = 'running';
```
