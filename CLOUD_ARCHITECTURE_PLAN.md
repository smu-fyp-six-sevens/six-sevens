# Cloud architecture

How the backend is split into services on Google Cloud, why, and what's left. Updated 2026-11-13.

**Where it stands:** live on Cloud Run in project `fyp-six-sevens` (`asia-southeast1`) since 2026-10-22; every green push to `main` deploys. One GCP project and one Supabase project, shared by local runs and the cloud; staging is planned (§9). Operations and every problem met: `deploy/terraform/README.md`. Adding a service: `docs/ADDING_A_SERVICE.md`. Diagrams: `docs/architecture/`. Kubernetes: `KUBERNETES_DEPLOYMENT_PLAN.md`.

## 1. Rules

The award asks for a genuinely decoupled architecture, not a monolith.

- Services talk **only** through Pub/Sub events and Supabase. No service calls another.
- One repo, one Dockerfile with a build target per role; `ROLE` picks what runs. Locally one `uvicorn` runs every role with in-memory events.
- Split only where scaling, failure or permissions differ.
- Stateless, configured from the environment; each worker is a `handle(event)` function behind a push adapter (a pull adapter later).

| Cloud-native criterion | Shown by |
|---|---|
| Loose coupling | Events only (§2–3) |
| Independent scaling | Each role scales alone (§2) |
| Resilience | Redelivery, dead letters, idempotent handlers, the sweeper, automatic rollback (§3, 5, 6) |
| Observability | Metrics, Grafana, Cloud Trace, audit trail, alerts (§7) |
| Secure delivery | CI/CD with Checkov and Snyk, Terraform, keyless GitHub sign-in (§6) |
| Least privilege | One account and secret set per service; private workers (§6) |
| Cost control | Scale to zero, budget and Claude-spend alerts, cost per document and case (§7) |

## 2. Services

| Service | Public | Receives | Does | Size |
|---|---|---|---|---|
| **api** | via Kong | HTTP | Reads, login, officer commands, uploads (ingestion), WebSocket, case match, notes | 1 CPU, 1 GiB, max 3 |
| **doc-worker** | no | `document.uploaded`, `retyped`, `retry_requested`, `cancelled`, recoveries | OCR, classify, extract | 2 CPU, 4 GiB, 2 at once, max 3 |
| **detection-worker** | no | `document.approved`, detection retry/cancel, recoveries | Lead + 3 specialists, 13 checks, findings | 2 CPU, 4 GiB, 2 at once, max 3; zoning index |
| **case-worker** | no | `case.analysis.requested`, recoveries | Case analysis, risk score, summary, adverse-media search | 1 CPU, 2 GiB, max 2 |
| **audit-metrics** | via Kong (`/audit`) | every event | Stores events in `audit_events`; serves the audit trail and costs; runs the sweeper | 1 CPU, 1 GiB, max 2 |
| **kong** | yes | HTTP | The single entry point (§4) | |
| **frontend** | yes | HTTP | React build in nginx | |
| **grafana** | yes (login) | HTTP | Dashboards | max 1 |

Workers accept calls only from Pub/Sub's push identity; audit-metrics checks the push token itself. Everything scales to zero by default (`min_instances` in `terraform.tfvars`).

## 3. Events and state

One topic, `domain-events`, plus `domain-events-dead-letter`. Each role has a filtered push subscription; filters come from `backend/app/events/types.py` (export with `python -m app.events.types`; max 256 bytes, immutable, so a change replaces the subscription). Messages carry ids only; Supabase holds the state. The envelope is also the `audit_events` row.

- **At-least-once:** handlers tolerate repeats; status changes are compare-and-swap; a repeated event id isn't stored twice.
- **An officer action that starts work** (approve, retype, analyse) fails with a 502 if the publish fails (a failed approval is undone). Audit-only actions use `events.record`, which never fails the request.
- Kong's request id and a W3C `traceparent` travel with each message, so one upload is one trace.

**One writer per transition:** api (documents up to `queued`, officer actions); doc-worker (`queued` → `analyzed`/`failed`/`cancelled`, fields); detection-worker (`approved` → `detected`, reports, findings); case-worker (case findings, `case_analysis_runs`, `risk_scores`); audit-metrics (`audit_events`). Recovery is done by the stage's owner; the sweeper only publishes. The case stage refresh is debounced in api and flushed before each response (Cloud Run throttles CPU after it).

## 4. Gateway (Kong)

DB-less Kong (`deploy/kong/`), routes rendered from `kong.yml.tmpl` at start.

| Path | Goes to |
|---|---|
| `/audit*` | audit-metrics |
| `/auth/login` (20/min), `/documents/upload-batch` (60/min) | api, rate-limited per client address |
| retries, retype, analysis run (10/min, 60/h); on-demand checks (30/min, 300/h) | api, rate-limited per officer (their token) |
| `/push*`, `/metrics` | blocked (404) |
| everything else, including the WebSocket | api |

**Only through Kong:** api and audit-metrics are public on their own URLs, so Kong adds `X-Gateway-Key` (secret `gateway-secret`) to every request, and both refuse any request without it (403), except `/health`, Pub/Sub's `/push` and `/metrics` from the container's own sidecar (`backend/app/gateway.py`). Not checked locally.

Gotchas: Cloud Run routes by `Host`, so Kong mustn't preserve it and must use HTTPS upstream; CORS only in Kong (`CORS_IN_APP=false`), never `*`; auth stays in each service.

## 5. Stuck work and recovery

Pub/Sub redelivery covers a worker dying mid-request; the sweeper covers the rest (a lost publish, a dead letter, a run that never finished).

- Cloud Scheduler sends `sweep.requested` every 5 minutes; audit-metrics runs the sweeper (`backend/app/sweeper.py`). Off locally; `python -m app.sweeper --once --case <id>` runs it for one case.
- **Limits:** uploaded/queued 5 min; processing/extracting/detecting 15 min; approved with a type but never started (no report, no error) 15 min; a running case analysis 15 min.
- It publishes `recovery.requested` to the stage's owner; at most 3 attempts in 24 h. doc-worker reruns (gives up as `failed`); detection-worker reruns (gives up as approved with an error); case-worker marks the run failed (no automatic rerun: Claude calls are the officer's call).
- **Cancel across instances:** detection polls the audit trail every 5 s; the document pipeline polls its own status.
- **Socket tickets** are HMAC-signed, valid 10 s, accepted by any api instance.

## 6. Delivery and security

**CI** (`ci-cd.yml`, PRs and pushes to `main`, not docs-only): backend (ruff and pyright on `app` and `tests`, pytest with coverage), frontend (lint, build, Playwright), security (Checkov; pip-audit and `npm audit` high every run; Snyk only when lock files change, weekly or by hand, non-blocking until its quota resets; Terraform fmt/validate), infrastructure (Kong config parses, compose files valid, dashboard PromQL parses with promtool; on a PR, the images build). All blocking except Snyk for now.

**Deploy** (`deploy.yml`, after CI on `main` or by hand; one at a time):
1. Build each image once (skipping any the commit already has).
2. Record what each service is serving.
3. Roll out the workers and audit-metrics, then api, Kong, frontend, Grafana.
4. Check through Kong (no LLM, no writes): `/health` answers, a protected route gives 401, `/push` and `/metrics` blocked, workers refuse unsigned POSTs, api and audit-metrics refuse a direct call (403), CORS, frontend and Grafana up.
5. Any failure rolls every service back.

**Security:** one account per service; each secret readable only by the services that need it (Anthropic key: the three workers; token key and gateway key: the public services; the gateway key also Kong); keyless GitHub sign-in; non-root containers with health checks.

**Cost:** scale to zero; budget alert; Claude spend per agent, document and case; registry cleanup keeps the 5 newest images and nothing older than 7 days.

## 7. Observability

- **Metrics** at `/metrics` (`backend/app/metrics.py`): HTTP, events, stage times, Claude calls, tokens, cost, cache, detection outcomes, Supabase calls, stuck work. Never labelled with document or case ids.
- **Scraping:** Managed Prometheus sidecar per service, every 30 s; the service is the `namespace` label (Cloud Run's own metrics use `service_name`).
- **Grafana** (12.4.10 with BigQuery plugin 3.4.6; bump together): Services, Pipeline, Agents, Cost dashboards, generated by `deploy/grafana/build_dashboards.py` (edit that, not the JSON). The Cost dashboard reads Google's billed cost once the billing export is set.
- **Alerts:** dead letters, API 5xx over 5%, daily Claude spend, recovery given up.
- **Cost per case:** `GET /audit/cases/{id}/cost` and the case's Claude Cost tab (including what prompt caching saved).
- **Locally:** `docker compose --profile observability up prometheus grafana jaeger` (Grafana admin/admin on :3000, Jaeger on :16686).

## 8. Decisions

**Locked** (changing one touches every service): the event envelope and names, one topic with filtered subscriptions, the ownership rules, a Dockerfile target per role, Kong as the only entry, handler plus adapters.

**Settled:** push delivery (detection takes 1–2 min, well inside 600 s); ingestion in api; Grafana on Cloud Run; uvicorn + `npm run dev` by default, compose optional; tests stub Claude and Supabase; injection tool only in uvicorn mode; no Redis until needed.

**Open:**
1. **Award check:** confirm the Cloud Native assessment accepts Kubernetes as a tested Helm chart rather than a running cluster.
2. **Login:** keep `AUTH_USERS` as a stated limit, or move to Supabase Auth.
3. **Staging:** promote automatically or after approval; Grafana in staging; who approves; smoke test per merge or nightly.

## 9. To do

- **Failure tests** (a deliverable): kill a worker mid-run; flood uploads; stop audit-metrics; a poison message; a lost publish (set a document to `queued` without publishing; the sweeper should recover it).
- **Shared Anthropic rate budget:** today each instance caps itself.
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.
- **Optional:** ingestion out of api; the frontend reading Kong's URL at runtime (needed for staging).

**Staging** (planned, not started): a second GCP project (`fyp-six-sevens-staging`) and a second free Supabase project, same Terraform with its own variables and state.
1. **Schema as migrations** (`supabase/migrations/`), staging first.
2. **Frontend runtime config** (`/config.js` from an env variable), so one image serves both.
3. **Terraform environments** (`staging.tfvars`, `production.tfvars`); the identity condition also requires the GitHub environment.
4. **Create the project**, apply, fill secrets with **no Anthropic key**, share production's image registry.
5. **No Claude on staging:** a stub setting makes `langgraph/_anthropic.py` return canned replies. Staging then proves the wiring, not Claude; API-side breaks are caught by the on-demand live check (`PENDING_WORK.md`).
6. **Pipeline:** `main` → staging → gateway check and smoke test → same images to production (automatic, or after a GitHub approval).
7. **Nightly on staging:** the full path on synthetic documents plus the failure tests (also keeps the free Supabase project awake).
8. **Terraform in the pipeline:** plan on PRs, apply to staging on merge, to production behind approval.

Cost: ~$0 idle, cents per run.

## 10. Stage 2 (revised after the supervisor's feedback, 2026-10-26)

**Feedback in short:** the architecture is fine. A handful of users doesn't justify heavier infrastructure without a measured need. Let load testing drive decisions; spend the time on testing, productionisation, usability and business value. Staging approved; `docker compose up` liked; on-prem accepted as hard; a deployment document (server, model, RAM, storage) requested.

**Steps, in order** (6 can run alongside):
1. **Staging and promotion** (§9).
2. **Load tests as code** (k6 or Locust) against staging, written up as a capacity report. **No Claude calls** (stubbed) and **no Supabase writes** (writes go to an in-memory store).
3. **Tune autoscaling** from the results, recording before and after.
4. **Failure tests on staging**, automated nightly.
5. **Service-level targets** with burn-rate alerts.
6. **Claude through Vertex AI** (Google identity and billing, regional processing); check the models are offered in-region first.
7. **Cost guardrails:** shared rate budget, a spend cap per case, budget alerts.
8. **Self-host kit** (`docker compose up` install) and one Supabase backup-and-restore drill.

**Not doing:** nightly evals or model-comparison runs (they spend tokens).

**Kubernetes** (decided 2026-11-02): production stays on Cloud Run; a Helm chart is built as a handover kit and tested on `kind` in CI. Measured 2026-11-02: detection p95 92 s, extraction 36 s, analysis 26 s, far below the 600 s limit. Triggers, design and cost: `KUBERNETES_DEPLOYMENT_PLAN.md`. Ask GLDB what its platform team runs.

## 11. Risks

- **Database written, publish lost:** the sweeper recovers it.
- **Supabase Realtime** carries all live updates; untested under load.
- **Kong** is a single entry point: a wrong `Host` or CORS rule breaks the frontend; test with compose first.
- **One environment** until staging: the post-deploy check and rollback are the safety net.
- **Shared Supabase:** local and cloud see each other's data; whole-database scans (the sweeper) run only in the cloud.
- **Time:** staging, load and failure tests compete with the Helm chart before the deadline.

## 12. Running it

**Daily:** `cd backend && uvicorn app.main:app --reload --reload-dir app` and `cd frontend && npm run dev`. No Docker needed.

**The split locally:** `docker compose up --build` (stop uvicorn; Kong takes :8000), then `npm run dev`. Containers: Pub/Sub emulator plus `pubsub-init`, Kong, api, one `workers` container, audit-metrics (~3.5 GB). `--profile frontend` adds nginx on :5173. **Debug workers in the IDE:** `PUSH_HOST=host.docker.internal PUSH_PORT=8001 docker compose up --build --scale workers=0`, then uvicorn on :8001 from `backend/` with `ROLE=doc-worker,detection-worker,case-worker EVENT_TRANSPORT=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local`. The emulator has no auth, so `/push` takes unsigned requests locally.

**Before a risky change** (real Supabase and Claude): upload → `analyzed`; approve → `detected`; wait for the analysis; read `GET /audit/events?case_id=<id>`. Then `docker compose restart workers` mid-detection (it should still reach `detected`); stop and start audit-metrics (missed events appear); `docker compose run --rm pubsub-init python -m app.events.emulator_setup --poison` (retried, then dead-lettered).

**Manual rollback:** `gcloud run services update-traffic <service> --to-revisions=<revision>=100 --region asia-southeast1`. Config changes go in Terraform.

| Symptom | Likely cause |
|---|---|
| Every request through Kong gets Google's 404 | Kong forwards the original `Host`; `preserve_host: false` |
| A worker gets no messages; Pub/Sub shows 403 | Push account lacks `roles/run.invoker` on it |
| The same message is processed again and again | Handler takes over 600 s or returns non-2xx |
| CORS error in the browser | Frontend origin missing from Kong's CORS plugin |
| Live updates stop after an hour | Cloud Run's 60-minute WebSocket limit; the client reconnects |
| A document stays "Processing stuck" | Should recover within a sweep plus the limit; else check worker logs and dead letters, then Retry |
| Events never reach a worker in compose | `pubsub-init` failed: `docker compose logs pubsub-init`, then `docker compose up pubsub-init` |
