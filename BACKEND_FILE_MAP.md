# Backend File Map

Reference map of every source file under `backend/` (excluding `backend/doc_gen_scripts/` and `__pycache__`).

## app/

- **auth.py** — Shared-credential HMAC-signed bearer token auth (`create_token`/`require_auth`) plus short-lived single-use WebSocket ticket minting/consumption for the case-status socket.
- **case_broadcaster.py** — In-memory pub/sub (`CaseBroadcaster`) that fans out case snapshots from the Realtime listener to subscribed WebSocket connections via per-case `asyncio.Queue`s.
- **case_repository.py** — Supabase CRUD for the `cases` table: create/get/list cases and set/clear `submission_finalized`.
- **case_status.py** — Computes case-level rollups: worst-first `documents_status`, required/additional requirement coverage, `sync_submission_finalized`, and `build_case_snapshot`/`build_summary` for the WebSocket and REST case endpoints.
- **config.py** — Centralised `Settings` object reading all configuration (Supabase, Dify, auth, thresholds, LangGraph models) from environment variables.
- **dify_client.py** — Async HTTP client (`DifyClient`) for Dify's `/workflows/run` and `/chat-messages` APIs, with retry-with-backoff wrappers and a concurrency semaphore for the Classifier chatflow.
- **document_repository.py** — Supabase CRUD for the `documents` table: create, status updates, classification/extraction/escalation/metadata writes, and status-filtered queries.
- **extracted_fields_repository.py** — Mirrors a document's `extracted_fields` into the normalised one-row-per-field `extracted_fields` table (replace/get, single and batched-by-document-ids).
- **extraction_prompts.py** — Per-document-type extraction schemas (required/additional/optional field tiers), extra field-specific guidance, and `build_extraction_prompt`/`schema_field_names` used by both the Dify and LangGraph extraction paths.
- **health.py** — `check_db_connection`, a lightweight real Supabase query used by the `/health/db` readiness probe.
- **main.py** — FastAPI app setup: CORS, router registration, lifespan (starts the document queue + Realtime listener), and `/health`, `/health/db`, `/message` endpoints.
- **mapper.py** — Cross-document identity/address matching logic: groups documents into identities (by `nric_number` or name), computes proof-of-address and id-document (front/back) coverage per identity.
- **models.py** — Pydantic models and enums for the whole domain (`DocumentStatus`, `DocumentType`, `CaseRecord`, `DocumentRecord`, `CaseSummary`, etc.) plus `compute_fulfilled_requirements`, the checklist-requirement logic.
- **monitoring.py** — `is_stuck`/`get_stuck_documents`: flags documents idle in a non-terminal status past `STUCK_THRESHOLD_SECONDS`.
- **orchestrator.py** — The Dify-based pipeline: runs Classifier (Intake Coordinator) → Extraction with a bounded reclassification escalation loop, persists results, and always leaves a document at `ANALYZED`/`FAILED`.
- **queue_service.py** — In-memory async `DocumentQueue` with a worker pool; dequeues documents and (on this branch) hands them to the LangGraph pipeline for processing.
- **realtime_listener.py** — Subscribes once per process to Supabase Realtime changes on `documents`/`cases`, debounces bursts per `case_id`, and publishes rebuilt snapshots via `case_broadcaster`.
- **storage_service.py** — Thin wrapper over Supabase Storage: upload/download/delete files and create signed URLs.
- **supabase_client.py** — Builds the shared sync `supabase_client` (HTTP/1.1, publishable key) and an async client factory (secret key) used only by the Realtime listener.

## app/routers/

- **auth.py** — `POST /auth/login`: verifies shared credentials and issues a bearer token.
- **cases.py** — `POST/GET /cases`, `GET /cases/{id}`: case creation and summary retrieval.
- **detection.py** — On-demand fraud-detection endpoints: font-consistency v1/v2 and spelling-anomaly checks for a stored document.
- **documents.py** — Document CRUD/actions: get/list, manual extracted-fields correction, manual document-type correction (triggers a LangGraph extraction rerun), retry, langgraph debug trace, and signed download URL.
- **extraction.py** — Dev/QA endpoints: `/extraction/test` (raw ingestion only, bypasses Dify) and `/extraction/test-spelling-anomaly`.
- **injection.py** — Dev fraud-injection API: list injectable template documents, list a document's fields, and `POST /api/dev/inject` to apply a defect.
- **monitoring.py** — `GET /documents/monitoring/stuck` and `/monitoring/issues`: surfaces stuck documents, failed documents, and recent in-memory queue errors.
- **upload.py** — Single and batch document upload: validates, stores to Supabase Storage, creates the document row, and enqueues it.
- **ws.py** — `POST /ws/tickets` (mints a short-lived WS auth ticket) and the `WS /ws/cases/{case_id}` push-based case-status socket.

## app/langgraph/

- **__init__.py** — Package docstring only; marks this as the in-progress LangGraph experiment not yet wired into production routing.
- **cache_gate.py** — `PromptCacheGate`: lets only the first concurrent caller for a given static-prompt key through immediately, so parallel documents don't all miss Anthropic's prompt cache at once.
- **classify_graph.py** — The classify subgraph: deterministic `rule_classify` → Sonnet agent (offered `llm_classify_tool` first, then `vision_llm`) → validate → repair, plus the reclassify re-entry point.
- **classify_support.py** — Shared classify logic: `validate_agent_output` (ported from the old Dify "Validate Output" node), `address_check`, and the agent's static/dynamic system prompt builder.
- **extract.py** — One-shot Sonnet extraction call (`run_extraction`) reusing `extraction_prompts.build_extraction_prompt` and `orchestrator._parse_extraction_output`, with prompt-cache splitting.
- **graph.py** — The full per-document graph: ingest → classify → finalise → extract → analyzed/failed, including the bounded classify↔extract reclassify loop.
- **llm_classify.py** — `llm_classify_tool`: a Haiku-based independent text classification pass returning top-3 ranked candidates (port of the old Dify LLM classification node).
- **nodes.py** — Deterministic graph nodes (ingest, finalise_classification, extract, mark_analyzed/failed, record_escalation) that delegate to the shared `orchestrator`/repository helpers.
- **pipeline.py** — Public entry points (`process_document`, `rerun_extraction`) mirroring `orchestrator`'s contract, invoking the compiled LangGraph document graph.
- **rule_classify.py** — Pure regex/keyword classifier (`classify_by_rules`) ported from the old Dify rule-based node, plus a LangGraph-only "certain type" fast-path and residential-address heuristics.
- **state.py** — `DocumentGraphState` TypedDict: the small, PII-free graph state (ids, hashes, classification/extraction summaries, attempt counters).
- **text_cache.py** — Process-local, run-scoped cache mapping `document_id` → ingested raw text, since graph state itself never carries raw text.
- **trace.py** — Dev-only in-memory per-document trace of which route/tools/models the LangGraph pipeline used, exposed via `/documents/{id}/langgraph-trace`.
- **vision_tool.py** — `vision_llm` tool: sends the actual document image/PDF to Sonnet for vision-based classification when text-only signals are weak or ambiguous.

## app/fraud_detection/

- **domain_terms.py** — Seed allow-list of legal/financial/KYC terms and acronyms so `spelling_anomaly.py` doesn't flag legitimate domain vocabulary.
- **font_consistency.py** — v1 font-consistency check: extracts PDF text spans, compares each span against its line's and region's dominant font/size/emphasis pattern, and reports scored/grouped anomalies.
- **font_consistency_v2.py** — v2 font-consistency check: tighter row-level comparison plus field-anchored comparison (locates each extracted field's value in the PDF and checks its rendering against the document baseline).
- **spelling_anomaly.py** — SymSpell-based spelling-anomaly detector: tokenizes extracted text, filters known domain terms and extracted-field values, and suppresses likely proper nouns to avoid LLM calls.

## app/ingestion/

- **ingestion_service.py** — `ingest_document`: top-level orchestration choosing native-PDF text extraction, OCR fallback for scanned PDFs, or straight OCR for images, plus a `needs_review` heuristic.
- **metadata_extractor.py** — Runs `exiftool` on a temp copy of the uploaded file to pull embedded file metadata (author, creation date, software, etc.).
- **ocr_extractor.py** — Wraps RapidOCR (`extract_image_with_ocr`) to OCR a PIL/numpy image into per-line text + confidence elements.
- **pdf_extractor.py** — PyMuPDF-based native PDF text/span extraction (`extract_pdf`) and a `pdf_contains_text` check used to decide whether OCR fallback is needed.

## app/injection/

- **__init__.py** — Empty; marks `app.injection` as a package.
- **field_text.py** — Splits a recognised text field into `(label_prefix, value)` (e.g. "Account No. " + "XXXX-0472") so injectors can replace just the value while preserving printed labels.
- **injectors.py** — One `DefectInjector` subclass per `DefectType` (font, font_size, numerical, metadata, spelling, misalignment, emphasis, text_overlay, image_manipulation) implementing the actual defect mutation.
- **pdf.py** — Large low-level module for PDF/image field discovery (native PDF spans, PDF embedded images, OCR-discovered fields on raster images) and pixel/vector-level defect rendering (font swap, text splice, emphasis, overlay, image manipulation, metadata read/write).
- **schemas.py** — Pydantic models for the injection API: `DefectType`, `FieldValue`, `InjectableDocument`, `InjectDefectRequest`/`Response`, with per-defect-type required-field validation.
- **service.py** — `InjectionService.inject_defect`: looks up the template document, stages a working copy, delegates to the matching injector, and asserts only the expected fields changed.
- **store.py** — In-memory registry of injectable template/generated documents (`DOCUMENTS`/`TEMPLATES`), loaded from `document_templates/`, with lookup/registration/staging helpers.

## scripts/

- **_inspect_mastersheet.py** — Ad-hoc dev script that dumps every sheet name, shape, columns, and sample rows of the v9 mastersheet xlsx.
- **generate_from_mastersheet.py** — Main synthetic-document generator: reads the mastersheet workbook and, per case ID, calls each document type's generator (bank statements, IDs, payslips, COI, proof of address, incumbency, constitution, FATCA/CRS, loans, NOA, optional docs), then normalises output file extensions.
- **regenerate_selected.py** — Regenerates only a fixed subset of document types (passport, constitution, COI, incumbency, source-of-wealth, NOA) across all/selected cases without touching the rest.
- **run_batch.py** — CLI to generate documents for a case range or explicit list, optionally parallelised across worker processes via `ProcessPoolExecutor`.
- **verify_documents.py** — Cross-checks the mastersheet's document-producing rows against `documents_generated/<case_id>/` to report missing/extra files per case.

## Refactoring analysis — path toward the 5-service split (`CLAUDE.md`)

Mapping current files onto the target services (Ingestion, Per-Document Orchestrator, Case
Gate/Orchestrator, Aggregation, Query), and what's blocking each from being genuinely
independent today. Dify-vs-LangGraph duplication inside "Per-Document Orchestrator" is
intentionally not called out below — that's a separate, later decision.

**1. Ingestion Service** (upload → storage → row → publish `document.uploaded`)
- Files: `routers/upload.py`, `storage_service.py`, `ingestion/` (all 4 files).
- Blocker: `upload.py` calls `document_repository.create` and `queue_service.enqueue`
  directly, in-process. Splitting this out means it only writes the row + storage object,
  then publishes an event — it must stop knowing `queue_service` exists at all.

**2. Per-Document Orchestrator** (classify → extract → escalation loop → analyzed/failed)
- Files: `queue_service.py`, `orchestrator.py`, `langgraph/` (all 14 files), `dify_client.py`,
  `extraction_prompts.py`, `document_repository.py`, `extracted_fields_repository.py`.
  Future home of the Detection Agent (`fraud_detection/*`, currently only reachable via the
  dev-only `routers/detection.py`).
- Blocker: `queue_service.py` is an in-memory `asyncio.Queue` — inherently single-process.
  For this service to be independently deployable it needs a real subscription
  (`document.uploaded` in, `document.analyzed`/`document.classified` out) in place of the
  in-memory dequeue loop. This is the one piece with no shortcut — it's structural, not a
  file move.

**3. Case Gate/Orchestrator** (tracks completion, triggers per-case pipeline)
- Files: `case_repository.py`, `case_status.py`.
- Blocker: `case_status.sync_submission_finalized` is called synchronously, inline, from
  inside `orchestrator.py`/`langgraph/nodes.py` and from `routers/documents.py` — i.e. the
  Per-Document Orchestrator currently reaches into the Case Gate's logic directly. Splitting
  requires this to become "per-document work finishes → publish an event" with the Case Gate
  reacting to it independently, not being called as a function.

**4. Aggregation Service** (not built yet)
- No dedicated file exists yet. `mapper.py` (cross-document identity/address matching) is the
  closest existing logic to what this service will need, since it already reasons across a
  case's full document set rather than one document at a time — likely relocates here once
  the Output Aggregator is built, rather than staying a helper `orchestrator`/routers reach
  into directly.

**5. Query Service** (read-only dashboard + live push, CQRS split from the write path)
- Files: `routers/cases.py`, the read paths of `routers/documents.py`, `case_broadcaster.py`,
  `realtime_listener.py`, `routers/ws.py`.
- Blocker: today these run in the same process as everything else — `main.py`'s lifespan
  starts both the document queue *and* the Realtime listener together. Also, `routers/documents.py`'s
  GET handlers currently call `document_repository` directly; under a real split that's Query
  Service reaching into Per-Document Orchestrator's data layer, which should become an API
  call (or a dedicated read-replica/materialised view) instead.

**Shared across all services (needs to become a small shared library, not copy-pasted)**
- `models.py` — becomes the event/contract schema layer between services, not just internal
  Pydantic models.
- `auth.py` — token verification needs to be callable by every service independently.
- `config.py`, `supabase_client.py` — each service gets its own slice of config/client init
  rather than one shared `Settings` object for the whole monolith.
- `health.py`, `monitoring.py` — become per-service health/stuck-document checks instead of
  one global view.

**Stays internal tooling, not a customer-facing service**
- `routers/injection.py` + `injection/` (all 6 files) — dev-only fraud-injection tooling for
  generating test cases, not part of the runtime pipeline.
- `routers/extraction.py` — dev/QA test endpoints, same category.

**Biggest structural blocker overall:** nothing above is really "file organization" — every
service boundary today is crossed by a direct Python function call (`case_status.sync_submission_finalized(...)`,
`document_repository.get_document(...)`, `queue_service.enqueue(...)`) rather than an event or
API call. Reorganizing files into service-shaped folders is cosmetic until those specific
call sites are replaced with publish/subscribe or HTTP boundaries — that replacement, not the
file move, is the actual work of the eventual split.

## Loosely-coupled architecture — recommendations independent of any service split

These apply regardless of whether the app ever physically splits into separate services —
they're just good in-process boundaries.

**1. Component = table owner + a service layer, not just a repository.**
Right now routers and orchestrator code call `document_repository`/`case_status`/
`extracted_fields_repository` directly, from wherever. Instead, each domain (documents,
cases, extracted_fields) should get one module that's the *only* thing allowed to import its
repository — everything else calls that module's functions, never the repository directly.
Same idea as a service boundary, just enforced in-process now rather than across a network.

**2. Replace direct cross-component calls with an internal event bus.** *(Deprioritised —
revisit once the bulk of the app is built, not now.)*
The concrete coupling example: `case_status.sync_submission_finalized(...)` is called inline
from `orchestrator.py`, `langgraph/nodes.py`, and `routers/documents.py`. Instead of every
caller knowing "finishing a document also means updating case status," the document
component would just do `event_bus.publish("document.analyzed", document_id)`, and the case
component subscribes and reacts independently. In-process this can be a small `asyncio`-based
dispatcher (dict of event name → handler coroutines) — no new infrastructure — but it means
the communication shape (named events + payloads) is already correct if a real message broker
is ever introduced later, since only the transport underneath would change.
</content>
