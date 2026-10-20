# Cloud architecture plan

Proposed 2026-10-20. **Not started:** other work comes first; implementation is expected to begin in a few days. Replaces the five-service split in `CLAUDE.md` ("Architecture: Decoupling Requirement") and its "Kubernetes + ArgoCD rejected" line once agreed. Deadline: mid-November 2026.

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
- **Dev injection tool:** `routers/injection.py` is mounted in the production API today. It leaves the production image.
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

Envelope (it is also the `audit_events` row, `backend/sql/audit_events.sql`):

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

Rules:
- At-least-once delivery, so every handler must tolerate a repeat. Status changes are compare-and-swap (`expected_current_statuses`), which the code already has.
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
| Stuck-document monitor | Also re-publishes the event for a document stuck between steps (covers a crash between the database write and the publish) |
| `routers/injection.py` in the API | Removed from the production image |
| Calls such as `queue.enqueue` and `sync_submission_finalized` | `events.publish()`, which calls the handlers in-process in dev and tests, so local running and the test suite stay as they are |
| Token and cost counts only logged (`query_stats.py`); no `/metrics` | `prometheus_client` in every service (section 8) |
| Work arrives through one transport | **Handler plus adapters:** each worker is a `handle(event)` function with two thin adapters, an HTTP push endpoint (Stage 1) and a pull subscriber (Stage 2) |
| No health endpoints, no shutdown handling | `/healthz` for liveness and readiness; on SIGTERM finish the current run or hand it back. Cloud Run benefits now; Kubernetes requires both |

## 6. Platform and delivery

**Runtime, Stage 1 and Stage 2**

| Concern | Stage 1: Cloud Run | Stage 2: GKE (Autopilot) |
|---|---|---|
| A role | Cloud Run service | Deployment and Service per role |
| Scaling | Cloud Run autoscaling; min instances | HPA, or KEDA on Pub/Sub backlog (KEDA also gives scale to zero) |
| Event delivery | Pub/Sub push with OIDC to private services; ack deadline capped at 10 minutes | Workers pull and extend the lease, so long detection runs are no problem |
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
- **Terraform:** modules plus a separate `monitoring.tf`; the budget alert lives here too.

**Security and identity**
- One service account per service with only the permissions it needs; only the Pub/Sub service account may invoke the workers.
- Secrets in Secret Manager and mounted per role, not plain environment variables set from GitHub secrets: Anthropic key in the three workers only; token-signing key in the public services; Supabase key in all. The database cannot be partitioned further (one key), so those, plus which services are public, are the boundaries we can enforce.

**Cost**
- Scale to zero for workers where cold starts allow; min-instances 1 for api, Kong, and (on demo day) doc-worker and detection-worker, since OCR and LangGraph imports are slow.
- A budget alert at 50% and 90%, since LLM calls and always-on instances are what cost money.
- Stage 2 adds a cluster fee (Autopilot has a free zonal tier) and the operations time for Helm and Argo CD.

**Local:** a root compose file that `include:`s one file per role, plus Kong.

## 7. Gateway (Kong)

Kong runs as a Cloud Run service in DB-less mode, with its routes in a `kong.yml` baked into the image. It listens on Cloud Run's `$PORT`, and its admin API is off. In Stage 2 it becomes the cluster's ingress controller and the same routes become Kubernetes resources.

| Path | Upstream |
|---|---|
| `/api/v1/upload*` | ingestion |
| `/api/v1/audit*`, `/api/v1/dashboard*` | audit-metrics |
| everything else, including the WebSocket | api |

Plugins:
- **CORS** with the real frontend origins, never `*` (a wildcard with credentials is rejected by browsers).
- **Rate limiting** on login and upload (per Kong instance; Redis is not needed for that).
- **Request id**, copied into events.
- **Prometheus**, for gateway metrics.

Rules and gotchas:
- Workers are not behind Kong; Pub/Sub delivers to them.
- Auth stays in each public service (the token check). Kong adds a layer and does not replace it.
- Keep the three upstreams reachable by their `run.app` URLs and protected by the app token. Private upstreams would need Google identity tokens, which Kong does not mint on its own.
- Cloud Run routes by the `Host` header, so Kong must not forward the original host, and must use HTTPS to the upstream. This disappears on Kubernetes.
- Kong is one more service to run (min 1 instance) and adds a hop, including for the WebSocket.
- The frontend's `VITE_API_URL` points at Kong.

## 8. Observability (Prometheus and Grafana)

- Every service exposes `/metrics` with `prometheus_client`; Kong's plugin metrics are exposed the same way.
- Stage 1: each Cloud Run service runs Google's managed Prometheus sidecar, which scrapes `/metrics` into Google Managed Service for Prometheus. There is no Prometheus server to operate. Stage 2: the same managed service scrapes the pods directly.
- Grafana reads from Managed Prometheus and the Cloud Monitoring data source. Host: Grafana Cloud free tier, or one small instance of our own (open decision 5).
- OpenTelemetry traces continue to Cloud Trace (`telemetry.py`).

What to measure:
- Request rate, latency and status per service, including Kong.
- Stage durations as histograms (the same timings the events record).
- Queue depth per subscription, from the Pub/Sub backlog metric in Cloud Monitoring (also the scaling signal in Stage 2).
- Tokens, cache hits and cost per unit of work (today only logged by `query_stats.py`).
- Stuck-document count, as a gauge.
- Label by service and stage, never by document or case id (cardinality).

Grafana and the case dashboard stay separate:
- **Case dashboard** (section 9): for officers, inside the app, behind the app's login.
- **Grafana:** for engineers. Latency, errors, queue depth, cost. It is not where officers look.

## 9. Audit trail, dashboard, guardrails, evals

- **Audit trail:** the event stream, persisted by audit-metrics. It survives deletion of cases and documents (no foreign keys), and nothing can edit it. The current `backend/app/audit.py` (direct insert, login only) is a draft that `events.publish()` replaces.
- **Dashboard:** read endpoints and Postgres views in audit-metrics: cases by stage, risk distribution, stuck or failed, and "needs attention". Time in a stage is the gap between two events.
- **Guardrails (online):** library code inside the worker making the call (output validation, the bias screen).
- **Evals (offline):** the nightly job runs the golden set and writes results to a table the dashboard reads.

## 10. Order of work

Stage 1, about four weeks:

| Step | Deliverable | Can stop here? |
|---|---|---|
| 1 | Event envelope, `events.publish()`, audit-metrics, api commands publish events, login and human actions audited; `/healthz` and SIGTERM handling | Yes: audit trail works |
| 2 | Per-stage measurement from the `perf` logs; decide open decision 1 | |
| 3 | detection-worker on Pub/Sub, as handler plus adapters (blockers: cancel flag, live pushes, rate budget) | Yes: first real split |
| 4 | doc-worker on Pub/Sub (replaces the queue; stuck monitor re-publishes) | Yes |
| 5 | case-worker (running guard, debounce) | Yes |
| 6 | Kong; Terraform with IAM per service, Secret Manager, budget alert; one image build to Artifact Registry; CI matrix with Snyk and Checkov; Workload Identity Federation | |
| 7 | `/metrics` in every service, sidecars, Grafana dashboards | Yes: observability works |
| 8 | Case dashboard, eval job, guardrail checks | |
| 9 | Failure tests, docs, buffer | |
| 10 | Optional: ingestion out of api | |

If time runs short, drop Grafana polish first. Kong and the worker splits carry the decoupling story.

Stage 2 (after Stage 1, each step deployable on its own):
1. GKE Autopilot module in Terraform; Helm chart with a values file per role.
2. Pull subscribers behind the existing handlers; KEDA on Pub/Sub backlog.
3. Kong as ingress controller; Managed Prometheus on the pods; Workload Identity.
4. Argo CD in place of `gcloud run deploy`; Checkov on the charts.
5. Litmus chaos tests (pod failure, load against the autoscaler).

## 11. Decisions

Locked (changing them later touches every service): the event envelope and type names, one topic with filtered subscriptions, the ownership table, a Dockerfile target per role, Kong as the single entry point, handler plus adapters for workers.

Open:
1. **Push or lease-extending dispatch (Stage 1).** Get the 95th-percentile detection time. Over about 8 minutes, use a pull subscription that extends its lease, or Cloud Tasks (also gives a global concurrency cap and a delayed, de-duplicated dispatch for the case-analysis debounce). Stage 2 pulls anyway.
2. **Ingestion separate or inside api.** Inside api first.
3. **Supervisor check:** does Stage 1 on Cloud Run count as "genuinely decoupled" for the Cloud Native award, or is Kubernetes expected? (`CLAUDE.md` records "EC2 or Cloud Run, it doesn't matter".) This decides whether Stage 2 moves earlier.
4. **Automatic case analysis** before November, which decides how much the case-worker's gate needs.
5. **Where Grafana runs:** Grafana Cloud free tier, or one small instance of our own.
6. **Login:** keep `AUTH_USERS` as a documented limitation, or move to Supabase Auth (touches login, token checks, WebSocket tickets, tests).
7. **Snyk and our `pyproject.toml`:** confirm it works, or use the fallbacks.

## 12. Risks

- **Dual write:** database updated, publish lost. Mitigated by the stuck-document re-publish.
- **Duplicate delivery and the ack deadline:** idempotent handlers, compare-and-swap status changes.
- **Supabase Realtime** carries all live updates; check its limits under load.
- **Kong** is a new single entry point: a misrouted `Host` header or a bad CORS rule breaks the whole frontend. Test the config locally with compose first.
- **Min-instance cost** during the demo (Kong, api, two workers).
- **Metric cardinality:** an id used as a label would overwhelm Prometheus.
- **Scope:** Stage 2 in the same four weeks would put too much new surface in play. Keep it after Stage 1 unless open decision 3 says otherwise.

## 13. Failure tests (deliverable for the submission)

- Kill a worker mid-run: the message is redelivered and the document recovers.
- Flood uploads: the platform scales out, and the queue drains.
- Stop audit-metrics: events wait, then catch up.
- A poison message goes to the dead-letter topic and does not block the queue.
- Stage 2: the same four as Litmus experiments (pod failure, load against the autoscaler).

## 14. Borrowed from the reference material

From `SMU-IS/agentic-ai-trading-be`: API and worker as separate processes of one codebase; Kong with one path prefix per service; a notification service (our live updates) and a metrics tracker (audit-metrics and Grafana); stage timestamps (our event gaps); a reusable workflow with thin callers; Helm chart with a values file per service and Argo CD-style deploys (Stage 2); Terraform layout with `monitoring.tf`; per-role compose files; chaos tests (pod failure, load).

From the architecture and pipeline diagrams: Artifact Registry, Terraform, Grafana and Prometheus, IAM, a billing view, Snyk and Checkov in CI, GKE with Argo CD (Stage 2).

Not taken: 15 services (the reference suits many unrelated ML jobs), wildcard CORS with credentials, a gateway with no rate limiting, no healthchecks or resource limits, no lint or security scans in CI, Firestore, Cloud Storage for documents, Stripe, OpenAI.

## 15. Out of scope here

Stateful and learning agents (separate plan), a parser sandbox service, Redis (until a measured need), an OutSystems integration (to confirm with the sponsor).
