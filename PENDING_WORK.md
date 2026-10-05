# Pending work

Consolidated from `BACKEND_CLEANUP_PLAN.md`, `BACKEND_FILE_MAP.md`,
`LANGGRAPH_MIGRATION_PLAN.md`, `LANGGRAPH_TESTING_SETUP.md`, and `TEST_SUITE_AUDIT.md` (all
deleted — their content was either completed work, now superseded, or folded in below). Product
roadmap (Detection Agent, Per-Case Pipeline, 5-service Pub/Sub split) stays documented in
`CLAUDE.md`'s "Future Direction" — not duplicated here.

## LangGraph pipeline

- **Postgres checkpointing + pruning** — not built. Crash recovery currently just fails the
  document the normal way; no time-travel, no thread cleanup.
- ~~**Detection → approval wiring**~~ — resolved: approving a document now fires the Detection
  Agent automatically as a background task (`routers/documents.py::_start_detection_after_approval`).
  Still no `pending_review`/`detecting` document statuses, and findings still live only on the
  `langgraph_trace` table's `detection_report`, nothing written to the `documents` table — that
  part of the original gap is unchanged, just no longer manual.
- **Per-provider rate limiting** — no shared Anthropic semaphore/rate limiter across the 4 queue
  workers yet (detection fans out up to 3x per document on top of that).
- **Cache-hit-ratio metrics** — prompt caching + `cache_gate.py` are built and working; recording
  cache read/creation token counts into Prometheus is not (blocked on the Prometheus `/metrics`
  endpoint below).
- **Guardrails/evals/CI** — golden-set nightly evals (classification accuracy, per-field
  extraction, escalation rate, cache hit rate) and a mocked-subset-per-PR check: not built.
- **LangGraph Store (learning from officer corrections)** — last phase, not started.
- ~~`classification_reasoning`~~ — resolved, not pending: confirmed dead code (no call site ever
  persisted it) and removed entirely from the schema/model this session.

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
- **In-process component boundaries** — mostly done (`document_repository`/`case_service` own
  their tables; `extracted_fields_repository` deliberately stays a separate, directly-importable
  module — two distinct tables, not folded together). An internal event bus to replace direct
  cross-component calls was discussed and deliberately deprioritized — revisit later, not now.

## Test suite

- **`tests/test_pdf.py` trim** — 88 tests / ~1800 lines against `app/injection/pdf.py`'s private
  implementation details (font-fallback chains, glyph/ink geometry, OCR box-splitting), while
  `test_fraud_injections.py`/`test_injectors.py` already cover the same tool's actual observable
  behaviour black-box. Recommended trim to ~10-15 tests covering just the public entry points
  (`discover_fields_from_pdf/image`, `replace_field_text`, `apply_text_emphasis`,
  `overlay_field_text`, `manipulate_field_region`). Never actioned — decide: do it, see the exact
  keep/cut list first, or leave as-is. (`app/injection/*` tests are currently excluded from
  regular runs via `conftest.py`'s `collect_ignore` regardless — dev-only tooling, not graded.)
- **Coverage on the deferred LangGraph files** — `classify_support.py`, `llm_classify.py`,
  `routers/detection.py`, `routers/upload.py`, `vision_tool.py`, `telemetry.py`,
  `detection/specialists.py`, `classify_graph.py`, `detection/tools.py`, `trace.py`,
  `detection/graph.py`, `nodes.py`, `pipeline.py` — 21-74% coverage each, needs real
  LangGraph-state/async fixtures. Explicitly deferred; pick up only when asked.
- **REG-004 / REG-005 known-defect tests (backend, `test.fail`)** — not yet written. Desired
  behaviour, code not changed: REG-004, a low-confidence named type on first extraction should use
  the generic prompt (currently uses the named type's schema, `nodes.py:142-146`). REG-005,
  confirming a type must re-run extraction against that type's schema (currently skipped when the
  type is unchanged, `routers/documents.py:843-848`). Full detail in `TEST_PLAN.md` Part 8.

## Frontend structure

- **Page state moved into hooks (done)** — `DocumentDetailsPage.tsx` (~570 lines) uses `useDocumentRecord`,
  `useDocumentCaseMatch`, `useFieldEditing`, `useDocumentTypeEditing`, `useDetection`, `useLangGraphTrace` and
  `useDocumentActions` in `pages/document-details/`. `UploadPage.tsx` (~510 lines) uses `useCaseSnapshot`,
  `useCaseSettings`, `useUploadSelection`, `useDocumentSelection` and `useDocumentActions` in `pages/upload/`.
  Still open: the cards take many props (`ExtractedInformationCard`, `DocumentListPanel`), which could now be
  grouped or read from hooks. Verify with `tsc`, `npm run build`, and the `document-review`, `upload`,
  `case-management` and `checklist` specs.
- **Dead code in `UploadPage.tsx`** (deferred): `isExtractionNotConfiguredFailure`, `structuredFieldEntries`,
  `REQUIREMENT_LABELS`, `formatFieldValue`, plus `savingFields`, `saveFieldErrors`, `setFieldDraftValue`,
  `saveFieldDraft`, `isApproving`. The hooks have moved, so these can be removed next.
- **Frontend mirrors backend thresholds** — `CLASSIFICATION_CONFIDENCE_THRESHOLD`, `EXTRACTION_CONFIDENCE_THRESHOLD`
  and the stuck timeouts are hardcoded in `services/uploadService/documentStatus.ts`. A backend change silently
  disagrees with the UI. Serve them from the backend with the case or config payload.
- **Reject wording** — `documents.ts` uses "Reject failed" while the other service messages say "Failed to …".
  Align if wanted.
- **Auto-shortened comments** — comments in the moved files were shortened mechanically. Worth a read-through.

Done recently: lazy-loaded pages and debug panels (main chunk 281 kB → 203 kB), shared `assertOk` helper in the
upload service, reject error shown on the details page, `humanizeFieldKey` moved to `utils/fieldLabels.ts`, shared
edit trigger for array fields, status tone table in `DocumentSummaryCard`, memoised derived lists in both pages.

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

## Local dev reference (LangGraph pipeline)

- Env: `ANTHROPIC_API_KEY` required; `LANGGRAPH_SONNET_MODEL`, `LANGGRAPH_HAIKU_MODEL`,
  `LANGGRAPH_RECURSION_LIMIT` all have sensible defaults in `app/config.py`.
- Debug trace: any document's details page has a "LangGraph trace (debug)" panel — in-memory only
  (`app/langgraph/trace.py`), resets on backend restart, shows which tools the classify agent
  called and why, step-by-step.
- Detection Agent: runs automatically on approval, or manually via the "Run detection"/"Retry
  detection" button on the (still dev-styled) Detection panel on a document's details page, or
  `POST /documents/{id}/detection/run` directly. Report stored on the document's langgraph trace,
  not the `documents` table (see "Detection → approval wiring" above). A crashed run is recorded
  and retryable rather than silently vanishing — see `FRAUD_DETECTION_MAP.md` §2.
- OpenTelemetry: off by default, `OTEL_ENABLED=true` to try locally (prints to console); add
  `GOOGLE_CLOUD_PROJECT=<id>` to export to real Cloud Trace instead (needs
  `roles/cloudtrace.agent`, falls back to console if credentials aren't set up).
- Troubleshooting: missing/invalid `ANTHROPIC_API_KEY` → documents go straight to `failed` with
  the auth error in `failure_reason`. `ModuleNotFoundError: langgraph` → dependencies weren't
  installed in the interpreter the backend actually runs under. A document stuck at
  `processing`/`extracting` far longer than usual → check backend logs; the pipeline's outer
  catch always eventually marks it `failed` rather than leaving it stuck, but a slow Anthropic
  response can still make it feel that way.
