# Pending work

Open work only. The product roadmap (automatic case analysis, the 5-service Pub/Sub split) is in
`CLAUDE.md`'s "Future Direction". Last reviewed 2026-10-15.

## Decided, not started

- **Recommended additional documents.** The rule-based supporting-document recommendations
  (`risk/supporting_documents.py`) should feed the agent, and what the agent concludes should be saved to
  `cases.recommended_additional_documents`. Today that column is always empty and the API reports the
  missing checklist types under the same name; resolve the naming when this is built.
- **Case-level flags.** Cross-document and identity findings (a name or address that differs between
  documents, case-analysis findings) are deliberately not in the per-document
  `flags` table. They need their own module.
- **Automatic case analysis.** The officer clicks it. The gate on every document completion, supersede with
  a run-id fence, debounce and re-run cap (`CROSS_DOCUMENT_AND_RISK_PLAN.md` Step 0) are not built.
- **Guard against repeated runs.** Each "Run again" spends about 12k tokens even when the documents are
  unchanged. Skipping a run whose input fingerprint matches the last complete run would stop that, but changes
  what the button does.
- **Install `exiftool`.** Without it the metadata check and the AI-provenance part of the AI-generation check
  report "could not run" on every document, so they never contribute to a score.
- **Officer review of flags.** The `flags` table has `status`, `reviewed_by` and `reviewed_at`; nothing reads
  or writes them yet, and the frontend doesn't show flags.
- **Flags for older documents.** Flags are written when a detection run finishes. Documents detected before
  the table was wired have a report but no flag rows, and approved documents can't re-run detection.

## Verification debt

- **Tests changed but not run.** The 2026-10-15 changes updated `tests/test_documents.py`,
  `test_document_repository.py`, `test_case_service.py`, `test_case_risk.py` and `conftest.py`. They compile
  but have not been run. Run `pytest backend/tests` before relying on them.
- **Checks that exist only as scratch scripts**, worth turning into repo tests: the verifier rules (two
  documents or a computed fact; a probe that re-reads the same fields doesn't count), the bias screens
  (`bias.py`), year-less statement dates (`tools/common.row_date`), the single-flight detection cache and
  snapshot builder, the flag write-on-change memo, and `flags_repository.build_rows`.
- **No SQL in the repo.** The migration files were removed on 2026-10-15 and the schema lives only in
  Supabase. A new environment can't be built from the repo; the objects it needs are listed in `README.md`,
  and the `CREATE` statements for them would have to be exported from Supabase first.

## LangGraph pipeline

- **Postgres checkpointing + pruning** — not built. Crash recovery currently just fails the
  document the normal way; no time-travel, no thread cleanup.
- **Detection Agent bugs** — eight open in `FRAUD_DETECTION_MAP.md` §6. The high-severity one: a lost run
  (server restart) before an error is recorded can leave the panel on "Scanning…" with no retry path. The
  other long-standing one: a finding a specialist never assessed is kept at `medium` (it is no longer stored
  as a flag, but still counts towards the case risk as "unreviewed").
- **Per-provider rate limiting** — no shared Anthropic limiter across the 4 queue workers. Detection runs
  are capped at `DETECTION_CONCURRENCY` (2), a detection run at `DETECTION_MAX_MODEL_CALLS` (30) and the
  case analysis at 6, but nothing bounds the total across the queue, detection and an analysis at once.
- **Prometheus metrics** — token and cache counts are now logged per unit of work (`query_stats.py`,
  `perf` log lines) but not exported. Blocked on the `/metrics` endpoint below.
- **Guardrails/evals/CI** — golden-set nightly evals (classification accuracy, per-field
  extraction, escalation rate, cache hit rate) and a mocked-subset-per-PR check: not built.
- **LangGraph Store (learning from officer corrections)** — last phase, not started.
- **Extraction truncation detection** — `extract.run_extraction` doesn't check `stop_reason == "max_tokens"`.
  A reply cut off at the token cap fails `json.loads`, reads as "did not return valid JSON", and triggers the
  reclassify loop until `Unresolved after N reclassification attempt(s)`. Should fail with a clear "output
  truncated" reason and no reclassify. The cap was raised 4096 → 16000 (`_EXTRACTION_MAX_TOKENS`), which only
  defers it; very long bank statements may still overflow and need chunked transaction extraction.
- **Spelling check re-ingests the file.** `check_spelling_anomalies` re-runs text extraction (and OCR for a
  scan) instead of reusing what ingestion already produced.
- **Detection prompts don't cache.** The specialists run on Haiku, whose minimum cacheable prompt is longer
  than theirs, so their `cache_control` markers do nothing. Either lengthen the shared prefix or accept it.

## Case pipeline (cross-document analysis and risk outlook)

Built and exercised end to end on generated cases. Design: `CROSS_DOCUMENT_AND_RISK_PLAN.md`.

- **Plan-then-judge has no follow-up** — the plan fixes its checks before seeing any result, so an
  investigation can't adapt (for example re-query with a different filter). It is deliberately cheap (2 Sonnet
  calls). If recall is too low in the evals, add one optional second round for hypotheses the judge marks
  "needs more evidence".
- **Trace includes dropped hypotheses** — `case_analysis_runs.trace` (shown in the case page's debug panel)
  lists every hypothesis tried and why it was discarded (`dropped_for_bias`, `dropped_for_scope`, or a verifier
  reason). The plan says unproven hypotheses are never shown or stored against a case. Decide before this is
  exposed beyond developers.
- **Evals** — false-positive rate on clean generated cases (including Other documents), recall on planted
  contradictions, and false-positive parity across subgroups.
- **Guardrails still open** — a dismissal flow for cross-document findings, audit retention, and the three
  sponsor questions in the plan. The protected-attribute screen over-blocks on purpose (a bank named "Indian
  Overseas Bank" would drop a hypothesis that names it).
- **Case-level frontend** — no frontend unit tests for `CaseAnalysisCard.tsx`; no Playwright spec.
- **Duplicated normalisers** — `case_match.py`, `company_identity.py` and `address_matching.py` each define
  their own company and person name normalisers with different rules. Consolidate in one pass, since the
  rules differ and the checks' results would change.
- **`Scrum-254-address-matching`** — commit `4a0742c` (address normalisation edge cases) is applied by hand
  to `case_pipeline/cross_document/address_matching.py`, not merged. Merge the branch to record it.
- **Vertical text** — `text_overlay.py` skips vertical text by an aspect-ratio guess because `_Line` records no
  direction. Extracting the direction from PyMuPDF would replace the guess.
- **Unrelated failing test** — `test_layout_reference.py::test_committed_references_load_and_have_no_value_text`
  fails on Windows (it reads the reference JSON with the default cp1252 encoding).

## Performance (what is left after the 2026-10 pass)

Budgets are in `HOW_IT_WORKS.md` §8; read them off the `perf` log lines.

- **Per-document pipeline is about 16 queries.** Two case resyncs (after classification and after extraction)
  each list the case's documents and fields. They could be coalesced.
- **`GET /cases/{id}/analysis` costs 5 queries** each time the card reloads (documents, fields and traces to
  recompute staleness).
- **A burst of changes can chain snapshot builds.** The first requester waits for the follow-up build; handing
  follow-ups to a background thread would keep its latency down.
- **`GET /documents/{id}/detection/case-match` costs 7 queries** and is called after each field edit.
- **Dev StrictMode** fires some document-page requests twice (`/documents/{id}`, `langgraph-trace`,
  `detection/report`).
- **Case list has no pagination.** It no longer loads fields, but it still loads every case and every
  document's status, and search, filters and sort run in the browser. Server-side paging would need those moved.

## Architecture (Kong / Prometheus / Grafana)

Not started, no urgency until the Cloud Native award work picks up:

- **Kong API gateway** — would sit in front of whatever service boundaries exist; can add value
  even before any real service split (auth, rate limiting, routing). Today's boundary crossings
  are all direct Python calls (`case_service.sync_submission_finalized(...)`,
  `document_repository.get_document(...)`, `queue_service.enqueue(...)`), not events or API
  calls — that replacement is the actual work, not a file move.
- **Prometheus `/metrics`** — no app-level metrics endpoint yet (LLM token usage/cost per
  document, classify/extract latency, queue depth, stuck-document count). Gateway-level metrics
  would come free from Kong's own plugin once that's added.
- **Grafana** — dashboards over both, once they exist. Cloud Trace can be added as a data source
  to also surface the existing OpenTelemetry traces here instead of only in the GCP console.

## Test suite

- **Coverage on the deferred LangGraph files** — `classify_support.py`, `llm_classify.py`,
  `routers/detection.py`, `routers/upload.py`, `vision_tool.py`, `telemetry.py`,
  `detection/specialists.py`, `classify_graph.py`, `detection/tools.py`, `trace.py`,
  `detection/graph.py`, `nodes.py`, `pipeline.py` — 21-74% coverage each, needs real
  LangGraph-state/async fixtures. Explicitly deferred; pick up only when asked.

## Frontend structure

- **Group card props** — `DocumentDetailsPage.tsx` and `UploadPage.tsx` now use hooks, but the cards still
  take many props (`ExtractedInformationCard`, `DocumentListPanel`). Group them or read from hooks. Verify with
  `tsc`, `npm run build`, and the `document-review`, `upload`, `case-management` and `checklist` specs.
- **Loading pages (not urgent)** — three pages in `App.tsx` are a bare line of text with no header: the
  `Suspense` fallback (`Loading…`, line 298), `Loading case...` (338) and `Loading document...` (365). Give them
  the same design as the document-not-found page. The case and document routes also wait for the whole case
  list before they start loading their own data; letting them load independently would remove that delay.

## Small known gaps

- `case_service.delete_case`'s "remove every document, then the case row" cascade has no direct
  unit test — only indirect coverage via whatever else touches `case_service`.
- `document_repository.update_status` only ever writes fields passed as non-`None`, so it can't
  clear a stale `failure_reason` from a prior attempt — a document that fails, then later
  succeeds on retry, still shows its old failure reason. Pre-existing, cosmetic, not fixed.
- Same staleness shape in the detection trace: a `detection_error` from a failed run isn't cleared
  when a later run succeeds (the success check just takes priority — see
  `FRAUD_DETECTION_MAP.md` §2/§3). Fine for the common case; an officer who re-triggers a crashed
  run, gets a *second* failure, won't see that it's the new failure if an old report also exists.
- `submission_finalized` is remembered per process (`case_service._synced_finalized`) so an unchanged value
  isn't rewritten. With more than one backend instance, or a hand edit in the database, the stored value is
  corrected on the next change in the computed answer, not before.
