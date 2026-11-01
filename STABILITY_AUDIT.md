# Stability audit (main, 2026-10-29; updated 2026-11-01)

A white-box read of `main`, looking for what is failing now, what will fail under load or a retry, and what the many merges left behind. The findings that `REFACTORING_AUDIT.md` and `PENDING_WORK.md` already list are not repeated here.

**How it was checked:** static checks (ruff, pyright, tsc, eslint, the OpenAPI and generated-types checks), reading the code paths below, and PyPI's release metadata. No pytest or Playwright runs, no database reads, and no Claude calls.

**The database:** it was wiped on 2026-10-31, and what the 2026-10-30 read found went with the data or is fixed. Once real use has built up rows, profile it again the same way (CLAUDE.md, "Profiling").

## 1. Findings, most severe first

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| 1 | Medium | **The detection worker isn't idempotent.** `on_requested` runs detection for any delivery of `document.approved` or `detection.retry_requested` without checking status, and `run_detection` calls `mark_detecting` unconditionally. A Pub/Sub redelivery (a run over the 600 s deadline, a lost acknowledgement, a shutdown mid-request) runs the whole detection again. It flips `detected` back to `detecting`, replaces the report and its findings (with the officers' verdicts on them) and spends the Claude tokens again. The design rule is "idempotent compare-and-swap handlers"; the doc-worker follows it (`queued → processing`), this one doesn't | `workers/detection.py` `on_requested`, `detection/graph.py:627` | Start with `update_status(DETECTING, expected_current_statuses=[APPROVED])` and return if it matched nothing |
| 2 | Medium | **The case worker isn't idempotent either.** `run_requested` doesn't claim the run, so a redelivered `case.analysis.requested` runs the full analysis again (the planner and judge on Sonnet, the outlook and summary on Haiku) | `case_pipeline/service.py` `run_requested` | Claim the run (`running` and not yet started) before executing |
| 3 | Medium | **Approve is check-then-write.** The route checks for `analyzed`, then `approve_document` writes `approved` without a condition. Two approvals at once (a double-click, or two officers) both pass and both publish `document.approved`, so two detection runs start. Fixing 1 fixes the cost; this fixes the double event | `document_service.approve`, `document_repository.approve_document` | `.eq("status", "analyzed")` on the update; 409 if it matched nothing |
| 4 | Medium | **The publishable key can rewrite or wipe the database.** Most backend reads and writes go through `SUPABASE_PUBLISHABLE_KEY` (role `anon`), and `anon` holds SELECT, INSERT, UPDATE, DELETE and TRUNCATE on `cases`, `documents`, `extracted_fields`, `langgraph_trace` and `risk_scores`, with `using (true)` policies (`schema.sql`, "Grants"). The key never reaches the browser, but publishable keys are meant to be public: anyone who obtains it, with the project URL, can change or delete every case and document | `supabase_client.py`, `schema.sql` grants | Move the backend to `SUPABASE_SECRET_KEY` for every table (service_role already bypasses RLS), then revoke `anon`'s grants. The SQL is the user's to run |
| 5 | Medium | **A document the content guard blocks can never be processed.** It's marked `failed`; Retry blocks it again (the scan is deterministic); there's no officer override. One false positive on a real document leaves it permanently failed | `langgraph/nodes.py` (the guard step) | An officer action "process anyway", recorded in the audit trail |
| 6 | Low | **The dashboard hides load failures.** `useDashboardCases` catches every error, so a failed first load shows zeros as if there were no cases | `frontend/src/pages/useDashboardCases.ts:35` | Keep an error state and show it |
| 7 | Low | **The upload trusts the browser's `Content-Type`.** The server doesn't check the file's first bytes (the frontend does), so any file labelled PDF is stored and parsed. Junk fails at ingest, so this is hardening | `routers/upload.py:45` | Check the first bytes (`%PDF`, PNG and JPEG signatures) server-side |
| 8 | Low | **The leak check keeps prompts of cancelled calls.** `on_llm_end` and `on_llm_error` never fire for a cancelled call, so its instruction text stays in the dict. It grows slowly with every cancel; the word matching also runs on the event loop | `guardrails/leak_check.py` | Cap the dict (drop entries older than the LLM timeout) |

## 2. Not verified

- **Redeliveries** (findings 1 and 2) were found by reading the code, not observed:
  - the dead-letter subscription would show whether any happened;
  - Cloud Run logs for "detection run starting" twice on one document would confirm a duplicate run.

## Suggested order

1. **Idempotency:** findings 1, 2 and 3; small changes, each with a unit test that delivers the event twice.
2. **The key:** finding 4, before anyone outside the team uses the deployment.
3. **Officer-facing:** findings 5 and 6.
4. Findings 7 and 8 as time allows.

## 3. Database load

Measured on 2026-11-01 from Supabase's API logs (140–300 ms a query, so the number of queries decides the time). The fixes for what that found are in; what's left:

- **Measure again** once the changes are deployed and a few cases have run: one case analysis made 110 queries, and each document about 11 updates and 17 reads of its row. The same log queries (by query shape, ids removed) show whether those fell.
- **Case search** matches `*q*` on the name and number, which no ordinary index can serve, so every search scans every case (`case_repository._matching`). Add a trigram index (`pg_trgm`) once cases number in the thousands; not needed before.
- **The precision and recall figures** still read every review when no filter is set (`findings_repository.list_reviews`): fine while reviews number in the thousands. Past that, aggregate in the database.
