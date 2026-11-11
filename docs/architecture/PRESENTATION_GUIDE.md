# The cloud architecture: a guide for presenting it

What we built for the cloud, how it works, and how to show it. The diagrams are in [`six-sevens-architecture.drawio`](six-sevens-architecture.drawio), one page per diagram:

1. System architecture
2. GCP infrastructure
3. Life of a document
4. Stuck work recovery
5. Observability and cost
6. CI and deploy

**Opening the diagrams:**
- **VS Code:** the "Draw.io Integration" extension (`hediet.vscode-drawio`) opens the file directly.
- **Browser:** app.diagrams.net → File → Open from → Device.
- **Slides:** export each page as PNG or SVG (File → Export as).
- **Changing them:** edit `build_diagrams.py` (next to the file) and run `python docs/architecture/build_diagrams.py docs/architecture/six-sevens-architecture.drawio` from the repo root. Edits made in draw.io are lost the next time it runs.

Details live in `CLOUD_ARCHITECTURE_PLAN.md` (design), `HOW_IT_WORKS.md` (behaviour) and `deploy/terraform/README.md` (operations).

---

## 1. The one-minute version

The fraud-detection backend used to be one FastAPI process doing everything. It's now **six services** that never call each other: they communicate only through **events on one Google Pub/Sub topic** and through shared state in Supabase.

1. **The officer uses the app.** An upload or an approval becomes an event.
2. **Workers pick up the events they subscribe to:**
   - doc-worker classifies and extracts;
   - detection-worker runs the fraud agents;
   - case-worker scores the case.
3. **Each worker publishes its result as another event.**
4. **audit-metrics stores every event,** which gives a complete audit trail for free.

It runs on **Cloud Run** (Stage 1), with everything defined in **Terraform**. **Kong** is the single front door. Deploys go through **GitHub Actions** without stored keys, and the system is observed through **Grafana, Managed Prometheus and Cloud Trace**. Stage 2 adds a Helm chart, tested in CI, so a bank can run the same services on its own Kubernetes platform.

## 2. What to show, in order (about 15 minutes)

| # | Show | Where | Point to make |
|---|---|---|---|
| 1 | Diagram page 1 | draw.io | Six services, one topic, no direct calls; workers are private |
| 2 | Upload a document and approve it; the case analysis starts on its own | the frontend URL | The app behaves as before, but each step is now an event |
| 3 | Cloud Run service list | Cloud Console → Cloud Run | Each service is deployed and scaled on its own (and scales to zero) |
| 4 | Pub/Sub subscriptions and their filters | Cloud Console → Pub/Sub | Each worker gets only its event types; failures go to the dead-letter topic |
| 5 | The case's **Claude Cost** tab | the case page → Claude Cost | What each document and the analysis cost, from the audit trail, and how much input the prompt cache served |
| 6 | One trace for that upload | Cloud Console → Trace explorer | One trace crosses api → Pub/Sub → doc-worker → detection-worker |
| 7 | Grafana: Pipeline, Agents, Cost | the Grafana URL (admin) | Stage timings, per-agent cost and cache hit ratio, estimated against billed cost |
| 8 | Diagram page 4, then the recovery row on the Pipeline dashboard | draw.io, Grafana | Stuck work recovers itself; the system is resilient, not just split |
| 9 | Diagram page 6, plus a deploy run in Actions | GitHub → Actions | Every push is linted, type-checked, tested and security-scanned; a deploy rolls back on its own |
| 10 | `deploy/terraform/` | the repo | Everything above is code |

**Optional failure demo** (prepare it beforehand; it takes up to about 17 minutes): start a detection, then deploy (or stop the detection-worker revision) while it runs. Show the document coming back to "detected" with nobody touching it. The audit trail shows `recovery.requested` then `document.recovered`.

## 3. The services

| Service | Public? | Does | Triggered by |
|---|---|---|---|
| **api** | via Kong | Reads, login, officer commands, uploads, live updates over WebSocket, case match | HTTP |
| **audit-metrics** | via Kong (`/audit`) | Stores every event; serves the audit trail and per-case cost; runs the sweeper | Every event (push) |
| **doc-worker** | private | OCR, classify, extract (LangGraph + Claude) | `document.uploaded`, `retyped`, `retry_requested`, `cancelled` |
| **detection-worker** | private | Lead agent plus 3 specialists over 13 fraud checks; writes findings | `document.approved`, detection retry and cancel |
| **case-worker** | private | Cross-document analysis, deterministic risk score, adverse-media search, summary | `case.analysis.requested` |
| **Kong** | public | The single entry point: CORS, rate limits, request ids; blocks `/push` and `/metrics` | HTTP |

Also deployed: the **frontend** (React build in nginx) and **Grafana**.

**One codebase, one Dockerfile.** Every backend service is the same code started with a different `ROLE`. Locally, `uvicorn` runs every role in one process with in-process events, so daily development is unchanged.

## 4. How a request flows (diagram page 3)

```mermaid
sequenceDiagram
    participant O as Officer (UI)
    participant A as api (via Kong)
    participant P as Pub/Sub
    participant D as doc-worker
    participant T as detection-worker
    participant C as case-worker
    participant M as audit-metrics
    O->>A: upload
    A->>P: document.uploaded
    P->>D: push
    D->>P: document.analyzed
    O->>A: approve
    A->>P: document.approved
    P->>T: push
    T->>P: document.detected
    T->>P: case.analysis.requested (the last document scanned)
    P->>C: push
    C->>P: case.analysis.completed
    P-->>M: every event is stored (audit trail)
```

The UI updates live: Supabase Realtime tells api a row changed, and api pushes a fresh snapshot over the open WebSocket.

## 5. How each cloud-native requirement is met

| Requirement | How |
|---|---|
| **Genuinely decoupled** | No service calls another. The contract is the event envelope on one topic. Each service can be deployed, scaled and failed on its own. |
| **Independent scaling** | Cloud Run per service: workers have 2 vCPU and 4 GiB and scale on pushes; api scales up to 3; everything scales to zero when idle. |
| **Resilience** | Pub/Sub redelivers, and dead-letters after 5 attempts. Handlers are idempotent with compare-and-swap status changes. A sweeper recovers stuck work. A deploy rolls back automatically when its checks fail. |
| **Infrastructure as code** | Terraform: services, IAM, secrets, Pub/Sub, scheduler, monitoring, alerts, billing export, Grafana. |
| **Secure delivery** | CI runs ruff, pyright (no suppressions allowed), pytest, Playwright, Checkov, dependency audits (pip-audit and npm audit every run, Snyk when dependencies change and weekly), and Terraform validation. Deploys use Workload Identity Federation, so no stored keys. |
| **Least privilege** | A service account per service; secrets readable only by the services that need them; workers callable only by Pub/Sub's signed identity. |
| **Observability** | Metrics from every service (Managed Prometheus), traces across services (Cloud Trace), four Grafana dashboards, email alerts, an audit trail of every action. |
| **Cost awareness** | Claude cost per agent, per document and per case; Cloud Run estimates live; actual GCP billing from the BigQuery export; a budget alert and a daily Claude spend alert. |

## 6. Questions to ask the professor

1. **Does Cloud Run with Pub/Sub count as "genuinely decoupled"** for the Cloud Native award, or does Kubernetes have to run, rather than ship as a Helm chart tested in CI?
2. **Is the event-driven design (one topic, filtered subscriptions, audit trail from events) the right story to lead with,** or should the demo lead with the infrastructure?
3. **Is the failure demo (self-recovery) worth the time slot** in the final presentation?

## 7. Questions you may be asked

| Question | Answer |
|---|---|
| Why Pub/Sub and not services calling each other? | Calls couple availability: if the worker is down, the api fails. With events, work waits in the subscription and is delivered when the worker is back. Detection takes 1-2 minutes, which shouldn't hold a request open. |
| Why one codebase for six services? | Shared models and repositories without a shared-library release process. The boundary is the event, not the repo. Each service still has its own image, scaling, identity and deploy. |
| What if a worker crashes mid-run? | Pub/Sub redelivers the message (ack deadline 600 s). If the run left a document stuck, the sweeper asks the owner to recover it, up to 3 times, then marks it failed so the officer can retry. |
| What if an event is processed twice? | Status changes are compare-and-swap, so the second attempt finds the status already moved and does nothing. The audit trail ignores a repeated event id. |
| Why not Kubernetes now? | Cloud Run gives the same separation for less operating effort, and measured load doesn't need more (detection p95 92 s against a 600 s deadline). For the bank, Stage 2 adds a Helm chart tested in CI, so its platform team can run the same services, with NetworkPolicies walling them off from each other. |
| How do you know what it costs? | Every Claude call is priced per agent and recorded per run; the Cost dashboard shows estimates live and Google's billed amount from the billing export. |
| What's not done? | A shared Anthropic rate limit across instances, the Stage 2 move, and the items in `PENDING_WORK.md`. |

## 8. Limits to be upfront about

- **All test documents are synthetic.** GLDB can't share real ones, so detection is tested on fraud we injected ourselves.
- **Cloud Run, not Kubernetes yet** (the open question above).
- **Some numbers are estimates:** Claude cost uses list prices, and the live Cloud Run cost is estimated. The billed row is exact but a few hours behind.
- **The `/dashboard` page still mixes in sample data:** real cases fill in sample values where a figure isn't recorded, and with no cases it shows sample cases. The Claude Cost tab on the case page is real data but a placeholder design.

## 9. Key facts at a glance

- **Region:** `asia-southeast1`. **Project:** `fyp-six-sevens`.
- **Services:** 5 backend roles (api, audit-metrics, doc-worker, detection-worker, case-worker; ingestion runs inside api), plus Kong, the frontend and Grafana. That's 8 Cloud Run services.
- **Messaging:** 1 topic and 4 filtered push subscriptions, plus a dead-letter topic and a sweep every 5 minutes.
- **Observability:** 4 Grafana dashboards and 4 alert policies.
- **Tests:** about 2,400 backend test functions (3,161 cases) and 276 Playwright tests, run in CI on every push.

## 10. The diagrams, page by page

Colours mean the same on every page (legend on pages 1 and 2):
- **purple** is Kong;
- **blue** is a public service, reached through Kong;
- **yellow** is a private worker;
- **orange** is a Pub/Sub topic;
- **green** is a data store;
- **grey dashed** is an outside service.

Dashed arrows are data reads and writes; solid arrows are requests and events.

**Page 1: System architecture.** The main picture; start here.
- **How to read it:**
  - **Top row:** the officer's path in. Browser → frontend → Kong → the two public services, api and audit-metrics. audit-metrics receives every event (the audit trail) and publishes `recovery.requested` when it finds stuck work.
  - **Middle:** the orange bar is the one Pub/Sub topic.
  - **Below it:** the three private workers. Each two-headed arrow is labelled with the events a worker receives ("in") and the event it publishes back ("out"). Their arrow to Supabase has two heads too: they read and write it.
  - **Around them:** Supabase on the left, Claude underneath, the dead-letter topic and public registries on the right. Cloud Scheduler drops a sweep tick onto the topic every 5 minutes.
- **Point to make:** there's no arrow from one service to another. Everything goes through the topic or Supabase, which is what "decoupled" means here.

**Page 2: GCP infrastructure.** Where everything runs.
- **How to read it:**
  - **Top band:** the management tools (Terraform, Grafana, IAM, billing, GitHub Actions).
  - **Big box:** the GCP project in Singapore. The Cloud Run services sit in one row, with the Pub/Sub bar under them. Each service has its own arrow to the bar: api publishes; audit-metrics and the workers receive pushes and publish back. Below that are the other managed services: Artifact Registry, Secret Manager, Monitoring, BigQuery, Trace, IAM.
  - **Right column:** Supabase (the workers read and write it), Anthropic (every worker's Claude calls) and the registries (detection-worker's lookups), all outside Google.
  - **Images:** "an image per role" means five build targets of one Dockerfile, sharing every layer. Only detection-worker's image adds the land-use index.
- **Point to make:** all of it is created by Terraform. Workers are private, there are no stored keys, and secrets are scoped per service (the Security note).

**Page 3: Life of a document.** One document from upload to case score, as a sequence of 22 numbered steps.
- **How to read it:** time runs top to bottom. Each column is a service, and each arrow is one step: an HTTP call, a published event, a push, or a database write.
  - **Dashed arrows are live updates:** Supabase Realtime tells api a row changed, and api pushes a fresh snapshot to the officer over the WebSocket (steps 8-9 and 21-22).
  - **Approving writes `approved` before publishing** (steps 11-12). If the publish fails, the status is put back.
- **Point to make:** every step between services is an event, and one trace in Cloud Trace follows the whole chain.

**Page 4: Stuck work recovery.** What happens when a run dies halfway.
- **How to read it:** left to right.
  1. Cloud Scheduler publishes a sweep every 5 minutes.
  2. The sweeper (inside audit-metrics) reads Supabase for work past its time limit.
  3. It publishes one `recovery.requested` event per stuck item, tagged with the worker that owns it.
  4. It's the same `domain-events` topic, not a new one. Each worker's subscription filters on the `owner` attribute, so each event reaches only its owner, which resets and reruns the work. Case analysis is the exception: it's marked failed instead, because a rerun costs Claude calls and the officer decides.
  5. Outcomes appear in the audit trail and on the dashboard, and an email goes out if recovery gives up after 3 tries.
- **Point to make:** the system heals itself without a database change; the audit trail is its memory. The note lists the other failure cases (redelivery, cancel across instances, a failed publish).

**Page 5: Observability and cost.** How we see what the system is doing and what it costs.
- **How to read it:** left to right.
  - **Left:** the sources. Every service and Kong expose `/metrics`, which the sidecar scrapes (a pull), and services send OpenTelemetry spans.
  - **Middle:** where they're stored. Managed Prometheus in Cloud Monitoring, Cloud Trace, the BigQuery billing export, and `audit_events`.
  - **Right:** where they're read. Grafana (four dashboards), email alert policies, and the app's Claude Cost tab. The tab reads through audit-metrics, which totals each run's cost from `audit_events`.
  - **Arrows show which way the data flows,** and the label says who starts it.
- **Point to make:** Claude cost is tracked per agent, per document and per case. Google's billed cost sits beside the live estimate.

**Page 6: CI and deploy.** The delivery pipeline.
- **How to read it:**
  - **Top row:** CI runs four jobs in parallel on every push and pull request: backend (lint, type checks, tests), frontend (lint, build, the Playwright suite), security scans (Checkov, pip-audit, npm audit, and Snyk when dependencies change) and Terraform checks. A docs-only push skips all of it.
  - **The gate:** when all four pass on a push to `main`, the deploy starts by itself. A pull request or another branch stops at CI.
  - **Bottom row:** the deploy (it can also be started by hand). Only one runs at a time, and one is never cancelled halfway. It authenticates without a stored key, builds 8 images (5 role targets of the one backend Dockerfile, plus Kong, frontend and Grafana), rolls them out workers first, then checks the gateway: pass finishes the deploy, fail rolls every service back.
- **Point to make:** it's continuous deployment, so every green change to `main` reaches production with no manual step, and a broken deploy undoes itself. The deploy only changes images; Terraform owns everything else.
