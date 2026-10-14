# Pending work

Product roadmap (Detection Agent, Per-Case Pipeline, 5-service Pub/Sub split) stays documented in
`CLAUDE.md`'s "Future Direction" — not duplicated here.

## LangGraph pipeline

- **Postgres checkpointing + pruning** — not built. Crash recovery currently just fails the
  document the normal way; no time-travel, no thread cleanup.
- **Detection status values** — no `pending_review` or `detecting` document statuses yet. Findings stay on
  the LangGraph trace by design.
- **Detection Agent bugs** — nine logged in `FRAUD_DETECTION_MAP.md` §6. The two high-severity ones: an
  unparseable lead plan reports "inconclusive" with no error recorded, and a lost run can leave the
  panel stuck on "Scanning…" with no retry path.
- **Per-provider rate limiting** — no shared Anthropic semaphore/rate limiter across the 4 queue
  workers yet. Detection can consult up to three specialists per document (each running only the tools it
  picks), plus one second look.
- **Cache-hit-ratio metrics** — cache read/creation token counts are not recorded into Prometheus.
  Blocked on the Prometheus `/metrics` endpoint below.
- **Guardrails/evals/CI** — golden-set nightly evals (classification accuracy, per-field
  extraction, escalation rate, cache hit rate) and a mocked-subset-per-PR check: not built.
- **LangGraph Store (learning from officer corrections)** — last phase, not started.
- **Extraction truncation detection** — `extract.run_extraction` doesn't check `stop_reason == "max_tokens"`.
  A reply cut off at the token cap fails `json.loads`, reads as "did not return valid JSON", and triggers the
  reclassify loop until `Unresolved after N reclassification attempt(s)`. Should fail with a clear "output
  truncated" reason and no reclassify. The cap was raised 4096 → 16000 (`_EXTRACTION_MAX_TOKENS`), which only
  defers it; very long bank statements may still overflow and need chunked transaction extraction.

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
- **Dead code in `UploadPage.tsx`**: `isExtractionNotConfiguredFailure`, `structuredFieldEntries`,
  `REQUIREMENT_LABELS`, `formatFieldValue`, plus `savingFields`, `saveFieldErrors`, `setFieldDraftValue`,
  `saveFieldDraft`, `isApproving`. Remove next.

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
