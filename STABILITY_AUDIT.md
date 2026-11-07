# Stability audit (main, 2026-10-29; updated 2026-11-01)

A white-box read of `main`, looking for what is failing now, what will fail under load or a retry, and what the many merges left behind. The findings that `REFACTORING_AUDIT.md` and `PENDING_WORK.md` already list are not repeated here.

**How it was checked:** static checks (ruff, pyright, tsc, eslint, the OpenAPI and generated-types checks), reading the code paths below, and PyPI's release metadata. No pytest or Playwright runs, no database reads, and no Claude calls.

**The database:** it was wiped on 2026-10-31, and what the 2026-10-30 read found went with the data or is fixed. Once real use has built up rows, profile it again the same way (CLAUDE.md, "Profiling").

## 1. Findings, most severe first

| # | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|

| 5 | Medium | **A document the content guard blocks can never be processed.** It's marked `failed`; Retry blocks it again (the scan is deterministic); there's no officer override. One false positive on a real document leaves it permanently failed | `langgraph/nodes.py` (the guard step) | An officer action "process anyway", recorded in the audit trail |

| 8 | Low | **The leak check keeps prompts of cancelled calls.** `on_llm_end` and `on_llm_error` never fire for a cancelled call, so its instruction text stays in the dict. It grows slowly with every cancel; the word matching also runs on the event loop confused| `guardrails/leak_check.py` | Cap the dict (drop entries older than the LLM timeout) |

## 2. Not verified

- **Redeliveries** (findings 1 and 2) were found by reading the code, not observed:
  - the dead-letter subscription would show whether any happened;
  - Cloud Run logs for "detection run starting" twice on one document would confirm a duplicate run.

## Suggested order

3. **Officer-facing:** findings 5 and 6.
4. Findings 7 and 8 as time allows.

## 3. Database load

Measured on 2026-11-01 from Supabase's API logs (140–300 ms a query, so the number of queries decides the time). The fixes for what that found are in; what's left:

- **Measure again** once the changes are deployed and a few cases have run: one case analysis made 110 queries, and each document about 11 updates and 17 reads of its row. The same log queries (by query shape, ids removed) show whether those fell.
- **Case search** matches `*q*` on the name and number, which no ordinary index can serve, so every search scans every case (`case_repository._matching`). Add a trigram index (`pg_trgm`) once cases number in the thousands; not needed before.
- **The precision and recall figures** still read every review when no filter is set (`findings_repository.list_reviews`): fine while reviews number in the thousands. Past that, aggregate in the database.
