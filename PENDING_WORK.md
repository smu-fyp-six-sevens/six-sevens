# Pending work

Open product and platform work only. Code smells and optimisations are in `REFACTORING_AUDIT.md`; test work is in `backend_tests.md`; detection bugs are in `FRAUD_DETECTION_MAP.md` §6. The roadmap (automatic case analysis, the 5-service Pub/Sub split) is in `CLAUDE.md`'s "Future Direction". Updated 2026-10-19.

## Decided, not started

- **Recommended additional documents.** The rule-based recommendations (`risk/supporting_documents.py`) should feed the agent, and what the agent concludes should be saved to `cases.recommended_additional_documents`. Today that column is always empty and the API reports the missing checklist types under the same name; resolve the naming when this is built.
- **Case-level flags.** Cross-document and identity findings are deliberately not in the per-document `flags` table. They need their own module.
- **Automatic case analysis.** The officer clicks it. The gate on every document completion, supersede with a run-id fence, debounce and re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md`, Step 0) are not built.
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when the documents are unchanged. Skipping a run whose input fingerprint matches the last complete run would stop that, but changes what the button does.
- **Officer review of flags.** The `flags` table has `status`, `reviewed_by` and `reviewed_at`; nothing reads or writes them, and the frontend doesn't show flags.
- **Flags for older documents.** Flags are written when a detection run finishes. Documents detected before the table was wired have a report but no flag rows, and approved documents can't re-run detection.
- **Install `exiftool` locally.** Without it the metadata check and the AI-provenance part of the AI-generation check report "could not run", so they never contribute to a score. Optional local fixes: a startup warning when it is missing, a PyMuPDF fallback for basic metadata, and re-reading the file when the stored record is a failure.

## Verification debt

- **Checks that exist only as scratch scripts**, worth turning into repo tests: the verifier rules (two documents or a computed fact; a probe that re-reads the same fields doesn't count), the bias screens (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and snapshot builder, the flag write-on-change memo, and `flags_repository.build_rows`.
- **No SQL in the repo.** The schema lives only in Supabase. A new environment can't be built from the repo; the objects it needs are listed in `README.md`, and their `CREATE` statements would have to be exported from Supabase first.

## LangGraph pipeline

- **Postgres checkpointing and pruning** are not built. Crash recovery fails the document the normal way; no time-travel, no thread cleanup.
- **Per-provider rate limiting.** No shared Anthropic limiter across the 4 queue workers. Detection runs are capped at `DETECTION_CONCURRENCY` (2), one at `DETECTION_MAX_MODEL_CALLS` (30) and the case analysis at 6, but nothing bounds the total across the queue, detection and an analysis at once.
- **Prometheus metrics.** Token and cache counts are logged per unit of work (`query_stats.py`) but not exported; blocked on the `/metrics` endpoint below.
- **Guardrails, evals and CI.** Golden-set nightly evals (classification accuracy, per-field extraction, escalation rate, cache hit rate) and a mocked-subset-per-PR check are not built.
- **LangGraph Store** (learning from officer corrections): last phase, not started.
- **Very long bank statements** can still overflow the extraction cap (`_EXTRACTION_MAX_TOKENS` 16,000). An overflow now fails the document cleanly with the complete fields kept; chunked transaction extraction would fix it properly.
- **Detection prompts don't cache.** The specialists run on Haiku, whose minimum cacheable prompt is longer than theirs, so their `cache_control` markers do nothing. Lengthen the shared prefix or accept it.
- **Vertical text.** `text_overlay.py` skips vertical text by an aspect-ratio guess because `_Line` records no direction. Extracting the direction from PyMuPDF would replace the guess.

## Case pipeline (cross-document analysis and risk outlook)

Built and exercised end to end on generated cases. Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

- **Plan-then-judge has no follow-up.** The plan fixes its checks before seeing any result, so an investigation can't adapt (for example re-query with a different filter). It is deliberately cheap (2 Sonnet calls). If recall is too low in the evals, add one optional second round for hypotheses the judge marks "needs more evidence".
- **The trace includes dropped hypotheses.** `case_analysis_runs.trace` (shown in the case page's debug panel) lists every hypothesis tried and why it was discarded, while the design says unproven hypotheses are never shown or stored against a case. Decide before this is exposed beyond developers.
- **Evals:** false-positive rate on clean generated cases (including Other documents), recall on planted contradictions, and false-positive parity across subgroups.
- **Guardrails still open:** a dismissal flow for cross-document findings, audit retention, and the three sponsor questions in the plan. The protected-attribute screen over-blocks on purpose (a bank named "Indian Overseas Bank" would drop a hypothesis that names it).
- **Case-level frontend:** no frontend unit tests for `CaseAnalysisCard.tsx` and no Playwright spec.

## Performance

Budgets are in `HOW_IT_WORKS.md` §8; read them off the `perf` log lines. Query-level optimisations (the case list, snapshot reads, and so on) are in `REFACTORING_AUDIT.md` §3. Remaining here:

- **`GET /cases/{id}/analysis` costs 5 queries** each time the card reloads (documents, fields and traces, to recompute staleness).
- **`GET /documents/{id}/detection/case-match` costs 7 queries** and is called after each field edit.
- **A burst of changes can chain snapshot builds.** The first requester waits for the follow-up build; handing follow-ups to a background thread would keep its latency down.
- **The case list has no pagination.** Search, filters and sort run in the browser; server-side paging would need those moved.
- **Dev StrictMode** fires some document-page requests twice.

## Architecture (Kong / Prometheus / Grafana)

Not started; no urgency until the Cloud Native award work picks up.

- **Kong API gateway** would sit in front of whatever service boundaries exist and can add value (auth, rate limiting, routing) even before a real split. Today's boundary crossings are direct Python calls (`case_service.sync_submission_finalized`, `document_repository.get_document`, `queue_service.enqueue`), not events or API calls; replacing them is the actual work.
- **Prometheus `/metrics`:** no app-level endpoint yet (LLM token usage and cost per document, classify and extract latency, queue depth, stuck-document count). Gateway metrics would come free from Kong's plugin.
- **Grafana** dashboards over both, once they exist; Cloud Trace can be a data source to surface the existing OpenTelemetry traces.

## Frontend

- **Group card props.** `DocumentDetailsPage.tsx` and `UploadPage.tsx` use hooks, but the cards still take many props (`ExtractedInformationCard`, `DocumentListPanel`). See `REFACTORING_AUDIT.md`. Verify with `tsc`, `npm run build` and the `document-review`, `upload`, `case-management` and `checklist` specs.
- **Loading pages (not urgent).** Three pages in `App.tsx` are a bare line of text with no header: the `Suspense` fallback, `Loading case...` and `Loading document...`. Give them the document-not-found page's design. The case and document routes also wait for the whole case list before loading their own data; loading independently would remove that delay.

## Small known gaps

- `document_repository.update_status` writes only fields passed as non-`None`, so it can't clear a stale `failure_reason`: a document that fails and later succeeds on retry still shows its old reason. Cosmetic.
- The same shape in the detection trace: a `detection_error` from a failed run isn't cleared when a later run succeeds (a report takes priority). An officer who re-triggers a crashed run and gets a second failure won't see it is new if an old report also exists.
- `submission_finalized` is remembered per process (`case_service._synced_finalized`) so an unchanged value isn't rewritten. With more than one backend instance, or a hand edit in the database, the stored value is corrected on the next change in the computed answer, not before.
