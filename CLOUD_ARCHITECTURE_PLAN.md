# Cloud architecture plan

Proposed 2026-10-20, updated 2026-10-21. **Status: Stage 1 is built in code on the `microservices` branch (happy path; section 10 has what is done and what is not). Terraform has not been applied to GCP yet.** Replaces the five-service split in `CLAUDE.md` ("Architecture: Decoupling Requirement") and its "Kubernetes + ArgoCD rejected" line. Deadline: mid-November 2026.

The demo is done and nothing uses the cloud deployment (every developer works locally), so the cloud environment is free to experiment on.

One GCP project and one Supabase project, shared by local runs and the cloud. No separate staging environment (section 6, "Deploy and roll back").

Two stages, one design:
- **Stage 1 (by mid-November): Cloud Run.** Everything in this plan, on Cloud Run.
- **Stage 2 (after): GKE.** Same services, events and images; the runtime changes (section 6). Stage 1 is built so that move is configuration, not a rewrite.

## 1. Goal and rules

Split the single FastAPI process into independently deployable services, so the Cloud Native award requirement ("genuinely decoupled, not a monolith") is met and each workload scales and fails on its own.

- Services talk **only** through Pub/Sub events and Supabase state. No service calls another directly.
- One repo, one Dockerfile with **a build target per role**, so an image holds only what its role needs.
- Split only where scaling, failure behaviour or permissions differ. Do not split finer than a run: the specialists in a detection run share one fetch of the document and one agent loop, and classify and extract share the OCR text and a reclassify loop.
- External validation (registry and address lookups) stays inside detection. It is not a service.
- **Portable by construction:** stateless services, configuration from environment variables, no reliance on Cloud Run specifics, so Stage 2 does not touch the code (section 5).

What this should show an assessor (our reading of cloud-native criteria; confirm with the supervisor, open decision 3):

| Criterion | Where it is shown |
|---|---|
| Loose coupling | Services connected only by events (sections 2-3) |
| Independent scaling | Each role scales on its own signal (section 2, 6) |
| Resilience | Redelivery, dead-letter topic, idempotent handlers, failure tests (sections 3, 12) |
| Observability | `/metrics`, Prometheus, Grafana, traces, the audit trail (sections 8-9) |
| Automated, secure delivery | CI/CD with Snyk and Checkov, Terraform, no long-lived keys (section 6) |
| Least privilege | A service account per service, Secret Manager, per-service secrets (section 6) |
| Portability | Containers per role, Cloud Run now and GKE next (section 6) |
| Cost control | Scale to zero where possible, budget alerts (section 6) |

## 2. Services

| Service | Public | Subscribes to | Does | Scales on | Size |
|---|---|---|---|---|---|
| **api** | Via Kong | none | Reads, auth, officer commands (publish events), WebSocket and live pushes, case match | Requests, open sockets | Small, min 1 instance |
| **ingestion** | Via Kong | none | Upload, validation, storage write, row creation | Upload bursts | Memory for file bytes |
| **doc-worker** | No | `document.uploaded`, `document.retyped` | OCR, classify, extract, reclassify loop | Pending messages | 2 CPU, 2-4 GB, 2 messages per instance |
| **detection-worker** | No | `document.approved`, detection cancel and retry | Lead, 3 specialists, 10 checks (incl. external validation), flags | Pending messages | Largest: 2+ CPU, 4 GB, `exiftool` in the image |
| **case-worker** | No | `document.detected`, `case.analysis.requested` | Readiness gate, cross-document analysis, deterministic risk score, summary | Pending messages (low volume) | 1 CPU, long timeout |
| **audit-metrics** | Via Kong (read) | every event | Stores every event; serves the audit trail, stage timings and the case dashboard | Event rate, reads | Small |

Also:
- **Kong** is the single entry point (section 7), a service of its own in front of api, ingestion and audit-metrics only.
- **Frontend:** a static build served by nginx in a container (as today), its `VITE_API_URL` pointing at Kong. A Cloud Storage bucket behind a CDN is a later optimisation and needs a load balancer, so it is not part of Stage 1.
- **Eval job:** a nightly Cloud Run Job (a Kubernetes CronJob in Stage 2). Not a service.
- **Sweeper job:** a Cloud Run Job run every 2 minutes by Cloud Scheduler (a CronJob in Stage 2). Finds stuck work and asks its owner to recover it (section 16). Reads only; writes nothing but events.
- **Dev injection tool:** `routers/injection.py` is mounted in the production API today. It leaves every image and is mounted only in the in-process (uvicorn) mode.
- **Case match** stays in api. It is computed on read (snapshots and the case-match endpoints) and writes nothing, so it reruns whenever a snapshot is rebuilt after extraction.
- **Ingestion** can run inside api first (same code, one role switch) and be split out last.
- **Live updates** (Realtime listener, broadcaster, WebSocket) live in api. If socket load ever needs its own scaling, it becomes a role of its own.

```
 officer ─► Kong ─┬─► api ───────────┐ commands publish events
                  ├─► ingestion ─────┤
                  └─► audit-metrics  │
                         ▲           ▼
                 every event     Pub/Sub  domain-events (+ dead-letter)
                         │      filter by type
                         │   ┌────────┬───────────────┬─────────────┐
                         │   ▼        ▼               ▼             │
                         │ doc-worker detection-worker case-worker  │
                         └────────── publish their own events ──────┘
        all services read and write Supabase (state of record)
        all services expose /metrics ─► Managed Prometheus ─► Grafana
```

## 3. Events

One topic, `domain-events`, with a dead-letter topic. Each subscription filters on the `type` attribute. A message carries ids only, never document content; Supabase holds the state.

Envelope (it is also the `audit_events` row):

```
{ event_id, type, occurred_at, actor_type: user|agent|system, actor,
  case_id, document_id, outcome: success|failed|cancelled,
  started_at, duration_ms, detail }
```

| Type | Published by | Consumed by |
|---|---|---|
| `document.uploaded` | ingestion | doc-worker |
| `document.retyped` | api | doc-worker |
| `document.analyzed`, `document.failed` | doc-worker | audit-metrics |
| `document.approved` | api | detection-worker |
| `document.rejected` | api | audit-metrics |
| `detection.cancel_requested`, `detection.retry_requested` | api | detection-worker |
| `document.detected` | detection-worker | case-worker |
| `case.analysis.requested` | api | case-worker |
| `case.analysis.completed` | case-worker | audit-metrics |
| `case.created / renamed / deleted`, `document.deleted / moved`, `document.field_edited`, `document.type_corrected`, `auth.login` | api (and ingestion for uploads) | audit-metrics |
| `recovery.requested` (`detail.stage`, `detail.attempt`) | sweeper | the worker owning that stage |
| `document.recovered`, `recovery.exhausted` | the owning worker | audit-metrics |

Rules:
- At-least-once delivery, so every handler must tolerate a repeat. Status changes are compare-and-swap (`expected_current_statuses`), which the code already has.
- A handler acks and ignores a type it does not handle, so a missing subscription filter (the local emulator) never breaks it.
- A human action that is also a trigger (approve, retype, request analysis) fails with a 502 if the publish fails. The event is the action.
- Staleness of a case analysis stays computed on read (fingerprint), not an event.
- Kong's request id is copied into the event's `detail`, so one request can be followed through the audit trail.

## 4. State ownership (one writer per transition)

| Data | Writer |
|---|---|
| `documents`: create, `queued` | ingestion |
| `documents`: `queued` to `analyzed` / `failed` / `cancelled`; `extracted_fields` from extraction | doc-worker |
| `documents`: `analyzed` to `approved` / `rejected`; officer field edits, type corrections, moves, deletes; `cases` | api |
| `documents`: `approved` to `detecting` to `detected`; `flags`; detection part of `langgraph_trace` | detection-worker |
| `case_analysis_runs`, `risk_scores`, `cases.submission_finalized` | case-worker |
| `audit_events` | audit-metrics only (service role: insert and select) |
| Recovery of a stuck stage (status reset, retry count, `failed`) | the worker owning that stage; the sweeper only publishes |

The classify and extract trace and the detection trace share one `langgraph_trace` row per document. The stages run one after another per document, so writes do not overlap; confirm when splitting.

## 5. What has to change in the code

| Today | Change |
|---|---|
| In-memory `DocumentQueue` | Pub/Sub: push to doc-worker on Cloud Run |
| Detection cancel: `_detection_tasks` dict and `task.cancel()` | `cancel_requested` flag in the database, checked by the worker between graph steps |
| "Analysis already running": `state.running_tasks` dict | A `running` row with a heartbeat |
| Workers push snapshots through the in-process broadcaster | Add `langgraph_trace` and `case_analysis_runs` to the Realtime publication; each api instance pushes on change |
| `submission_finalized` debounce: per-process timers | Moves to the case-worker, event driven |
| Anthropic limit: per-process semaphores | A budget of max instances times concurrency per worker (or a Cloud Tasks concurrency cap); a shared limiter later (`PENDING_WORK.md`). Redis is not needed for this |
| Stuck documents: computed on read for the "Processing stuck" badge and manual retry; nothing acts on them, `detecting` is not covered | The sweeper job and owner-side recovery (section 16) |
| `routers/injection.py` in the API | Mounted only when `EVENT_TRANSPORT=inprocess`; not copied into any image |
| Calls such as `queue.enqueue` and `sync_submission_finalized` | `events.publish()`. `EVENT_TRANSPORT=inprocess` (the default) calls the handlers in the same process, so `uvicorn` + `npm run dev` and the test suite stay as they are; `pubsub` publishes to Pub/Sub (or the emulator when `PUBSUB_EMULATOR_HOST` is set) |
| One process holds every role | `ROLE` selects what a process mounts: `all` (default, uvicorn), one role (cloud), or a comma list such as `doc,detection,case` (local compose, to save memory) |
| `cases.dev` flag and `IS_CLOUD_DEPLOYMENT` filtering (UAT only) | Removed from the code; the column can be dropped later. Local and cloud cases share one list |
| Token and cost counts only logged (`query_stats.py`); no `/metrics` | `prometheus_client` in every service (section 8) |
| Work arrives through one transport | **Handler plus adapters:** each worker is a `handle(event)` function with two thin adapters, an HTTP push endpoint (Stage 1) and a pull subscriber (Stage 2) |
| No health endpoints, no shutdown handling | `/healthz` for liveness and readiness; on SIGTERM finish the current run or hand it back. Cloud Run benefits now; Kubernetes requires both |

## 6. Platform and delivery

**Runtime, Stage 1 and Stage 2**

| Concern | Stage 1: Cloud Run | Stage 2: GKE (Autopilot) |
|---|---|---|
| A role | Cloud Run service | Deployment and Service per role |
| Scaling | Cloud Run autoscaling; min instances | HPA, or KEDA on Pub/Sub backlog (KEDA also gives scale to zero) |
| Event delivery | Pub/Sub push with OIDC to private services; ack deadline 600 s (the maximum), Cloud Run request timeout 600 s; work happens inside the push request, never in a background task (no CPU outside a request) | Workers pull and extend the lease, so long detection runs are no problem |
| Gateway | Kong as a Cloud Run service (`Host` header gotcha, section 7) | Kong ingress controller, routes as Kubernetes resources |
| Metrics | Managed Prometheus sidecar | Managed Prometheus scraping the pods |
| Deploy | `gcloud run deploy` from a GitHub Actions matrix | Helm chart (one chart, a values file per role) deployed by Argo CD |
| Secrets | Secret Manager references | External Secrets or the CSI driver |
| Identity | One service account per service | Workload Identity: one Kubernetes service account per role |
| WebSocket | 60-minute connection limit (client reconnects) | No such limit |
| Chaos tests | Script: kill instances, flood uploads | Litmus: pod failure, load test against the autoscaler |

Unchanged between stages: Supabase, Artifact Registry, the events and ownership, the Dockerfile target per role, Terraform (which gains the cluster module).

**Technology decisions**

| Item | Decision |
|---|---|
| Redis | Not needed. Pub/Sub carries messages, Supabase holds state and locks, each api instance listens to Realtime, uniqueness is a database constraint. Revisit only for a measured need (most likely a global Anthropic limiter); Memorystore costs money and needs a VPC connection from Cloud Run |
| Firestore, Cloud Storage for documents | No. Supabase Postgres and Supabase Storage (decisions in `CLAUDE.md`) |
| Cloud DNS, CDN, load balancer | Optional. Only for a custom domain or the static-site CDN option; Kong is the API entry point |
| Artifact Registry | Yes, the only image registry. `ci-cd.yml` currently also publishes to GitHub's registry and `deploy.yml` builds separately; unify to one build per role |
| Supabase Auth | Not in November. The interim login is `AUTH_USERS` (documented limitation). Later, Supabase Auth JWTs can be verified at Kong and the audit actor read from the token (open decision 6) |
| Stripe, OpenAI | Not relevant (no payments; we use Anthropic) |
| OutSystems | Likely the bank's front-end platform; treat as an outside consumer of our API. Not built; confirm with the sponsor |
| Region | `asia-southeast1` (already the deploy default); check Supabase is in the same region |

**Delivery pipeline** (GitHub Actions)
- **Build and test:** ruff, pyright and its suppression check, pytest, Playwright (as today).
- **Security:** Snyk on dependencies (Python and npm) and on each image, failing on high severity; Checkov on Terraform, Dockerfiles, workflows and, in Stage 2, the Helm charts. Public Cloud Run services will trigger Checkov's unauthenticated-access finding; suppress it with a written reason. Checking needed: Snyk's handling of our `pyproject.toml` dependencies may require an exported requirements file; `pip-audit`, `npm audit` and Trivy are account-free fallbacks.
- **Build:** one image per role from the Dockerfile targets, pushed once to Artifact Registry.
- **Deploy:** a reusable workflow called with a matrix over the roles (Stage 1); Argo CD syncing the Helm chart (Stage 2). Authenticate with Workload Identity Federation, replacing the long-lived `GCP_SA_KEY`.
- **Tests never call Anthropic or Supabase.** Both are stubbed (the existing `conftest.py` already refuses real calls); see section 17.

**Deploy and roll back (one project, no staging)**

The cloud environment is the only one, so a deploy is checked and undone automatically if it fails (`.github/workflows/deploy.yml`):
1. Build one image per role, Kong, and the frontend (pointed at Kong's URL); push them to Artifact Registry. An image the commit already has is skipped; otherwise unchanged layers come from a registry build cache. Each job mints one Google access token at its start (Workload Identity Federation) and gives it to gcloud and Docker.
2. Record the revision each service is serving.
3. Roll out the new images, workers and audit-metrics first, then api, Kong and the frontend (`gcloud run services update --image`, then `update-traffic --to-latest`, since an earlier rollback leaves traffic pinned; Terraform owns everything else about a service).
4. Check through Kong, with no LLM and no database writes: `/health` answers (not `/healthz`: Cloud Run's front end reserves paths ending in `z` and returns its own 404; `/healthz` stays for Kubernetes in Stage 2), an authenticated route answers 401 (never 404 or 502), `/push` and `/metrics` are blocked at the gateway, a worker refuses a POST without Pub/Sub's identity (403), CORS answers the frontend's origin, and the frontend serves.
5. Any failure: every service goes back to its recorded revision (`gcloud run services update-traffic --to-revisions=<recorded>=100`) and the workflow fails.

Tagged no-traffic revisions were dropped for now: nothing depends on the cloud deployment, and they would need Pub/Sub and Kong to target the tag URLs.
- **Terraform:** modules plus a separate `monitoring.tf`; the budget alert lives here too.

**Security and identity**
- One service account per service with only the permissions it needs; only the Pub/Sub service account may invoke the workers.
- Secrets in Secret Manager and mounted per role, not plain environment variables set from GitHub secrets: Anthropic key in the three workers only; token-signing key in the public services; Supabase key in all. The database cannot be partitioned further (one key), so those, plus which services are public, are the boundaries we can enforce.

**Cost**
- Scale to zero for workers where cold starts allow; min-instances 1 for api, Kong, and (on demo day) doc-worker and detection-worker, since OCR and LangGraph imports are slow.
- A budget alert at 50% and 90%, since LLM calls and always-on instances are what cost money.
- Stage 2 adds a cluster fee (Autopilot has a free zonal tier) and the operations time for Helm and Argo CD.

**Local:** unchanged `uvicorn` + `npm run dev` by default (in-process events, every role in one process); an opt-in compose stack with the Pub/Sub emulator and Kong for checking the split. See section 18.

## 7. Gateway (Kong)

Kong runs as a Cloud Run service in DB-less mode (`deploy/kong/`). Its routes are rendered at container start from `kong.yml.tmpl` by `render.sh` (sed) with the two upstream URLs, `preserve_host` and the CORS origins, so local compose (`http://api:8000`) and Cloud Run (`https://api-<number>.<region>.run.app`, `preserve_host: false`) share one route table. It listens on Cloud Run's `$PORT`, and its admin API is off. In Stage 2 it becomes the cluster's ingress controller and the same routes become Kubernetes resources.

| Path | Upstream |
|---|---|
| `/audit*` | audit-metrics |
| `/auth/login` (20 a minute), `/documents/upload-batch` (60 a minute) | api, rate-limited |
| `/push*`, `/metrics` | blocked (404): internal only |
| everything else, including the WebSocket | api |

There is no `/api/v1` prefix: the routes are the app's own. Ingestion is inside api, so upload goes to api.

Plugins:
- **CORS** with the real frontend origins, never `*` (a wildcard with credentials is rejected by browsers).
- **Rate limiting** on login and upload (per Kong instance; Redis is not needed for that).
- **Request id**, copied into events.
- **Prometheus**, for gateway metrics, on Kong's status port 8100 (not scraped on Cloud Run yet: the sidecar's default scrapes :8080).

Rules and gotchas:
- Workers are not behind Kong; Pub/Sub delivers to them.
- Auth stays in each public service (the token check). Kong adds a layer and does not replace it.
- CORS is answered by Kong only: the app's own CORS is off behind it (`CORS_IN_APP=false`), since two sets of headers make browsers refuse the response.
- Keep the upstreams reachable by their `run.app` URLs and protected by the app token. audit-metrics is therefore public, so its `/push` checks Pub/Sub's OIDC token in the app (`PUSH_AUDIENCE`). Private upstreams would need Google identity tokens, which Kong does not mint on its own.
- Cloud Run routes by the `Host` header, so Kong must not forward the original host, and must use HTTPS to the upstream. This disappears on Kubernetes.
- Kong is one more service to run (min 1 instance) and adds a hop, including for the WebSocket.
- The frontend's `VITE_API_URL` points at Kong.

## 8. Observability (Prometheus and Grafana)

- Every service exposes `/metrics` with `prometheus_client` (`backend/app/metrics.py`): `http_requests_total` and `http_request_duration_seconds` by route template, `events_published_total`, `events_handled_total` and `event_handler_duration_seconds` by role and type, `stage_duration_seconds` by stage (the duration each end-of-run event carries), `llm_calls_total`, `llm_tokens_total` by kind, and `supabase_calls_total` by table. Kong's plugin metrics are on its status port.
- Example queries: `sum by (route) (rate(http_requests_total[5m]))`; `histogram_quantile(0.95, sum by (le, stage) (rate(stage_duration_seconds_bucket[1h])))` (the 95th percentile per stage, which decision 1 watches); `sum(rate(llm_tokens_total[1h])) by (kind)`.
- Stage 1: each backend Cloud Run service runs Google's managed Prometheus sidecar (`run.tf`, the `collector` container), which with no config scrapes `:8080/metrics` every 60 s into Google Managed Service for Prometheus. While CPU is idle between requests a scrape can be late, so short gaps appear. There is no Prometheus server to operate. Stage 2: the same managed service scrapes the pods directly.
- Grafana reads from Managed Prometheus and the Cloud Monitoring data source. Host: Grafana Cloud free tier, or one small instance of our own (open decision 5).
- OpenTelemetry traces continue to Cloud Trace (`telemetry.py`).

What to measure:
- Request rate, latency and status per service, including Kong.
- Stage durations as histograms (the same timings the events record).
- Queue depth per subscription, from the Pub/Sub backlog metric in Cloud Monitoring (also the scaling signal in Stage 2).
- Tokens, cache hits and cost per unit of work (today only logged by `query_stats.py`).
- Stuck work as a gauge per stage, and recoveries and exhausted recoveries as counters (section 16).
- Label by service and stage, never by document or case id (cardinality).

Grafana and the case dashboard stay separate:
- **Case dashboard** (section 9): for officers, inside the app, behind the app's login.
- **Grafana:** for engineers. Latency, errors, queue depth, cost. It is not where officers look.

## 9. Audit trail, dashboard, guardrails, evals

- **Audit trail:** the event stream, persisted by audit-metrics. It survives deletion of cases and documents (no foreign keys), and nothing can edit it. Built: `backend/app/audit.py` stores every event (keyed by its id, so a redelivery is ignored) and `GET /audit/events` reads it.
- **Dashboard:** read endpoints and Postgres views in audit-metrics: cases by stage, risk distribution, stuck or failed, and "needs attention". Time in a stage is the gap between two events.
- **Guardrails (online):** library code inside the worker making the call (output validation, the bias screen).
- **Evals (offline):** the nightly job runs the golden set and writes results to a table the dashboard reads.

## 10. Order of work

Stage 1, about four weeks:

| Step | Deliverable | Can stop here? |
|---|---|---|
| 1 | Event envelope, `events.publish()` with `EVENT_TRANSPORT`, `ROLE`, audit-metrics, api commands publish events, login and human actions audited; `/healthz` and SIGTERM handling; remove the `dev` flag; compose skeleton (emulator, api, audit-metrics); first cloud deploy of api and audit-metrics | Yes: audit trail works |
| 2 | Per-stage duration histograms from the events, to confirm the push choice (section 11, decision 1) | |
| 3 | detection-worker on Pub/Sub, as handler plus adapters (blockers: cancel flag, live pushes, rate budget) | Yes: first real split |
| 4 | doc-worker on Pub/Sub (replaces the queue) | Yes |
| 5 | case-worker (running guard with heartbeat, debounce); sweeper job and owner-side recovery (section 16) | Yes |
| 6 | Kong (template, local and cloud); Terraform with IAM per service, Secret Manager, Scheduler, budget alert; one image build to Artifact Registry; CI matrix with Snyk and Checkov; deploy with tagged revisions and automatic rollback; Workload Identity Federation | |
| 7 | `/metrics` in every service, sidecars, Grafana dashboards | Yes: observability works |
| 8 | Case dashboard, eval job, guardrail checks | |
| 9 | Failure tests, docs, buffer | |
| 10 | Optional: ingestion out of api | |

If time runs short, drop Grafana polish first. Kong and the worker splits carry the decoupling story.

**Status (2026-10-21), `microservices` branch, in code; not yet applied to GCP:**

| Step | Done | Not done |
|---|---|---|
| 1 | Envelope, `events.publish()`, `EVENT_TRANSPORT`, `ROLE`, audit-metrics and `GET /audit/events`, officer actions and login audited (with Kong's request id), `/healthz`, graceful shutdown, `dev` flag removed | First cloud deploy |
| 2 | `stage_duration_seconds` from the events | Reading the real 95th percentile once deployed |
| 3 | detection-worker as handler plus push adapter; live results through Realtime | Database cancel flag (a cancel reaches a run only in the same process); shared rate budget |
| 4 | doc-worker replaces the in-memory queue (`queue_service.py` removed) | |
| 5 | case-worker; "already running" read from the database with a 15-minute limit | Heartbeat, `submission_finalized` debounce in the case-worker, the sweeper (section 16) |
| 6 | Kong (local and cloud), Terraform (IAM per service, Secret Manager, Pub/Sub, Cloud Run, Workload Identity Federation, budget), one build per role to Artifact Registry, deploy with automatic rollback, Checkov and Snyk in CI (non-blocking until the first findings are reviewed), GHCR publishing removed | Cloud Scheduler (comes with the sweeper) |
| 7 | `/metrics` in every service, the Managed Prometheus sidecar | Grafana dashboards (decision 5), Kong's metrics on Cloud Run |
| 8-10 | | Not started |

Stage 2 (after Stage 1, each step deployable on its own):
1. GKE Autopilot module in Terraform; Helm chart with a values file per role.
2. Pull subscribers behind the existing handlers; KEDA on Pub/Sub backlog.
3. Kong as ingress controller; Managed Prometheus on the pods; Workload Identity.
4. Argo CD in place of `gcloud run deploy`; Checkov on the charts.
5. Litmus chaos tests (pod failure, load against the autoscaler).

## 11. Decisions

Locked (changing them later touches every service): the event envelope and type names, one topic with filtered subscriptions, the ownership table, a Dockerfile target per role, Kong as the single entry point, handler plus adapters for workers.

Resolved 2026-10-21:
- **Push dispatch for Stage 1.** Detection takes about 1-2 minutes per document, well inside the 600 s ack deadline. Revisit only if the measured 95th percentile of any stage passes 8 minutes (then a lease-extending pull subscriber, or Cloud Tasks). Stage 2 pulls anyway.
- **Ingestion inside api** first.
- **One GCP project, one Supabase project**, no staging; tagged revisions and automatic rollback instead (section 6).
- **`dev` flag removed**; it existed only for UAT.
- **Local default stays `uvicorn` + `npm run dev`** for every developer; compose is opt-in.
- **Tests stub Anthropic and Supabase**, CI included (section 17).
- **Injection tool** only in uvicorn mode.
- **Case match** stays in api (computed on read).

Open:
1. ~~Push or lease-extending dispatch~~ (resolved: push).
2. ~~Ingestion separate or inside api~~ (resolved: inside api).
3. **Supervisor check:** does Stage 1 on Cloud Run count as "genuinely decoupled" for the Cloud Native award, or is Kubernetes expected? (`CLAUDE.md` records "EC2 or Cloud Run, it doesn't matter".) This decides whether Stage 2 moves earlier.
4. **Automatic case analysis** before November, which decides how much the case-worker's gate needs.
5. **Where Grafana runs:** Grafana Cloud free tier, or one small instance of our own.
6. **Login:** keep `AUTH_USERS` as a documented limitation, or move to Supabase Auth (touches login, token checks, WebSocket tickets, tests).
7. **Snyk and our `pyproject.toml`:** confirm it works, or use the fallbacks.

## 12. Risks

- **Dual write:** database updated, publish lost. Mitigated by the sweeper (section 16).
- **Duplicate delivery and the ack deadline:** idempotent handlers, compare-and-swap status changes.
- **Supabase Realtime** carries all live updates; check its limits under load.
- **Kong** is a new single entry point: a misrouted `Host` header or a bad CORS rule breaks the whole frontend. Test the config locally with compose first.
- **Min-instance cost** during the demo (Kong, api, two workers).
- **Metric cardinality:** an id used as a label would overwhelm Prometheus.
- **One environment:** a bad deploy hits the live system. Mitigated by tagged revisions, the post-deploy check and automatic rollback (section 6).
- **Shared Supabase:** local runs and the cloud see each other's cases, Realtime changes and stuck documents. Anything that scans the whole database must run in one place only: the sweeper runs only in the cloud (a local run must name a case, section 16). A local process killed mid-run is recovered by the cloud sweeper, which is acceptable.
- **Scope:** Stage 2 in the same four weeks would put too much new surface in play. Keep it after Stage 1 unless open decision 3 says otherwise.

## 13. Failure tests (deliverable for the submission)

- Kill a worker mid-run: the message is redelivered and the document recovers.
- Flood uploads: the platform scales out, and the queue drains.
- Stop audit-metrics: events wait, then catch up.
- A poison message goes to the dead-letter topic and does not block the queue.
- Lost publish: set a document to `queued` without publishing; the sweeper recovers it within one sweep plus the threshold, and the audit trail shows `recovery.requested` then `document.recovered`.
- Stage 2: the same four as Litmus experiments (pod failure, load against the autoscaler).

## 14. Borrowed from the reference material

From `SMU-IS/agentic-ai-trading-be`: API and worker as separate processes of one codebase; Kong with one path prefix per service; a notification service (our live updates) and a metrics tracker (audit-metrics and Grafana); stage timestamps (our event gaps); a reusable workflow with thin callers; Helm chart with a values file per service and Argo CD-style deploys (Stage 2); Terraform layout with `monitoring.tf`; per-role compose files; chaos tests (pod failure, load).

From the architecture and pipeline diagrams: Artifact Registry, Terraform, Grafana and Prometheus, IAM, a billing view, Snyk and Checkov in CI, GKE with Argo CD (Stage 2).

Not taken: 15 services (the reference suits many unrelated ML jobs), wildcard CORS with credentials, a gateway with no rate limiting, no healthchecks or resource limits, no lint or security scans in CI, Firestore, Cloud Storage for documents, Stripe, OpenAI.

## 15. Out of scope here

Stateful and learning agents (separate plan), a parser sandbox service, Redis (until a measured need), an OutSystems integration (to confirm with the sponsor).

## 16. Stuck work and recovery (a showcase item)

**Deferred (2026-10-21):** the happy path comes first; this is the edge-case pass after it. Nothing below is built.

Today "stuck" is only computed on read (`monitoring.is_stuck`) for the "Processing stuck" badge and the manual retry; nothing polls, and `detecting` is not covered. Pub/Sub redelivery already handles a worker that dies mid-request. Recovery covers what redelivery cannot: a publish lost after the database write, a message that went to the dead-letter topic, and a run that ended without reaching a final status.

**Who does what**
- **Sweeper** (Cloud Run Job, every 2 minutes): reads in-flight documents and running analysis rows, and for each one past its threshold publishes `recovery.requested` with the stage and the next attempt number (the count of earlier `recovery.requested` events for it in `audit_events`). It writes no state.
- **Owning worker** handles `recovery.requested` for its stage: compare-and-swap the status back to the stage's start (only if it is still the stuck status), run the stage, publish `document.recovered`. Past `MAX_RECOVERY_ATTEMPTS` (3) it publishes `recovery.exhausted` and marks the work failed the way it does today (`failed` for the document pipeline; back to `approved` with the error for detection; a failed run for case analysis).

| Stage (status) | Owner | Threshold |
|---|---|---|
| `uploaded`, `queued` (publish lost) | doc-worker | 5 min |
| `processing`, `extracting` | doc-worker | 15 min |
| `detecting` | detection-worker | 15 min (runs take 1-2 min; past the 600 s ack deadline, so it never races a redelivery) |
| case analysis `running` | case-worker | heartbeat older than 3 min (needs a `heartbeat_at` column on `case_analysis_runs`: a database change, ask first) |

`is_stuck` stays the single definition (extended to `detecting`), shared by the sweeper, the badge and the manual retry.

**How it is shown**
- Audit trail: `recovery.requested`, `document.recovered`, `recovery.exhausted`, with the attempt number.
- Case dashboard: "stuck now" and "recovered in the last 24 h" panels.
- Grafana: stuck gauge per stage; recovery and exhaustion counters.
- UI: the "Processing stuck" badge appears, then clears without anyone acting.
- Failure tests (section 13): kill a worker mid-run (redelivery), and a lost publish (sweeper).

**Local:** the sweeper is off in uvicorn mode (`SWEEPER_ENABLED=false`) and absent from compose by default, because the database is shared and a local sweeper would pick up cloud documents. To demonstrate locally, run it once against one case: `python -m app.sweeper --once --case <case_id>`.

## 17. Testing (no Anthropic, no Supabase)

Every automated test stubs Claude and Supabase; `conftest.py` already refuses real calls, and the new layers keep that rule.

| Layer | What it proves | How |
|---|---|---|
| Existing pytest | Behaviour of every role | Unchanged; `EVENT_TRANSPORT=inprocess` |
| Handler tests | Each worker's `handle(event)`: right status changes, idempotent on a repeat, ignores other types | Fake repositories, a fake publisher that records envelopes |
| Push adapter tests | Pub/Sub push body decoded (base64 data, attributes); 2xx on success or duplicate; non-2xx on a malformed message, so it is retried and then dead-lettered | FastAPI `TestClient` per role |
| Event-flow test | The whole chain across roles: upload, analyzed, approve, detected, analysis completed; the audit sequence; duplicate delivery; recovery after a simulated crash; sweeper recovery | An in-memory bus routes each published envelope to the matching role's push adapter; fake repositories, stub LLM replies |
| Subscription contract | Every published type has a consumer (or is audit-only); Terraform's filters match the handlers | One source of truth (`events/types.py`) exported to the JSON Terraform reads |
| Kong config test (CI) | Routes, CORS, request id, rate limiting, WebSocket upgrade | The rendered `kong.yml` in a Kong container in front of three stub upstreams that echo their name; no app |
| Frontend | UI regressions | Playwright against the mocked backend, as today |

Not automated tests (real Supabase and Claude, run by hand or after a deploy): the compose walkthrough (section 18.2) and the post-deploy check (section 6).

## 18. Running it

### 18.1 Daily development (everyone, unchanged)

```bash
cd backend && uvicorn app.main:app --reload    # http://localhost:8000
cd frontend && npm run dev                     # http://localhost:5173
```

Defaults: `EVENT_TRANSPORT=inprocess`, `ROLE=all`. One process runs every role and runs each event's handlers as background tasks, so nothing needs Docker, Pub/Sub or Kong. The injection tool is available here only.

### 18.2 The split, locally (opt-in, before a deploy)

Layout: `compose.yaml` at the root `include:`s `deploy/compose/{pubsub,api,workers,audit-metrics,kong,frontend}.yaml`. `pubsub-init` runs `python -m app.events.emulator_setup` once, creating the topic, the dead-letter topic and one push subscription per role, then exits. Kong is built from `deploy/kong/`. Every backend container reads `backend/.env` (as uvicorn does); compose adds `EVENT_TRANSPORT=pubsub`, `PUBSUB_EMULATOR_HOST`, `GOOGLE_CLOUD_PROJECT=local` and `CORS_IN_APP=false`, and each image sets its own `ROLE`.

```bash
docker compose up --build                 # stop uvicorn first: Kong takes :8000
cd frontend && npm run dev                # same UI, now through Kong
```

| Container | Stands in for | Memory (rough) |
|---|---|---|
| `pubsub-emulator` + `pubsub-init` (exits) | Pub/Sub | 300 MB |
| `kong` on :8000 | Kong on Cloud Run | 150 MB |
| `api` | api | 700 MB |
| `workers` (`ROLE=doc-worker,detection-worker,case-worker`, the `detection-worker` image) | the three workers, as one container to save memory | 1.5-2 GB |
| `audit-metrics` | audit-metrics | 700 MB (it imports the whole app) |

About 3.5 GB in total. The code paths are the same as in the cloud (emulator, push, envelopes, Kong routes); each subscription still pushes to its own `/push/<role>`, only the process boundaries between the three workers are merged. `--profile frontend` adds the nginx build on :5173 instead of `npm run dev`.

**Debug the workers in the IDE:** `PUSH_HOST=host.docker.internal PUSH_PORT=8001 docker compose up --build --scale workers=0`, then from `backend/`: `ROLE=doc-worker,detection-worker,case-worker EVENT_TRANSPORT=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local uvicorn app.main:app --port 8001` (in PowerShell, set each with `$env:NAME = "value"` first). One role alone works the same way with its own `ROLE`.

**Walkthrough before a deploy** (real Supabase and Claude; costs a few calls):
1. Create a case and upload one document: it reaches `analyzed`.
2. Approve it: it reaches `detected`; the flags appear.
3. Run case analysis: the score and summary appear.
4. `GET /audit/events?case_id=<id>` lists the events in order, each officer action with Kong's request id.
5. `docker compose restart workers` during a detection run: the interrupted run returns to `approved`, the failed push is redelivered, and the document still reaches `detected`.
6. `docker compose stop audit-metrics`, act, start it again: the missed events appear.
7. `docker compose run --rm pubsub-init python -m app.events.emulator_setup --poison`: it is retried, then lands in the `domain-events-dead-letter-pull` subscription, and nothing else stalls.

Emulator limits: no OIDC tokens and no IAM, so `/push` takes unsigned requests locally (`PUSH_AUDIENCE` is unset). If the emulator refuses subscription filters or dead-letter policies, `emulator_setup` creates the subscriptions without them and logs a warning: handlers ignore types they don't handle (section 3), and step 7 can then only be checked in the cloud.

### 18.3 Deploying

First time: `deploy/terraform/README.md` (state bucket, `terraform apply`, secret values, the GitHub repository variables). After that: Actions tab, "Deploy to Cloud Run", Run workflow. It builds one image per role, Kong and the frontend, rolls them out, checks the gateway, and rolls every service back if the check fails (section 6). Manual rollback: `gcloud run services update-traffic <service> --to-revisions=<revision>=100 --region asia-southeast1`. Logs: Cloud Logging, filtered by service; one request is followed through the audit trail by its Kong request id.

Configuration (sizes, scaling, environment, secrets) changes in Terraform, not in the workflow: edit `deploy/terraform/main.tf` and `terraform apply`. A changed event subscription: `python -m app.events.types` (rewrites `subscriptions.json`), then `terraform apply`.

**Scale to zero and the idle period.** Every service defaults to 0 minimum instances. After its last request (for a worker, its last Pub/Sub push) Cloud Run keeps an idle instance for up to about 15 minutes (Google publishes no exact figure), then stops it. The next event then waits for a cold start, roughly 10-30 s here because of the LangGraph and OCR imports. Idle instances cost nothing with CPU idle (`cpu_idle`). For a demo, keep them warm with `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }` in `terraform.tfvars`.

### 18.4 What only the cloud shows, and how often it matters

| Item | Kind | After the first working deploy |
|---|---|---|
| Kong `Host` header and HTTPS to `run.app` | Configuration in the template | Settled; touched again only when an upstream is added |
| OIDC on push subscriptions, Pub/Sub invoker role | Terraform | Settled; a new worker copies the pattern |
| Service accounts, Secret Manager mounts, Workload Identity Federation | Terraform | Settled; touched when a secret or service is added |
| Managed Prometheus sidecar | Configuration per service | Settled |
| Work inside the push request, 600 s ack deadline | Design rule in the shared push adapter | Settled once in the adapter; every worker inherits it |
| Cold starts | Behaviour | After about 15 idle minutes; min instances on demo day (section 18.3) |
| 60-minute WebSocket limit | Behaviour | Already handled: the client reconnects |

Each of these fails loudly on the first deploy (404, 403, 502 or a redelivery loop), the post-deploy check catches it, and the rollback keeps the old revision serving. Fix and redeploy.

### 18.5 Troubleshooting

Problems met while setting up and deploying (login consent, quota project, imports, secrets, `--container` flags, `/healthz`, token timeouts, pinned traffic) and their fixes are in `deploy/terraform/README.md`, "Problems met". The deploy status, the checks after a deploy and the clean-up list are there too.

| Symptom | Likely cause |
|---|---|
| Every request through Kong returns 404 from Google | Kong forwards the original `Host`; set `preserve_host: false` |
| A worker never receives messages; Pub/Sub shows 403 | The push service account lacks `roles/run.invoker` on that worker |
| The same message is processed again and again | The handler takes over 600 s or returns non-2xx; check its logs and the stage histogram |
| Browser shows a CORS error | The frontend origin is missing from Kong's CORS plugin |
| Live updates stop after an hour | The Cloud Run WebSocket limit; the client should reconnect by itself |
| A document stays "Processing stuck" | No recovery yet (section 16): check the worker's logs and the dead-letter subscription, then retry it from the UI |
| Kong returns 404 for `/push` or `/metrics` | Intended: they are internal only |
| Google's own HTML 404 for `/healthz` on any Cloud Run URL | Cloud Run reserves paths ending in `z`; use `/health` |
| Events never reach a worker in compose | `pubsub-init` failed or ran before the emulator was up: `docker compose logs pubsub-init`, then `docker compose up pubsub-init` |
