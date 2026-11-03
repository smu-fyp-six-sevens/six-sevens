# Cloud architecture

How the backend is split into services on Google Cloud, the decisions behind it, and what's left to do. Updated 2026-10-24. Deadline: mid-November 2026.

**Where it stands:** Stage 1 runs on Cloud Run in project `fyp-six-sevens` (`asia-southeast1`). It was first deployed on 2026-10-22, and since 2026-10-24 every push to `main` deploys automatically once CI passes. There is one GCP project and one Supabase project, shared by local runs and the cloud; a staging environment is planned (section 9). Stage 2 (section 10) adds staging, load and failure tests, and a Helm chart that lets the same services run on a bank's own Kubernetes platform.

Operations (setup, secrets, the checks after a deploy, every problem met and its fix) are in `deploy/terraform/README.md`. Adding a service: `docs/ADDING_A_SERVICE.md`. Presenting it: `docs/architecture/` (guide and draw.io diagrams).

## 1. Rules

The award requirement is a *genuinely* decoupled architecture, not a monolith on one instance.

- Services talk **only** through Pub/Sub events and Supabase state. No service calls another.
- One repo, one Dockerfile with **a build target per role** (shared layers); `ROLE` picks what a process runs. Locally, `uvicorn` runs every role in one process with in-process events (`EVENT_TRANSPORT=inprocess`, the default).
- Split only where scaling, failure behaviour or permissions differ, never finer than a run. External validation stays inside detection.
- Portable: stateless services, configuration from the environment, workers as a `handle(event)` function with a push adapter now and a pull adapter in Stage 2.

| Cloud-native criterion | Where it's shown |
|---|---|
| Loose coupling | Services connected only by events (sections 2-3) |
| Independent scaling | Each role scales on its own (section 2) |
| Resilience | Redelivery, dead-letter topic, idempotent compare-and-swap handlers, the sweeper, automatic rollback (sections 3, 5, 6) |
| Observability | `/metrics`, Managed Prometheus, Grafana, Cloud Trace, the audit trail, alerts (section 7) |
| Automated, secure delivery | CI/CD with Checkov and Snyk, Terraform, Workload Identity Federation (section 6) |
| Least privilege | A service account per service, Secret Manager per service, private workers (section 6) |
| Cost control | Scale to zero, budget and Claude-spend alerts, cost per document and case (section 7) |

## 2. Services

| Service | Public | Receives | Does | Size (Terraform `local.roles`) |
|---|---|---|---|---|
| **api** | via Kong | HTTP | Reads, login, officer commands (publish events), uploads (ingestion lives here), WebSocket live updates, case match, notes | 1 CPU, 1 GiB, max 3 |
| **doc-worker** | no | `document.uploaded`, `retyped`, `retry_requested`, `cancelled`; its recoveries | OCR, classify, extract, reclassify loop | 2 CPU, 4 GiB, 2 at once, max 3 |
| **detection-worker** | no | `document.approved`, detection retry and cancel; its recoveries | Lead agent, 3 specialists, 10 checks, findings | 2 CPU, 4 GiB, 2 at once, max 3; carries the zoning index |
| **case-worker** | no | `case.analysis.requested`; its recoveries | Cross-document analysis, deterministic risk score, summary | 1 CPU, 2 GiB, max 2 |
| **audit-metrics** | via Kong (`/audit`) | every event | Stores each event in `audit_events`; serves the audit trail and per-case cost; runs the sweeper | 1 CPU, 1 GiB, max 2 |
| **kong** | public | HTTP | The single entry point (section 4) | |
| **frontend** | public | HTTP | Static React build in nginx; Kong's URL is baked in at build | |
| **grafana** | public (login) | HTTP | Dashboards; Google's Prometheus query proxy as a sidecar | max 1 |

Workers can be invoked only by the `pubsub-push` identity (OIDC); audit-metrics is public so Kong can reach it, and checks that token itself on `/push`. Every service scales to zero by default (`min_instances` in `terraform.tfvars`; for a demo, warm api, Kong and the two document workers).

## 3. Events and state

One topic, `domain-events`, plus `domain-events-dead-letter` (and a pull subscription to read it). Each role has a filtered push subscription; the filters come from `backend/app/events/types.py`, exported to `deploy/terraform/subscriptions.json` (`python -m app.events.types`; Pub/Sub filters max 256 bytes and are immutable, so a change replaces the subscription). A message carries ids only; Supabase holds the state. The envelope is also the `audit_events` row: `event_id, type, occurred_at, actor_type, actor, case_id, document_id, outcome, started_at, duration_ms, detail`.

- **At-least-once:** handlers tolerate repeats; status changes are compare-and-swap; audit storage ignores a repeated event id. A handler acks and ignores a type it doesn't handle.
- **A human action that triggers work** (approve, retype, request analysis) fails with a 502 if the publish fails; a failed approval publish is undone. Audit-only actions (`case.created`, `note.added`, ...) use `events.record`, which never fails the request.
- **Kong's request id** goes into `detail`, so one request can be followed through the audit trail.
- **Traces:** each message carries a W3C `traceparent`, so an upload is one trace in Cloud Trace across services.

**One writer per transition:** `documents` up to `queued` and officer actions: api. `queued` to `analyzed`/`failed`/`cancelled` and extracted fields: doc-worker. `approved` to `detected`, `detection_results`, the document's findings, the detection trace: detection-worker. The case's cross-document findings: case-worker. `case_analysis_runs`, `risk_scores`: case-worker. `audit_events`: audit-metrics only. Recovery of a stuck stage: the worker that owns it (the sweeper only publishes). The case's stage (`cases.status`, `case_stage.sync_case_stage`) is debounced in api: each request runs any pending refresh before returning (`document_repository.flush_syncs`), because Cloud Run throttles CPU after a response.

## 4. Gateway (Kong)

DB-less Kong on Cloud Run (`deploy/kong/`); routes rendered at start from `kong.yml.tmpl`, so compose and Cloud Run share one table.

| Path | Goes to |
|---|---|
| `/audit*` | audit-metrics |
| `/auth/login` (20 a minute), `/documents/upload-batch` (60 a minute) | api, rate-limited |
| `/push*`, `/metrics` | blocked (404) |
| everything else, including the WebSocket | api |

Gotchas: Cloud Run routes by `Host`, so Kong must not preserve it and must use HTTPS upstream; CORS is answered by Kong only (`CORS_IN_APP=false` behind it), never `*`; auth stays in each service. Kong's metrics (status port 8100) are scraped by its own sidecar with the config in the `kong-gmp-config` secret.

## 5. Stuck work and recovery

Pub/Sub redelivery covers a worker that dies mid-request. The sweeper covers the rest: a publish lost after the database write, a dead-lettered message, a run that never reached a final status. No database changes were needed.

- Cloud Scheduler publishes `sweep.requested` every 5 minutes; audit-metrics runs the sweeper (`backend/app/sweeper.py`, only where `SWEEPER_ENABLED`, which Terraform sets on audit-metrics). Locally it's off; `python -m app.sweeper --once --case <id>` runs it for one case.
- Limits: uploaded or queued 5 min (`STUCK_WAITING_SECONDS`); processing, extracting or detecting 15 min (`STUCK_RUNNING_SECONDS`; detecting is timed from the newest approval, retry or recovery event); a running case analysis 15 min (`CASE_ANALYSIS_TIMEOUT_SECONDS`).
- The sweeper publishes `recovery.requested` with an `owner` attribute; only that worker's subscription matches. Attempts are counted from the audit trail (last 24 h), a full limit apart, at most 3 (`RECOVERY_MAX_ATTEMPTS`).
- Owners: doc-worker resets to queued and reruns (gives up as `failed`, so Retry works); detection-worker resets to approved and reruns (gives up as approved with an error); case-worker marks the run failed with no rerun (Claude calls are the officer's call).
- Shown as `document.recovered` / `recovery.exhausted` events, the `stuck_work` and `recoveries_total` metrics, the Pipeline dashboard and an email alert on give-up.
- **Cancel across instances:** a running detection polls the audit trail every 5 s for a cancel; a document pipeline polls its own status.
- **WebSocket tickets** are HMAC-signed with a 10 s expiry, so any api instance accepts them (a replay inside 10 s on another instance would pass).

## 6. Delivery and security

**CI** (`.github/workflows/ci-cd.yml`, on every PR and every push to `main`, except docs-only pushes): backend (ruff, pyright, pytest with coverage; pip and exiftool cached), frontend (lint, build, Playwright in Microsoft's Playwright container, version pinned to the lockfile), security (Checkov on Terraform, Dockerfiles and workflows; Snyk on `requirements.txt` and npm; Terraform `fmt` and `validate`; **blocking**).

**Deploy** (`deploy.yml`, called by CI when all four pass on `main`, or run by hand; one at a time, never cancelled halfway):
1. Build each image once (role targets of the backend Dockerfile, Kong, frontend, Grafana) into Artifact Registry `app`, skipping any this commit already has; a registry build cache keeps it fast. One service list at the top of the workflow drives every step.
2. Record the revision each service is serving.
3. Roll out every backend role but api, then api, Kong, the frontend and Grafana.
4. Check through Kong with no LLM and no writes: `/health` answers (not `/healthz`: Cloud Run reserves paths ending in `z`), an authenticated route answers 401, `/push` and `/metrics` are blocked, a worker refuses a POST without Pub/Sub's identity, CORS answers the frontend, the frontend serves, Grafana is up and wants a login.
5. On any failure, every service goes back to its recorded revision.

Terraform owns everything else about a service (`deploy/terraform`); `apply` is manual and must come before a push that needs new infrastructure.

**Security:** a service account per service; secrets in Secret Manager, each readable only by the services that need it (the Anthropic key in the three workers, the token key in the public services); Workload Identity Federation for GitHub, no stored keys; containers run as non-root with health checks; Checkov findings that don't apply are skipped inline with a reason.

**Cost:** everything scales to zero (idle costs about nothing); a budget alert; Claude spend tracked per agent, document and case. Artifact Registry cleanup policies delete versions older than 7 days (untagged ones after 1) and always keep the 5 newest of each image.

## 7. Observability

- **Metrics:** every service exposes `/metrics` (`backend/app/metrics.py`): HTTP, events, stage durations, Claude calls, tokens, cost, cache and latency by `agent` (the span the call ran in) and `model`, cost per unit of work, detection tool outcomes and verdicts, Supabase calls, stuck work and recoveries. Labels are never document or case ids.
- **Scraping:** Google's Managed Prometheus sidecar beside each backend service and Kong, every 30 s, labelling each series with the service as `namespace` (Cloud Run system metrics use `service_name`).
- **Grafana** (`deploy/grafana`, Grafana 12.4.10 with the BigQuery plugin pinned to 3.4.6; bump them together): four dashboards (Services, Pipeline, Agents, Cost) baked into the image, so it's stateless. The dashboard JSON is generated by `deploy/grafana/build_dashboards.py`: change panels there, not in the JSON. The Cost dashboard's top row reads Google's billed cost from the Cloud Billing export (`billing_export.costs` view) once `billing_export_table` is set.
- **Alerts** (`monitoring.tf`, to `alert_email`): dead letters, API 5xx above 5%, daily Claude spend, recovery given up.
- **Cost per document and case:** from the audit trail (each end-of-run event carries `llm_cost_usd`), served by `GET /audit/cases/{id}/cost` and shown in the case page's Claude Cost tab.
- **Locally:** `docker compose --profile observability up prometheus grafana jaeger` (Grafana admin/admin on :3000, Jaeger on :16686).

## 8. Decisions

**Locked** (changing one touches every service): the event envelope and type names, one topic with filtered subscriptions, the ownership rules, a Dockerfile target per role, Kong as the only entry point, handler plus adapters.

**Settled:** push delivery for Stage 1 (detection takes 1-2 min, inside the 600 s ack deadline; revisit if a stage's p95 passes 8 minutes); ingestion inside api; Grafana as our own Cloud Run service; `uvicorn` + `npm run dev` stays the default for everyone, compose is opt-in; tests stub Anthropic and Supabase; the injection tool only in uvicorn mode; case match stays in api; no Redis until a measured need; Snyk scans `requirements.txt`.

**Open:**
1. **Award check:** the supervisor accepted the Cloud Run architecture (2026-10-26, section 10); confirm the Cloud Native award assessment is fine with Kubernetes as a tested Helm chart rather than a running cluster.
2. **Automatic case analysis** before November (the case-worker gate, `CROSS_DOCUMENT_AND_RISK_PLAN.md` Step 0).
3. **Login:** keep `AUTH_USERS` as a documented limitation, or move to Supabase Auth (touches login, token checks, WebSocket tickets, tests).
4. **Staging** (section 9): approval before production or fully automatic; Grafana in staging; who approves; a smoke test per merge or nightly only.

## 9. To do

**Stage 1**
- **Failure tests** (a deliverable): kill a worker mid-run (redelivered, the document recovers); flood uploads (scale out, the queue drains); stop audit-metrics (events wait, then catch up); a poison message goes to the dead-letter topic without blocking; a lost publish (set a document to `queued` without publishing; the sweeper recovers it and the audit trail shows `recovery.requested` then `document.recovered`).
- **Case dashboard in the app:** cases by stage, risk distribution, stuck or failed, "needs attention", from audit-metrics. (`/dashboard` is mock data owned by teammates.)
- **Shared Anthropic rate budget** across instances: today each instance caps itself (max instances times concurrency).
- **A Kong config test in CI:** the rendered `kong.yml` in front of stub upstreams (routes, CORS, rate limits, WebSocket upgrade). See `backend_tests.md` step 3 for the other missing infrastructure checks.
- **Optional:** ingestion out of api; the frontend reading Kong's URL at runtime (needed for staging anyway).

**Staging** (planned, not started). A second GCP project (`fyp-six-sevens-staging`) and a second free Supabase project, run by the same Terraform with a second variables file and state. Rejected: suffixing every resource in one project (shared IAM, easy to cross-wire) and tagged Cloud Run revisions (same database and topic).
1. **Schema as code:** dump production's schema into `supabase/migrations/` (read-only, with the user's go-ahead); from then on schema changes are migrations, staging first. The repo has no SQL today.
2. **Frontend runtime config:** nginx writes `/config.js` from an environment variable, so one image serves both environments.
3. **Terraform:** an `environment` variable and `staging.tfvars` / `production.tfvars` (staging: min 0, smaller max, lower budget, alerts and billing export optional). Workload Identity's condition also requires the GitHub environment, so only an approved job gets a production token (this also clears Checkov's CKV_GCP_125 skip).
4. **Create the project** (a billing admin links it), apply Terraform and the migrations, fill staging's secrets (`AUTH_USERS`, token key; **no Anthropic key**), give staging's Cloud Run agent read access to production's Artifact Registry so both run the same images.
5. **No Claude calls on staging** (the user's requirement): a stub setting, set only in staging's Cloud Run config, makes `langgraph/_anthropic.py` return canned replies; with no key either, a call that slips past the stub fails instead of spending. Staging then proves the wiring (storage, Pub/Sub, every worker, statuses, WebSocket, Kong, the audit trail), not Claude itself: an API-side break like the `temperature` 400 passes staging. That class is caught by the on-demand live check (one tiny call per model, by hand after an SDK or model change; `PENDING_WORK.md`, "Claude models").
6. **Pipeline:** `deploy.yml` takes an environment input; main deploys to staging, runs the gateway check plus a smoke test (upload one synthetic document, wait for `analyzed`, delete the case), then deploys the same images to production. Open (section 8): promote automatically when staging passes (continuous deployment, recommended), or wait for a GitHub approval (continuous delivery; a required reviewer on the `production` environment, which can also be switched on just for a demo week). GitHub Environments `staging` and `production` hold each project's variables.
7. **Nightly run on staging:** the full path on a few synthetic documents (stubbed Claude), plus the failure tests; it also keeps the free Supabase project from pausing (it pauses after 7 idle days; 2 free projects per organisation). The Playwright suite and pytest stay in CI against mocks; staging's data comes from the migrations and these runs, never copied from production, and the repo's test fixtures stay in the repo.
8. **Terraform in the pipeline:** plan on PRs, apply to staging on merge, apply to production behind the same approval. Last, since the deployer needs broader permissions.

Cost: about $0 idle and cents per merge or night (Cloud Run and Supabase only; no Claude spend on staging).

**Stage 2 (original plan, replaced by section 10):** GKE Autopilot module and a Helm chart with a values file per role; pull subscribers behind the existing handlers, KEDA on Pub/Sub backlog; Kong as ingress controller, Managed Prometheus on the pods, Workload Identity; Argo CD in place of the deploy workflow, Checkov on the charts; Litmus chaos tests.

## 10. Stage 2, revised after the supervisor's feedback (2026-10-26)

**Feedback in short:** the current architecture (decoupled roles, containers, one Pub/Sub topic) is acceptable. Expected usage is a handful of users, so heavier infrastructure isn't justified without a measured need: document it as a scaling option instead. Load and concurrency testing should drive infrastructure decisions. Spend the remaining time on testing, productionisation, usability and business value. He approved a staging environment, liked `docker compose up` as the way to run it, accepted that on-prem deployment is hard for now, and asked for a deployment document (server, model, RAM, storage).

**What changed at a glance**

| Original Stage 2 | Now |
|---|---|
| GKE Autopilot, Helm chart per role | Helm chart as a handover kit, tested on `kind` in CI (below); running on GKE stays behind the triggers |
| Pull subscribers, KEDA on Pub/Sub backlog | Documented option; Cloud Run push stays |
| Kong as ingress controller, Managed Prometheus on pods | Documented option; Kong on Cloud Run stays |
| Argo CD instead of the deploy workflow | Staging and promotion in the existing GitHub Actions pipeline (step 1) |
| Litmus chaos tests | Scripted failure tests on staging (step 4) |
| (none) | Load testing that doesn't call Claude or write to Supabase (step 2), autoscaling tuned from it (step 3), service-level targets (step 5), Claude through Vertex AI (step 6), cost guardrails (step 7), self-host kit and a recovery drill (step 8) |

**Steps, in order** (6 can run alongside the others):
1. **Staging and promotion.** The staging plan in section 9: a second GCP and Supabase project, the same image promoted after an approval step, Terraform plan and apply in the pipeline, schema migrations. First, because load and failure tests against the shared production database would pollute real cases.
2. **Load-test harness as code.** k6 or Locust scripts in the repo, run against staging on demand or nightly, results stored and charted, written up as a capacity report ("handles X concurrent officers and Y cases in flight; the limit is Z"). Two hard rules:
   - **No Claude API calls.** Model calls are replaced by canned responses (a stub at the `langgraph/_anthropic.py` boundary, switched on by a setting that production never sets), so the run spends no tokens.
   - **Supabase reads only, no writes.** The read paths are loaded for real: queue pages, search, counts, case lookups, case snapshots, WebSocket connections. Paths that write (upload, approval, status changes, audit rows) run against an in-memory store instead, so the event pipeline, Pub/Sub delivery and worker scaling are loaded without changing any data.
3. **Autoscaling tuned from the measurements.** Cloud Run concurrency and min and max instances per role, Pub/Sub flow control, `DETECTION_CONCURRENCY`; each change recorded with its before and after numbers.
4. **Scripted failure tests on staging.** The section 9 list (kill a worker mid-run, flood uploads, stop audit-metrics, a poison message, a lost publish), automated and run nightly on staging.
5. **Service-level targets and alerts.** Latency and success targets per stage, with burn-rate alerts in Cloud Monitoring and Grafana.
6. **Claude through Vertex AI.** Google Cloud identity (Workload Identity, no Anthropic key in Secret Manager), Google billing, and regional processing for the bank's data-residency question (`PENDING_WORK.md`, "Deployment note"). Check first that Vertex AI serves the models we use in a region we want.
7. **Cost guardrails.** The shared Anthropic rate budget from section 9, a spend cap per case, and budget alerts.
8. **Self-host kit and a recovery drill.** The compose setup packaged as a `docker compose up` install for the deployment document; one Supabase backup-and-restore test with recovery time and data loss recorded.

**Not doing:** a nightly evaluation job or a model-comparison run (they spend tokens for little return). The evaluation framework (`PENDING_WORK.md`, "Evals and verification") is run on demand when a prompt or model changes, not on a schedule.

**Triggers for running on Kubernetes** (any one, measured, not expected): sustained load above what step 2 shows Cloud Run handles; a stage's p95 near the 600 s push deadline, which would need pull workers; Cloud Run cost above what the same load would cost on GKE Autopilot; a sponsor requirement to run on their own cluster.
- **Measured 2026-11-02** (`audit_events.duration_ms` since the 2026-10-31 wipe): detection p95 92 s (max 120 s, 22 runs), extraction p95 36 s, case analysis p95 26 s. Far under the deadline; re-measure once a large scanned statement has run.

**Kubernetes as a handover kit** (decided 2026-11-02, not started). Production stays on Cloud Run and no GKE cluster runs; the chart is what GLDB's platform team would take.
- **Why:** portability (Cloud Run is Google-only; a bank runs its own Kubernetes platform: GKE, EKS, AKS or OpenShift), and east-west segmentation Cloud Run can't express (NetworkPolicies: only Kong reaches api and audit-metrics; workers accept only Pub/Sub push).
- **First:** ask GLDB what its platform team deploys to; the answer is the sponsor trigger above, cited in the report.
- **Build:** a Helm chart with a values file per role (the sizes feed the deployment document's server, RAM and storage table), NetworkPolicies, Checkov on the chart; CI installs it on a `kind` cluster with the Pub/Sub emulator (as compose does) and runs a smoke check, including one call the policies must block.
- **Open:** how the pods reach Supabase and secrets on `kind` (CI has neither); a CNI that enforces NetworkPolicies.

## 11. Risks

- **Dual write** (database updated, publish lost): the sweeper.
- **Supabase Realtime** carries all live updates; its limits under load are untested.
- **Kong** is a single entry point: a wrong `Host` or CORS rule breaks the whole frontend; test with compose first.
- **One environment** until staging exists: the post-deploy check and automatic rollback are the safety net.
- **Shared Supabase:** local runs and the cloud see each other's cases and stuck documents. Anything that scans the whole database runs only in the cloud (the sweeper; a local run must name a case).
- **Scope:** staging, load testing and failure tests (section 10) all need time before November; the Helm chart (section 10) competes with them; running on GKE stays out of scope unless a trigger fires.

## 12. Running it

**Daily development (unchanged):** `cd backend && uvicorn app.main:app --reload --reload-dir app` and `cd frontend && npm run dev`. One process runs every role; no Docker, Pub/Sub or Kong needed.

**The split locally** (opt-in): `docker compose up --build` (stop uvicorn first; Kong takes :8000), then `npm run dev`. Containers: the Pub/Sub emulator plus `pubsub-init` (creates the topic and subscriptions, then exits), Kong, api, one `workers` container running the three workers (to save memory) and audit-metrics: about 3.5 GB. `--profile frontend` adds the nginx build on :5173. To debug the workers in the IDE: `PUSH_HOST=host.docker.internal PUSH_PORT=8001 docker compose up --build --scale workers=0`, then run uvicorn on :8001 from `backend/` with `ROLE=doc-worker,detection-worker,case-worker EVENT_TRANSPORT=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local`. The emulator has no OIDC or IAM, so `/push` takes unsigned requests locally.

**Walkthrough before a risky change** (real Supabase and Claude): upload a document (reaches `analyzed`), approve it (`detected`, findings appear), run case analysis (score and summary), read `GET /audit/events?case_id=<id>`; `docker compose restart workers` mid-detection (the run returns to `approved`, is redelivered, still reaches `detected`); stop and start audit-metrics (missed events appear); `docker compose run --rm pubsub-init python -m app.events.emulator_setup --poison` (retried, then dead-lettered).

**Deploying:** push to `main`. Manual rollback: `gcloud run services update-traffic <service> --to-revisions=<revision>=100 --region asia-southeast1`. Configuration changes go in Terraform (`main.tf`, then `apply`); a changed subscription needs `python -m app.events.types`, then `apply`.

**Troubleshooting**

| Symptom | Likely cause |
|---|---|
| Every request through Kong returns 404 from Google | Kong forwards the original `Host`; set `preserve_host: false` |
| A worker never receives messages; Pub/Sub shows 403 | The push service account lacks `roles/run.invoker` on that worker |
| The same message is processed again and again | The handler takes over 600 s or returns non-2xx; check its logs and the stage histogram |
| Browser shows a CORS error | The frontend origin is missing from Kong's CORS plugin |
| Live updates stop after an hour | Cloud Run's 60-minute WebSocket limit; the client reconnects by itself |
| A document stays "Processing stuck" | The sweeper should recover it within one sweep plus the limit; if not, check the worker's logs and the dead-letter subscription, then Retry |
| Google's own HTML 404 for `/healthz` | Cloud Run reserves paths ending in `z`; use `/health` |
| Events never reach a worker in compose | `pubsub-init` failed or ran early: `docker compose logs pubsub-init`, then `docker compose up pubsub-init` |
| Grafana: "plugin failed ... 404 loading react/jsx-runtime" | The BigQuery plugin is newer than Grafana supports; keep the versions in `deploy/grafana/Dockerfile` in step |

More problems met during setup and their fixes: `deploy/terraform/README.md`, "Problems met".
