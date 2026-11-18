# Presenting the cloud architecture

A plain-language guide to how Six Sevens runs in the cloud, so you can explain it without notes. Read it top to bottom once; after that, each section stands on its own.

**The diagrams** are in [`six-sevens-architecture.drawio`](six-sevens-architecture.drawio), one page each: 1 System architecture, 2 GCP infrastructure, 3 Life of a document, 4 Stuck work recovery, 5 Observability and cost, 6 CI and deploy. They show the finished picture: GKE only (Cloud Run removed in phase 6), with Claude through the Anthropic API.

- **Open them:** the VS Code "Draw.io Integration" extension, or app.diagrams.net → File → Open from → Device. For slides: File → Export as PNG or SVG.
- **Change them:** edit `build_diagrams.py`, then run `python docs/architecture/build_diagrams.py docs/architecture/six-sevens-architecture.drawio` from the repo root. Anything you change by hand in draw.io is lost the next time the script runs.

More detail, if you need it: `HOW_IT_WORKS.md` (what the app does and how it's built, the cloud included), `CLOUD_ARCHITECTURE_PLAN.md` (open cloud work), `deploy/terraform/README.md` (how to operate it).

---

## 1. The whole thing in 30 seconds

Think of a kitchen with a ticket rail.

- The **waiter** (the `api` service) takes the officer's order: "upload this file", "approve this document".
- The waiter doesn't cook. They write a **ticket** (an *event*) and clip it to the **rail** (Pub/Sub, one topic called `domain-events`).
- Each **cook** (a *worker*) only takes the tickets meant for them, does the job, and clips a new ticket saying "done".
- A **clerk** (`audit-metrics`) photocopies every ticket into a logbook. That logbook is our audit trail.
- The **pantry** (Supabase) is where everything is kept: the case data and the uploaded files.

The key idea: **the services never talk to each other directly.** They only leave tickets on the rail and read and write the pantry. If one cook is off sick, the tickets wait on the rail until they're back. Nothing is lost and nobody else stops working.

All of it runs on **Kubernetes** (Google's managed version, *GKE Autopilot*) in Singapore, behind **one front door** (a Google load balancer with a firewall, then Kong).

---

## 2. The words you need

| Word | What it means here, plainly |
|---|---|
| **Service** | One program with one job. We have five backend ones (below), plus Kong, the website and Grafana |
| **Event** | A short message saying something happened: `document.uploaded`, `document.approved`, `case.analysis.completed` |
| **Pub/Sub** | Google's message rail. Services publish events to it; other services subscribe and pull the ones they want |
| **Topic / subscription** | The topic is the rail (we have one). A subscription is one service's filtered view of it ("only show me `document.approved`") |
| **Worker** | A service that has no web address. It only pulls events and works on them |
| **Kubernetes** | Software that runs many containers on many machines, restarts the ones that crash, and adds copies when busy |
| **GKE Autopilot** | Google runs the Kubernetes machines for us; we just say what to run |
| **Container / image** | Our code packed with everything it needs, so it runs the same on any machine. The *image* is the packed file; a *container* is it running |
| **Pod** | One running copy of a service inside Kubernetes |
| **Namespace** | A walled-off section of the cluster. Each service has its own |
| **Helm chart** | The recipe that tells Kubernetes what to run and how (`deploy/helm/`) |
| **Terraform** | Code that creates the cloud pieces (the cluster, Pub/Sub, the load balancer, secrets) so nothing is clicked by hand (`deploy/terraform/`) |
| **Kong** | The gatekeeper in front of our API: rate limits, logins and so on |
| **KEDA** | Adds or removes worker copies depending on how many events are waiting |
| **Supabase** | Hosted Postgres (the database) plus file storage, and *Realtime*, which tells us when a row changes |
| **Idempotent** | Doing it twice has the same effect as doing it once. Matters because Pub/Sub may deliver an event twice |

---

## 3. Follow one document from start to finish

This is diagram page 3. Tell it as a story.

1. **The officer uploads a bank statement.** The browser sends it through the front door to `api`.
2. `api` saves the file in Supabase Storage, adds a row to the database ("uploaded, queued"), and publishes **`document.uploaded`**. It answers the officer straight away; it doesn't wait for the work.
3. **doc-worker** pulls that event. It reads the file (text, or OCR for a scan), asks Claude what kind of document it is, and pulls out the fields (names, amounts, dates). It saves them and publishes **`document.analyzed`**.
4. The officer's screen updates by itself. Supabase Realtime tells `api` the row changed, and `api` pushes a fresh view over a WebSocket (a connection that stays open).
5. **The officer checks the fields and approves.** `api` marks the document approved and publishes **`document.approved`**. If that publish fails, the approval is undone, so a document is never left approved with nobody working on it.
6. **detection-worker** pulls it. A lead agent and three specialist agents run 13 forensic checks (fonts, layout, images, metadata, arithmetic, government registries) and write a fraud report. It publishes **`document.detected`**.
7. When the **last** document in the case is scanned, the case analysis starts by itself: **`case.analysis.requested`**.
8. **case-worker** pulls it, compares the documents with each other, works out the risk score (with plain code, not AI), searches the news for the company, writes a summary, and publishes **`case.analysis.completed`**.
9. All the way through, **audit-metrics** has stored every one of those events. That is the audit trail, and it also gives us the timing and the Claude cost of every run.

The one-line version: **every step is an event, and one upload can be traced end to end across every service.**

---

## 4. The pieces, one by one

| Piece | Can the internet reach it? | Its job, plainly | What wakes it up |
|---|---|---|---|
| **Load balancer + Cloud Armor** | Yes, the only thing that can | The front door. Handles HTTPS for `sixsevens.me`, `api.sixsevens.me` and `grafana.sixsevens.me`. Cloud Armor is a firewall that blocks common attacks and too many requests from one address | Any web request |
| **Kong** | Only from the load balancer | The gatekeeper for the API: rate limits (for example 20 logins a minute), CORS, a request id for tracing, and it blocks internal paths like `/metrics` | Web requests |
| **api** | Through Kong | Logins, reading cases, officer actions, uploads, live updates | Web requests |
| **audit-metrics** | Through Kong (`/audit` only) | Stores every event; serves the audit trail and costs; runs the "sweeper" that finds stuck work | Every event |
| **doc-worker** | No | Reads, classifies and extracts each document | `document.uploaded`, retry, retype, cancel |
| **detection-worker** | No | The detection agents: a lead plus three specialists | `document.approved`, detection retry and cancel |
| **case-worker** | No | The case analysis, risk score, news check and summary | `case.analysis.requested` |
| **Frontend** | Through the load balancer | The website the officers use (React, served by nginx) | Web requests |
| **Grafana** | Through the load balancer | Our dashboards | Web requests |

**One codebase, five services.** All five backend services are the same code. A setting called `ROLE` tells each copy which job to do. On a laptop, one program runs all five at once and passes events in memory (no Pub/Sub needed). In the cloud, each runs separately.

**Why the workers have no web address:** nobody needs to call them. They only pull from Pub/Sub. Something that can't be reached can't be attacked.

---

## 5. What stops it from breaking

Say these in order. They're layered: if one misses, the next catches it.

1. **Events wait.** If a worker is down, its events sit in Pub/Sub until it comes back.
2. **A crash means a retry.** A worker only says "done" (*acknowledges*) when it has finished. If it crashes halfway, it never acknowledged, so Pub/Sub hands the event to another copy.
3. **Doing it twice is harmless.** Every status change is a *compare-and-swap*: "change approved to detecting, but only if it's still approved". A repeated event finds the status already moved and does nothing. The audit trail ignores an event id it has already stored.
4. **Poison messages are set aside.** An event that fails 5 times goes to a *dead-letter* topic for a human to read, instead of blocking everything.
5. **The sweeper.** Every 5 minutes, Cloud Scheduler wakes the sweeper, which looks for anything stuck too long (5 minutes waiting, or 10 minutes running) and asks the worker that owns it to redo it. After 3 tries it gives up and marks the document failed, so an officer can retry. A stuck case analysis is just marked failed and never rerun automatically, because rerunning costs Claude calls and that's the officer's decision.
6. **Bad releases roll back.** After every deploy, an automatic check runs through the real front door. If it fails, the previous version comes back.
7. **A runaway case can't run up the bill.** Once a case has spent $5 on Claude, no more runs start on it until an administrator raises the cap.
8. **If the database is lost.** We rehearsed it: copy the database, restore it somewhere empty, check every table came back (`dr_drill.py`). The cluster holds no data, so everything else is rebuilt from code. The copy and restore took 10 seconds, and every row came back; the target for being fully back, reconnecting included, is an hour.

---

## 6. How code gets to production

This is diagram page 6.

1. Someone pushes to the `main` branch on GitHub.
2. **CI checks everything**, three jobs in parallel:
   - backend: style (ruff), types (pyright), the tests;
   - frontend: lint, types, build, browser tests (Playwright);
   - security: Checkov (infrastructure rules), pip-audit and npm audit (known-vulnerable libraries), gitleaks (a password or key pasted into the code), Terraform checks.
   If anything fails, nothing deploys.
3. **Build and sign.** It builds 8 images, each with a parts list (an *SBOM*) and a signed record of where it was built (*provenance*: which commit and which run), and signs each one with our key.
4. **Release (GitOps).** CI writes those exact images into git, and Argo CD, inside the cluster, pulls the Helm chart from git and makes the cluster match it. Git is the record of what runs; every sync is in Argo CD's history and can be rolled back in one step.
5. **Only our images run.** *Binary Authorization* refuses to start any image we didn't sign. Even if someone got into the cluster, they couldn't run their own code there.
6. **No passwords for the deploy.** GitHub signs in to Google with a short-lived token (Workload Identity Federation), so there's no long-lived key that could leak.

---

## 7. How we watch it, and what it costs

This is diagram page 5.

- **Metrics:** every service reports numbers (requests, errors, how long each stage takes, Claude calls and cost per agent). Google's Managed Prometheus collects them.
- **Dashboards:** five in Grafana. The Overview is the one to show: is it up, is work flowing, how fast, what it costs, anything wrong, on one screen. Services, Pipeline, Agents and Cost hold the detail.
- **Traces:** one upload shows as one timeline across every service in Cloud Trace, because each event carries a trace id.
- **Uptime checks:** Google opens the website and the API from Asia, Europe and the US every 5 minutes.
- **Promises we measure (SLOs):** 99% of API requests succeed, and 95% of detections finish within 3 minutes, over 28 days. The 1% or 5% we're allowed to miss is the *error budget*; we get an email if it's being used up too fast.
- **Alerts:** email for the site being down, the error budget burning, dead letters, API errors over 5%, daily Claude spend, and recovery giving up.
- **Cost per case:** the case page's **Claude Cost** tab shows what each document and the analysis cost, and what prompt caching saved. The Cost dashboard adds the cloud's share per case (Google's bill split by service, divided by cases finished), beside the Claude cost per case.
- **Load test:** a script plays many officers at once against the real services, with the database and Claude replaced by stand-ins, so it costs nothing (`deploy/loadtest/`). It shows where work starts to queue.

---

## 8. Vendor lock-in (your professor's question)

**What it means:** how hard it would be to move off a company's product if we had to: because of price, a policy change, or because the bank uses something else.

**Our answer in one breath:** the core runs on open standards (containers, Kubernetes, Postgres, Helm), and the Google-only pieces sit at the edges, each behind a switch or a single file. The real lock-in is the AI model, and we contain it by never letting the model decide the outcome.

### Piece by piece

| Piece | Who owns it | How locked in | Why, and what it would take to leave |
|---|---|---|---|
| **Our containers** | Open standard (Docker/OCI) | Not locked | The same images run on a laptop, on any cloud, or on the bank's own servers |
| **Kubernetes + the Helm chart** | Open source | Low | Every Kubernetes runs the same chart. We prove it: CI runs the whole chart on **kind** (Kubernetes on a laptop, no Google at all) on every chart change. The Google-only bits are switches in the chart's values: the load balancer, Workload Identity, how secrets are mounted, Managed Prometheus, and KEDA's Pub/Sub trigger. `values-kind.yaml` turns them all off |
| **Pub/Sub** | Google | Medium | Only two files touch it: `events/bus.py` (sending) and `events/pull.py` (receiving), plus a local-testing helper (`emulator_setup.py`). The rest of the code only knows "an event". Locally it isn't used at all. Moving to Kafka or RabbitMQ means rewriting those two files, the subscription filters and KEDA's trigger. The services and their events stay the same |
| **Supabase** | Supabase (open source) | Low to medium | The database is plain Postgres, so the data moves anywhere. Supabase itself is open source and can be self-hosted. If we dropped Supabase completely: the data access (written against Supabase's API) and the live updates (Realtime) would need rewriting, and file storage lives in one file (`storage_service.py`) |
| **Claude (Anthropic)** | Anthropic | **High: the real lock-in** | The prompts are written and tuned for Claude, and we use Claude features such as prompt caching and web search. Getting the same Claude from another provider (for example AWS Bedrock) is a small change. Switching to a different AI model means re-tuning and retesting every prompt |
| **Terraform** | Open tool, Google resources | Medium | The tool works on any cloud, but our files describe Google resources, so another cloud means new Terraform files. The design (what we create and why) carries over |
| **Google extras** | Google | Low (each is replaceable) | Cloud Armor → Kong plugins or another web firewall; Argo CD already runs anywhere; Binary Authorization → a Kubernetes admission policy (for example Kyverno) checking the same signatures; Cloud Trace → Jaeger (our tracing already speaks the open OTLP format); Cloud Scheduler → a Kubernetes CronJob; Artifact Registry → any container registry |
| **Kong, Grafana, Prometheus format, OpenTelemetry, FastAPI, LangGraph, React** | Open source | Not locked | Run anywhere |

### How we keep the AI lock-in from hurting

- **The AI never decides the outcome.** The risk score is plain code (`case_risk.py`). The AI finds and explains things; code checks every answer before it's used. A different model could slot in without changing how a case is scored.
- **The model is a setting** (`LANGGRAPH_SONNET_MODEL`, `LANGGRAPH_HAIKU_MODEL`), so switching versions is one change.
- **Every reply is checked against a fixed shape** (`llm_contracts.py`). A new model that answers differently is caught, not silently trusted.
- **Honest limit:** we chose depth over portability here, on purpose. Tuning for one model family gave better results than writing prompts for "any model".

### Why we accepted some Google lock-in

- We're a small team with free-trial credits and a deadline. Managed services (Pub/Sub, Autopilot, Cloud Armor) meant no servers to look after.
- We kept the lock-in **at the edges**, never in the business logic. The fraud checks, agents, scoring and events don't know they're on Google.
- The bank's likely path is to run **our Helm chart on its own Kubernetes**. That's exactly the part that's portable.

**If asked "so could GLDB run this tomorrow?":** "The application, yes. They'd run the same images and Helm chart on their own Kubernetes, turn off the Google switches, and point it at their own Postgres and message broker. The two real pieces of work would be the Pub/Sub adapter (two files) and, if they wanted a different AI provider, re-tuning the prompts."

---

## 9. What to show (about 15 minutes)

| # | Show | Where | What to say |
|---|---|---|---|
| 1 | Diagram page 1 | draw.io | "Services never call each other. One topic. The workers have no web address." |
| 2 | Upload a document and approve it; the analysis starts on its own | the app | "Looks like one app, but every step was an event between separate services." |
| 3 | Workloads and namespaces | Console → Kubernetes Engine → Workloads | "Each service in its own namespace, scaling on its own." |
| 4 | Pub/Sub subscriptions | Console → Pub/Sub | "Each worker only gets its own events. Failures go to the dead-letter topic." |
| 5 | The case's **Claude Cost** tab | case page | "We know what every document and analysis cost, and what caching saved." |
| 6 | One trace for that upload | Console → Trace explorer | "One upload, one timeline, across every service." |
| 7 | Grafana: Status, then Pipeline, Agents, Cost | grafana.sixsevens.me | "Is it up, are we keeping our promises; then stage times, cost per agent and per case, the real bill." |
| 8 | Diagram page 4, then the recovery panel | draw.io, Grafana | "Stuck work fixes itself." |
| 9 | Diagram page 6 and a release | GitHub → Actions, Console → Cloud Deploy | "Every push is tested and scanned; only signed images run; a failed check rolls back." |
| 10 | `deploy/terraform/` and `deploy/helm/` | the repo | "All of it is code. Nothing was clicked together by hand." |

**Optional failure demo** (rehearse it first): start a detection, then delete the detection-worker pod in the console. The event was never acknowledged, so Pub/Sub gives it to the replacement pod, and the audit trail shows the run again.

---

## 10. Questions you may get, and short answers

| Question | Answer |
|---|---|
| Why events, not services calling each other? | If services call each other, one being down takes the others down. With events, work just waits. And a detection that takes a minute or two doesn't hold a web request open |
| Why Kubernetes and not something simpler like Cloud Run? | Load didn't force it: detection's slowest 5% take about 92 seconds, well within Cloud Run's limit. We chose it because the same chart runs on a bank's own cluster, everything can be private, pull workers have no time limit, and we control which software runs |
| Why one codebase for five services? | They share the same data models without us publishing a library. The boundary is the event. Each service still has its own image, scaling, identity and release |
| What if a worker crashes halfway? | It never said "done", so Pub/Sub redelivers the event. If something is still stuck, the sweeper asks its owner to redo it, up to 3 times, then marks it failed for the officer |
| What if an event arrives twice? | Status changes only happen if the status is still what we expect, so the repeat does nothing. The audit trail ignores ids it has already stored |
| How do you know only your code runs? | Every image is signed in CI with a parts list, and Binary Authorization refuses anything unsigned |
| How do you know what it costs? | Every Claude call is priced and recorded per run, and the Cost dashboard shows Google's bill beside it, split per case |
| How much load can it take? | The load test (§7): 10 officers uploading 30 documents in one minute all finished in 11 minutes with no failures, and the website never slowed. Detection is the bottleneck: one copy clears about 3 documents a minute, so on GKE KEDA adds a second when work waits. Details: `HOW_IT_WORKS.md` §9 |
| How do you know it's reliable? | Two measured promises (SLOs) with alerts when we're missing them, uptime checks from three continents, and a rehearsed database restore |
| Could a bug burn through the Claude budget? | A $5 cap per case stops new runs, and a daily spend alert covers everything |
| Aren't you locked into Google? | §8. Short version: the core is portable and proven on kind; the Google parts are at the edges; the real lock-in is the AI model, and the model never decides the score |
| What isn't done? | A shared Anthropic rate limit across copies, a staging environment (decided against), and the list in `PENDING_WORK.md` |

---

## 11. Limits to say before anyone asks

- **All test documents are made up.** GLDB can't share real ones, so real fraud may look different.
- **Claude calls leave Singapore** (the Anthropic API). The cluster and the image registry stay in Singapore.
- **Claude cost uses list prices.** The real bill is exact but a few hours behind.
- **One cluster, no staging.** To save money, most services run one copy; a bank would run at least two.

---

## 12. Numbers to remember

- Google Cloud project `fyp-six-sevens`, region `asia-southeast1` (Singapore), one regional GKE Autopilot cluster.
- 8 workloads: 5 backend services, Kong, the frontend and Grafana, each in its own namespace.
- 1 topic, 4 filtered subscriptions (one per worker and audit-metrics), 1 dead-letter topic, a sweep every 5 minutes.
- 13 detection checks, 1 lead and 3 specialist agents.
- 5 Grafana dashboards; 2 SLOs (99% API, 95% detections within 3 minutes); uptime checks from 3 regions every 5 minutes.
- $5 Claude cap per case; database restore target 1 hour.

---

## 13. The diagrams, page by page

**Colours:** purple is the front door (load balancer, Kong); blue is an internal service; yellow is a private worker; orange is the Pub/Sub topic; green is where data is stored; grey dashed is an outside service; white with a blue edge is a Google Cloud service. Dashed arrows are data being read or written; solid arrows are requests and events.

- **Page 1, System architecture** (start here). Top: browser → load balancer with Cloud Armor → Kong → api and audit-metrics (the website goes straight from the load balancer). Middle: the one topic. Below: the three workers, each arrow labelled with the events it pulls and publishes; KEDA scales them. Around the edge: Supabase, Claude, the dead letters, the registries, the scheduler. *Point to make:* there's no arrow from one service straight to another.
- **Page 2, GCP infrastructure.** Management tools on top; the load balancer; the Singapore cluster with a namespace per service; Pub/Sub under it; Cloud NAT, the one exit for traffic to Supabase, the registries and Anthropic; the managed services along the bottom (Scheduler, Artifact Registry, Binary Authorization, Secret Manager, Monitoring, BigQuery, Trace). *Point to make:* one public way in, everything else private, and no Google keys anywhere (the Anthropic key lives in Secret Manager).
- **Page 3, Life of a document:** 22 numbered steps, top to bottom (§3 above tells it as a story). Dashed arrows are the live screen updates (steps 8–9 and 21–22). Approval saves "approved" before publishing and undoes it if the publish fails (steps 11–12). *Point to make:* every step is an event, and it's one trace from start to end.
- **Page 4, Stuck work recovery:** scheduler → the sweeper finds overdue work → one `recovery.requested` per item, on the same topic, filtered to the service that owns it → the owner redoes it (a case analysis is marked failed instead, since redoing costs Claude calls) → the outcome goes in the audit trail, with an email after 3 failed tries. *Point to make:* it heals itself, with nothing changed by hand.
- **Page 5, Observability and cost:** where the data comes from (metrics, traces, uptime checks from 3 regions) → where it's kept (Managed Prometheus, Cloud Trace, BigQuery billing by namespace, `audit_events`) → who reads it (Grafana's five dashboards with the Overview first, the SLOs and their burn-rate alerts, the Claude Cost tab, the $5 per-case cap). *Point to make:* we see whether it's up and keeping its promises, and the cost per agent, document and case, beside Google's bill.
- **Page 6, CI and deploy:** the three CI jobs side by side (backend, frontend, security), plus the Helm chart's own checks when the chart changes. The security job includes gitleaks (secrets in new commits). Green on `main` starts the deploy: keyless sign-in, 8 images with a Trivy scan (a fixable high finding in our own images stops it), sign and attest with an SBOM and SLSA provenance, commit the exact images (by digest) to git, Argo CD syncs the chart from git into the cluster, a check through the load balancer, and rollback to the last good sync if it fails. Page 2 shows Argo CD in its own namespace. *Point to make:* only signed images reach production, git says exactly what runs, and every release is recorded and reversible.
