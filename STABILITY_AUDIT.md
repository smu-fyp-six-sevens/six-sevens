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
