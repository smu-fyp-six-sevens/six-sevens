# Stability audit (main, 2026-10-29)

A white-box read of `main` at `d6794e4`, looking for what is failing now, what will fail under load or a retry, and what the many merges left behind. The findings that `REFACTORING_AUDIT.md` and `PENDING_WORK.md` already list are not repeated here.

**How it was checked:** static checks (ruff, pyright, tsc, eslint, the OpenAPI and generated-types checks), reading the code paths below, and PyPI's release metadata. No pytest or Playwright runs, no database reads, and no Claude calls.

## 1. Findings, most severe first

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| 1 | Medium | **The detection worker isn't idempotent.** `on_requested` runs detection for any delivery of `document.approved` or `detection.retry_requested` without checking status, and `run_detection` calls `mark_detecting` unconditionally. A Pub/Sub redelivery (a run over the 600 s deadline, a lost acknowledgement, a shutdown mid-request) runs the whole detection again. It flips `detected` back to `detecting`, replaces the report, deletes the document's finding reviews and spends the Claude tokens again. The design rule is "idempotent compare-and-swap handlers"; the doc-worker follows it (`queued → processing`), this one doesn't | `workers/detection.py:78`, `detection/graph.py:572` | Start with `update_status(DETECTING, expected_current_statuses=[APPROVED])` and return if it matched nothing |
| 2 | Medium | **The case worker isn't idempotent either.** `run_requested` doesn't claim the run, so a redelivered `case.analysis.requested` runs the full analysis again (the planner and judge on Sonnet, the outlook and summary on Haiku) | `case_pipeline/service.py:107` | Claim the run (`running` and not yet started) before executing |
| 3 | Medium | **Approve is check-then-write.** The route checks for `analyzed`, then `approve_document` writes `approved` without a condition. Two approvals at once (a double-click, or two officers) both pass and both publish `document.approved`, so two detection runs start. Fixing 1 fixes the cost; this fixes the double event | `document_service.approve`, `document_repository.approve_document` | `.eq("status", "analyzed")` on the update; 409 if it matched nothing |
| 4 | Medium | **Finding reviews are deleted on every rerun.** A full detection run deletes the document's reviews, and every completed case analysis deletes all cross-document reviews. That happens even when the rerun produces the same findings: the deterministic `xd-det-N` findings keep their IDs. Officers redo their review after every "Run again", and the precision and recall labels are lost | `detection/graph.py:554`, `case_pipeline/service.py:202` | Keep a review whose finding still exists in the new result (match on finding ID, or on check plus check code plus summary); delete only the orphans |
| 5 | Medium | **Saving a verdict can fail under a race.** `set_verdict` selects, then inserts. Two saves of the same finding at once both insert, the unique index rejects the second, and the officer sees "Failed to save". The batch save writes two round trips per finding in sequence, and isn't atomic despite its docstring ("a bad entry saves nothing" holds only for validation) | `finding_reviews_repository.set_verdict`, `routers/finding_reviews.py:97` | Upsert on `(case_id, finding_id)`, or catch the conflict and update; for the batch, one Postgres function, or accept and fix the docstring |
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

**Size and load.** Small: 56 cases, 289 documents, 4,999 extracted fields, 651 traces, 311 flags, 52 analysis runs, 479 audit events (20 to 110 a day). The heavy columns are JSON:

| Column | Average | Largest |
|---|---|---|
| `langgraph_trace.trace` | 7.5 KB | 70 KB |
| `case_analysis_runs.trace` | 18 KB | 47 KB |
| `case_analysis_runs.result` | 13 KB | 30 KB |
| `extracted_fields.field_value` (a transaction list is one value) | 2 KB | 7.6 KB |

The biggest case (24 documents) is 40 KB of document rows, 88 to 124 KB of fields, and 350 to 600 KB of traces. The live snapshot and the case analysis read only each trace's report (`trace.py` `get_detection_states`, by document id), so they don't pull the whole trace. Load is not a problem at this scale.

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| D1 | Medium | **A teammate's unmerged branch writes to the shared database.** `cases.status` holds eight values (`ready_for_analysis` 15, `pending_review` 13, `pending_evaluation` 10, `failed` 6, `case_analysed` 4, `fraud_detection` 4, `open` 3, `finalised` 1); `main` only ever writes `open` and reads none of them. Probably `SCRUM-80-Track-case-status`. Until it merges, `main` and the data disagree, and nothing in the repo records its schema | Supabase | Ask who owns them; get the schema change into the repo with the merge (REFACTORING_AUDIT repo 2); one shared dev database means every branch's experiments land in it (staging would separate them) |
| D2 | Medium | **362 of 651 traces (56%) belong to deleted documents.** `langgraph_trace` has no foreign key, and deletes before the explicit child cleanup left theirs. They're scanned by any query that filters inside the trace JSON (D3) | `langgraph_trace` | One-off, in the SQL editor: `delete from langgraph_trace t where not exists (select 1 from documents d where d.id = t.document_id);`. Then a foreign key with `on delete cascade` |
| D3 | Low | **A dead route scans every trace.** `GET /cases/{id}/detection/reports` (`routers/detection.py:442`, `trace.get_case_detection_reports`) filters on `trace->>case_id`, which no index covers, and reads whole traces (600 KB for the biggest case). It's also wrong after a move: the trace keeps the old `case_id`. Nothing calls it (only `api.gen.ts` lists it) | `routers/detection.py`, `langgraph/trace.py:102-125` | Delete the route and the two functions; regenerate the API types |
| D4 | Low | **PostgREST returns at most 1,000 rows a query.** Reads that page (`fetch_all`, `list_document_statuses`) are fine; `finding_reviews_repository.list_all` doesn't, so the dashboard's precision and recall would silently drop reviews past 1,000 (64 today) | `finding_reviews_repository.py:34` | Page it with `fetch_all` |
| D5 | Low | **One analysis run belongs to a deleted case** (`case_analysis_runs.case_id` has no foreign key; from before the explicit delete) | `case_analysis_runs` | `delete from case_analysis_runs r where not exists (select 1 from cases c where c.id = r.case_id);` |
| D6 | Info | **What the columns show:** `flags.related_document_ids` is null on every row and inserts succeed (so REFACTORING_AUDIT's "flag inserts may be failing" was wrong); `flags.reviewed_by`, `reviewed_at` are never set and `confidence` is empty on 80%; `documents.guard_result` exists and is written since the guard shipped (all older rows empty); `documents.metadata` is empty on 35 of the oldest 200 (before exiftool); 10 recent documents failed with the raw `temperature` 400 as their reason and 7 have no type | - | After the deploy, retry the documents that failed on the `temperature` error |

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

- **Grants and delete rules:** PostgREST can't show them; run the two queries in REFACTORING_AUDIT §4 in the SQL editor. `case_status_history` and `latest_risk_scores` also refuse the service key (403).
- **Runtime behaviour:**
  - redeliveries (findings 1 and 2) were found by reading the code, not observed;
  - the dead-letter subscription would show whether any happened;
  - Cloud Run logs for "detection run starting" twice on one document would confirm a duplicate run.

## Suggested order

1. **After the model upgrade deploys:** retry the documents that failed on the `temperature` error (D6).
2. **Idempotency:** findings 1, 2 and 3; small changes, each with a unit test that delivers the event twice.
3. **The shared database:** D1 (talk to the branch owners), then the two clean-up queries (D2, D5).
4. **Officer-facing:** findings 4, 5, 6 and 8.
5. D3, D4 and finding 14 with the next deploy; the rest of the Low ones as time allows.
