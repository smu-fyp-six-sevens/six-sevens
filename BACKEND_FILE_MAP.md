# Backend File Map

Reference map of source files under `backend/`, scoped to the parts likely to be reorganised for an API gateway (Kong) and Prometheus/Grafana observability. Excludes `backend/doc_gen_scripts/`, `__pycache__`, and — deliberately, not an oversight — `app/langgraph/`, `app/langgraph/detection/`, `app/fraud_detection/`, `app/injection/` and `scripts/`: self-contained subsystems (the LangGraph pipeline, the Detection Agent, the fraud-check library, the dev fraud-injection tooling, and the synthetic-document generator) that a Kong/API-gateway refactor wouldn't fold into anything else — they'd sit behind whichever service exposes them, unchanged internally.

## app/

- **auth.py** — Shared-credential HMAC-signed bearer token auth (`create_token`/`require_auth`) plus short-lived single-use WebSocket ticket minting/consumption for the case-status socket.
- **case_broadcaster.py** — In-memory pub/sub (`CaseBroadcaster`) that fans out case snapshots from the Realtime listener to subscribed WebSocket connections via per-case `asyncio.Queue`s.
- **case_repository.py** — Supabase CRUD for the `cases` table: create/get/list cases and set/clear `submission_finalized`.
- **case_service.py** — Computes case-level rollups: worst-first `documents_status`, required/additional requirement coverage, `sync_submission_finalized`, and `build_case_snapshot`/`build_summary` for the WebSocket and REST case endpoints.
- **config.py** — Centralised `Settings` object reading all configuration (Supabase, auth, thresholds, LangGraph models) from environment variables.
- **document_repository.py** — Supabase CRUD for the `documents` table: create, status updates, classification/extraction/escalation/metadata writes, and status-filtered queries.
- **extracted_fields_repository.py** — Mirrors a document's `extracted_fields` into the normalised one-row-per-field `extracted_fields` table (replace/get, single and batched-by-document-ids).
- **health.py** — `check_db_connection`, a lightweight real Supabase query used by the `/health/db` readiness probe.
- **main.py** — FastAPI app setup: CORS, router registration, lifespan (starts the document queue + Realtime listener), and `/health`, `/health/db`, `/message` endpoints.
- **mapper.py** — Cross-document identity/address matching logic: groups documents into identities (by `nric_number` or name), computes proof-of-address and id-document (front/back) coverage per identity.
- **models.py** — Pydantic models and enums for the whole domain (`DocumentStatus`, `DocumentType`, `CaseRecord`, `DocumentRecord`, `CaseSummary`, etc.) plus `compute_fulfilled_requirements`, the checklist-requirement logic.
- **monitoring.py** — `is_stuck`/`get_stuck_documents`: flags documents idle in a non-terminal status past `STUCK_THRESHOLD_SECONDS`.
- **queue_service.py** — In-memory async `DocumentQueue` with a worker pool; dequeues documents and (on this branch) hands them to the LangGraph pipeline for processing.
- **realtime_listener.py** — Subscribes once per process to Supabase Realtime changes on `documents`/`cases`, debounces bursts per `case_id`, and publishes rebuilt snapshots via `case_broadcaster`.
- **storage_service.py** — Thin wrapper over Supabase Storage: upload/download/delete files and create signed URLs.
- **supabase_client.py** — Builds the shared sync `supabase_client` (HTTP/1.1, publishable key) and an async client factory (secret key) used only by the Realtime listener.
- **telemetry.py** — OpenTelemetry setup (`setup`, `get_tracer`, `record_usage`): off by default, exports to Google Cloud Trace when configured, else console.

## app/routers/

- **auth.py** — `POST /auth/login`: verifies shared credentials and issues a bearer token.
- **cases.py** — `POST/GET /cases`, `GET /cases/{id}`: case creation and summary retrieval.
- **detection.py** — On-demand fraud-detection endpoints: font-consistency and spelling-anomaly checks for a stored document.
- **documents.py** — Document CRUD/actions: get/list, manual extracted-fields correction, manual document-type correction (triggers a LangGraph extraction rerun), retry, langgraph debug trace, and signed download URL.
- **extraction.py** — Dev/QA endpoints: `/extraction/test` (raw ingestion only, bypasses the LangGraph pipeline) and `/extraction/test-spelling-anomaly`.
- **injection.py** — Dev fraud-injection API: list injectable template documents, list a document's fields, and `POST /api/dev/inject` to apply a defect.
- **monitoring.py** — `GET /documents/monitoring/stuck` and `/monitoring/issues`: surfaces stuck documents, failed documents, and recent in-memory queue errors.
- **upload.py** — Single and batch document upload: validates, stores to Supabase Storage, creates the document row, and enqueues it.
- **ws.py** — `POST /ws/tickets` (mints a short-lived WS auth ticket) and the `WS /ws/cases/{case_id}` push-based case-status socket.

## app/ingestion/

- **ingestion_service.py** — `ingest_document`: top-level orchestration choosing native-PDF text extraction, OCR fallback for scanned PDFs, or straight OCR for images, plus a `needs_review` heuristic.
- **metadata_extractor.py** — Runs `exiftool` on a temp copy of the uploaded file to pull embedded file metadata (author, creation date, software, etc.).
- **ocr_extractor.py** — Wraps RapidOCR (`extract_image_with_ocr`) to OCR a PIL/numpy image into per-line text + confidence elements.
- **pdf_extractor.py** — PyMuPDF-based native PDF text/span extraction (`extract_pdf`) and a `pdf_contains_text` check used to decide whether OCR fallback is needed.

## Refactoring analysis — path toward the 5-service split (`CLAUDE.md`)

Mapping current files onto the target services (Ingestion, Per-Document Orchestrator, Case
Gate/Orchestrator, Aggregation, Query), and what's blocking each from being genuinely
independent today.

**1. Ingestion Service** (upload → storage → row → publish `document.uploaded`)
- Files: `routers/upload.py`, `storage_service.py`, `ingestion/` (all 4 files).
- Blocker: `upload.py` calls `document_repository.create` and `queue_service.enqueue`
  directly, in-process. Splitting this out means it only writes the row + storage object,
  then publishes an event — it must stop knowing `queue_service` exists at all.

**2. Per-Document Orchestrator** (classify → extract → escalation loop → analyzed/failed)
- Files: `queue_service.py`, `document_repository.py`,
  `extracted_fields_repository.py`, plus `app/langgraph/` (the actual classify/extract graph,
  including `extraction_prompts.py` — out of scope for this map, see the note at the top, but
  it's what this service would run).
  Detection work happens through `app/langgraph/detection/` and `app/fraud_detection/`, both
  likewise out of scope here.
- Blocker: `queue_service.py` is an in-memory `asyncio.Queue` — inherently single-process.
  For this service to be independently deployable it needs a real subscription
  (`document.uploaded` in, `document.analyzed`/`document.classified` out) in place of the
  in-memory dequeue loop. This is the one piece with no shortcut — it's structural, not a
  file move.

**3. Case Gate/Orchestrator** (tracks completion, triggers per-case pipeline)
- Files: `case_repository.py`, `case_service.py`.
- Blocker: `case_service.sync_submission_finalized` is called synchronously, inline, from
  inside `app/langgraph/nodes.py` and from `routers/documents.py` — i.e. the Per-Document
  Orchestrator currently reaches into the Case Gate's logic directly. Splitting
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
- `routers/injection.py` (+ `app/injection/`, out of scope here) — dev-only fraud-injection
  tooling for generating test cases, not part of the runtime pipeline.
- `routers/extraction.py` — dev/QA test endpoints, same category.

**Biggest structural blocker overall:** nothing above is really "file organization" — every
service boundary today is crossed by a direct Python function call (`case_service.sync_submission_finalized(...)`,
`document_repository.get_document(...)`, `queue_service.enqueue(...)`) rather than an event or
API call. Reorganizing files into service-shaped folders is cosmetic until those specific
call sites are replaced with publish/subscribe or HTTP boundaries — that replacement, not the
file move, is the actual work of the eventual split.

## Loosely-coupled architecture — recommendations independent of any service split

These apply regardless of whether the app ever physically splits into separate services —
they're just good in-process boundaries.

**1. Component = table owner + a service layer, not just a repository.**
Right now routers and orchestrator code call `document_repository`/`case_service`/
`extracted_fields_repository` directly, from wherever. Instead, each domain (documents,
cases, extracted_fields) should get one module that's the *only* thing allowed to import its
repository — everything else calls that module's functions, never the repository directly.
Same idea as a service boundary, just enforced in-process now rather than across a network.

**2. Replace direct cross-component calls with an internal event bus.** *(Deprioritised —
revisit once the bulk of the app is built, not now.)*
The concrete coupling example: `case_service.sync_submission_finalized(...)` is called inline
from `app/langgraph/nodes.py` and `routers/documents.py`. Instead of every
caller knowing "finishing a document also means updating case status," the document
component would just do `event_bus.publish("document.analyzed", document_id)`, and the case
component subscribes and reacts independently. In-process this can be a small `asyncio`-based
dispatcher (dict of event name → handler coroutines) — no new infrastructure — but it means
the communication shape (named events + payloads) is already correct if a real message broker
is ever introduced later, since only the transport underneath would change.

## Implications for Kong (API gateway) + Prometheus + Grafana

This is the actual motivation for the component/service-layer cleanup above: Kong needs real
service boundaries to route to, and Prometheus/Grafana need a metrics surface on each of those
boundaries. Neither is a reason to add more files — they're a reason the boundary work above
has a concrete payoff instead of being cleanup for its own sake.

**Kong sits in front of the same boundaries as the refactoring analysis, not new ones.**
Whatever comes out of splitting `routers/` into the 5 service-shaped groups above (or even just
into in-process modules with the "component = table owner" boundary) is what Kong would route
to — `/documents/*` and `/cases/*` upload/write paths to the Ingestion/Per-Document
Orchestrator, `GET /cases/*` and `GET /documents/*` read paths to the Query Service, and so on.
Kong doesn't need those to be separately *deployed* to add value immediately — it can sit in
front of the single FastAPI app today and still centralise:
- **Auth**, replacing `auth.py`'s shared-credential bearer check with Kong's own key-auth/JWT
  plugin at the gateway — FastAPI's `require_auth` dependency becomes redundant once Kong is
  the one thing that can reach the backend at all.
- **Rate limiting and quotas** per API key/consumer, which nothing in this codebase does today
  (`QUEUE_WORKER_CONCURRENCY`-style limits are Anthropic-call-specific, not request-level).
- **Routing**, once there's more than one deployable service to route between — today's
  single-FastAPI-process reality means this part is mostly future-facing.

**Prometheus needs two different things, not one.**
- **Gateway-level metrics** (request count, latency, status code, per-route) come from Kong's
  own Prometheus plugin — no backend code needed for these at all.
- **App-level metrics** (LLM token usage/cost per document, classify/extract latency, queue
  depth, stuck-document count) need a `/metrics` endpoint added to the FastAPI app
  (`prometheus_client`, alongside the existing `/health`/`/health/db` in `main.py`). This is a
  different signal from the OpenTelemetry traces already wired (`app/telemetry.py`): traces are
  for following one document's run end-to-end, metrics are for aggregate rates/percentiles over
  time — both are worth having, and they don't replace each other. `monitoring.py`'s
  `is_stuck`/`get_stuck_documents` (currently only polled via `GET /monitoring/issues`) is the
  most natural first Prometheus gauge, since the data already exists.

**Grafana** then dashboards both sources — Kong's gateway metrics and the app's own
`/metrics` — and, since Cloud Trace can be added as a Grafana data source, can also surface the
OTel traces next to them instead of only being reachable via the GCP console.
</content>
