# Cloud architecture: what's left

Planned and open cloud work only. Delete an item once it's done. Updated 2026-11-14.

**Where it stands:** production runs on Cloud Run today. The cloud as built is summarised in `HOW_IT_WORKS.md` §9; operations and every problem met are in `deploy/terraform/README.md`; running the split locally is in `README.md`. Sections 1–7 and 12 moved there, so the numbers from 8 on are unchanged. Adding a service: `docs/ADDING_A_SERVICE.md`.

**Decided 2026-11-14:**
- **Production moves to GKE Autopilot**, and the Cloud Run deployment is removed once GKE is stable (§13–24). This is a Cloud Native award, so Kubernetes is the platform, not an afterthought. Load doesn't require it: detection's p95 is 92 s against a 600 s limit.
- **No staging environment.** It's designed (below, for the record) but not built: about 3–5 days of work before the deadline. Load tests run against the local compose stack with Claude stubbed; failure tests run on production after the move, on synthetic cases.
- **Front door:** `sixsevens.me` (the frontend), `api.sixsevens.me` (Kong), `grafana.sixsevens.me`, on one Google-managed certificate on the load balancer, which re-encrypts to the cluster. No plain-HTTP hop anywhere. The API's URL is fixed, so the frontend keeps its build-time URL: until phase 6, CI builds a second frontend image for GKE (`VITE_API_URL=https://api.sixsevens.me`).
- **No release approval in Cloud Deploy, ever:** a green push to `main` deploys, as today. The human checkpoint is the merge to `main`; every release is still recorded and one step to roll back.
- **Claude through Vertex AI** is kept but comes after phase 6 (§17). Vertex appears not to serve the Claude 5 models in `asia-southeast1` (only `global` and the US and EU multi-regions; confirm in Model Garden), so its case rests on no Anthropic key, Google IAM, billing and audit logs, not on processing in Singapore.

**What's left of the move:** the code, chart, Terraform and release pipeline are built (`HOW_IT_WORKS.md` §9); none of it is applied. Open: the phases (§17), run with `deploy/terraform/README.md` ("GKE"); after the first run on GKE, the Cloud Run panels' GKE versions in `build_dashboards.py` and the budget's new amount; then Vertex.

## 8. Open decisions

1. **Login:** keep `AUTH_USERS` (hashed now) as a stated limit, or move to Supabase Auth.

## 9. To do

- **Failure tests** (a deliverable), on production after the GKE move, on synthetic cases at a quiet time: kill a worker pod mid-run; flood uploads; stop audit-metrics; a poison message; a lost publish (set a document to `queued` without publishing; the sweeper should recover it). The lost publish writes to the shared database.
- **Shared Anthropic rate budget:** today each instance caps itself.
- **Kong behaviour test in CI** (routes, CORS, rate limits, WebSocket upgrade): CI only checks the config parses.
- **Optional:** ingestion out of api.

**Staging (designed, not built):** a second GCP project and Supabase project, the same Terraform with its own variables; schema as migrations; a frontend runtime config so one image serves both; no Anthropic key, with `langgraph/_anthropic.py` returning canned replies; `main` → staging → smoke test → the same images to production. Revisit after the deadline, or if GLDB asks for one.

## 10. Stage 2 (revised after the supervisor's feedback, 2026-10-26; updated 2026-11-14)

**Feedback in short:** the architecture is fine. A handful of users doesn't justify heavier infrastructure without a measured need. Let load testing drive decisions; spend the time on testing, productionisation, usability and business value. `docker compose up` liked; on-prem accepted as hard; a deployment document (server, model, RAM, storage) requested. The GKE move goes beyond that advice deliberately, for the award; the measured load figures stay in the write-up.

**Steps, in order:**
1. **GKE production** (§13–24).
2. **Claude through Vertex AI** (Google identity and billing, regional processing); check which Claude models Vertex offers in `asia-southeast1` first. Can be done with the move.
3. **Load tests as code** (k6 or Locust) against the local compose stack, written up as a capacity report. **No Claude calls** (stubbed) and **no Supabase writes** (writes go to an in-memory store).
4. **Tune autoscaling** (KEDA, pod sizes) from the results, recording before and after.
5. **Failure tests** on production (§9).
6. **Service-level targets** with burn-rate alerts.
7. **Cost guardrails:** shared rate budget, a spend cap per case, budget alerts (the cluster costs money idle, §21).
8. **Self-host kit** (`docker compose up` install and the Helm chart) and one Supabase backup-and-restore drill.

**Not doing:** staging (above); nightly evals or model-comparison runs (they spend tokens).

## 11. Risks

- **Database written, publish lost:** the sweeper recovers it.
- **Supabase Realtime** carries all live updates; untested under load.
- **Kong** is a single entry point: a wrong `Host` or CORS rule breaks the frontend; test with compose first.
- **One environment:** with no staging, the post-deploy check, the phased move (§17) and rollback are the safety net.
- **Shared Supabase:** local and cloud see each other's data; whole-database scans (the sweeper) run only in the cloud.
- **Time:** the GKE move competes with testing and the write-up before the deadline. If it runs out, production stays on Cloud Run and the chart is shown running on GKE for the demo only (with its own topic, its sweeper off).

---

# Kubernetes deployment (decided, not built)

How Six Sevens moves from Cloud Run to GKE. Decided 2026-11-14; nothing here is built yet. Sections 13–24.

**The rule:** every phase is watched on the agents' sensibility checks (built, §13) and, once built, the officer-verdict accuracy trend.

## 13. First: the evaluation harness

*In plain words:* the agents are officer-facing, and the officer decides every case. So the harness has one job: catch an agent going off track before the officer relies on it. The sensibility checks on every agent run are built (`backend/app/evals/`, the trace view and the Agents dashboard; how they work: `HOW_IT_WORKS.md` §3). Accuracy comes from the officers' own verdicts (§13.3), not from a golden set. No golden set, no nightly evals, no tokens spent on evals.

### 13.1 Open on the sensibility checks

- **Tests:** `PENDING_TESTS.md` rows 36, 37 and 39 (the rest are written: `tests/test_evals.py`), then one real run per run kind (a payslip, a bank statement, a case) to see the checks on a live trace.
- **Tune from real runs:** the overconfidence gap (0.3); the expected-checks table (`evals/detection.py`: today only Content Validation's numerical checks on income documents and bank statements, and Digital Authenticity's metadata check); the field-name rules for dates and amounts (`evals/extraction.py`). A check that warns on most runs is noise: change or drop it.
- **Extraction rerun:** records no `extraction` step, so its field checks don't run (only its second look is checked).

### 13.2 What the checks cannot tell you

- A run can follow the expected path, give a sensible answer, and still miss fraud. The checks don't measure accuracy.
- Officers mostly confirm or dismiss what the system flagged. They rarely see what it missed, so misses are under-measured. This is a stated limitation, along with the fact that all test data is synthetic.
- With no golden set, a prompt change can't be rerun on fixed cases and compared. Changes are watched through the checks and the officer-verdict trend instead.

### 13.3 Accuracy from officer verdicts (not built)

*In plain words:* when a case is finalised, the officers' verdicts on each agent's findings are counted against what the agent said. This is the accuracy figure. It's human-labelled and comes from real use, so no answer key is needed.

| Part | Design |
|---|---|
| Which cases | Every case created with reviews on (`cases.reviews_required`) |
| When | When the case is finalised (`case.finalised`): every verdict is in |
| Who writes it | audit-metrics: one handler, idempotent on the case id |
| What it stores | One `evaluation_results` row per case: per check and document type, true positives, false alarms, wrong dismissals, missed issues where an officer adds one; prompt version, models, cost, time. Counts only, no personal data. Cascades from the case |
| Figures | Precision and recall per check and document type (weighted to income documents and bank statements), dismissal accuracy, cost and time, by prompt version and model. Recall is only as good as what officers add, so it is labelled as such |
| Where it shows | The dashboard's accuracy panel reads stored rows (also removes the `list_reviews` load); one Grafana panel per prompt version |
| Use | A trend to watch, not a gate. A drop after a prompt or model change prompts a person to look; nothing blocks automatically |
| Never | Changes a score, severity or verdict, or reaches a prompt |
| Size | M: one table (dated SQL file), one handler, one route, one panel |

### 13.4 Rules

- **Evals only watch.** They never change a score, severity, verdict, status or finding, never reach a prompt, and never block a run.
- **No model marks another model** (cost, and "code verifies" is the design rule).
- **No golden set, no nightly evals, no scheduled token spend.** Evals run inside normal use and cost nothing extra per run.
- **No personal data** in metrics or the trace's check results (field names, never values).

## 14. Why GKE, and what it shows

The move isn't forced by load (detection p95 92 s against Cloud Run's 600 s push limit), so it's justified by what Kubernetes demonstrates and what a bank would run:

| Reason | What it shows |
|---|---|
| Portability | The same Helm chart runs on GLDB's own cluster, on-premises or in any cloud; Cloud Run is Google-only |
| Network isolation | Services are internal (no public URLs); only the load balancer and Kong face the internet |
| No request-time ceiling | Pull workers have no 600 s push limit, so a long case analysis can't be cut off |
| Event-driven scaling | Workers scale on undelivered messages (KEDA), down to zero |
| Supply-chain control | Only signed images from our registry are admitted |
| Operations a bank recognises | GitOps-style releases, namespaces per service, network policies, pod security |

The measured figures stay in the write-up, so the move reads as a deliberate design choice, not a load fix.

## 15. Target design

| Concern | Choice | Why |
|---|---|---|
| Cluster | GKE Autopilot, regional, `asia-southeast1`, one cluster (prod) | No nodes to manage; survives a zone loss |
| Namespaces | One per role, plus `gateway`, `frontend`, `observability` | A boundary each; dashboards already key on `namespace` |
| Front door | `sixsevens.me` on Cloud Load Balancing (Gateway API, global external) with a Google-managed certificate (Certificate Manager, DNS authorisation) → Kong; services internal (ClusterIP) | One front door, by the network; the gateway key (backend `gateway.py`) stays until phase 6 as a second guard |
| WebSockets | A `GCPBackendPolicy` with `timeoutSec: 3600` on Kong's backend (the default 30 s would cut every case WebSocket); a `HealthCheckPolicy` on `/health` | Live updates through the load balancer |
| Edge protection | Cloud Armor on the load balancer: OWASP rules and per-address rate limits before traffic reaches Kong | A bank-facing app gets a web application firewall; Kong keeps the per-officer limits |
| Events | Same topic; each role's existing subscription switches from push to pull in place (no second subscription: it would collect every message and process each twice) | Private services can't take push; removes the 600 s limit |
| Scaling | KEDA on undelivered messages, 0 to max; Kong, api, frontend and audit-metrics (sweeps every 5 min) at least 1. From zero, expect 2–4 minutes (the backlog metric lags 1–2 min, and Autopilot may add a node) against Cloud Run's 10–30 s, close to the sweeper's 5-minute limit for queued work. Workers carry `safe-to-evict: "false"` and a termination grace longer than a detection run | Idle workers cost nothing |
| Database | Supabase outside; egress via Cloud NAT on a static IP | Private nodes need NAT to reach Supabase and Anthropic. Supabase's network restrictions cover direct Postgres and pooler connections only, and the backend uses only the HTTPS APIs (`supabase_client.py`), so the allow-list does **not** make a leaked key useless |
| Secrets | Secret Manager through the GKE add-on, mounted as files; the chart's start command exports them as the variables the app (`config._str`) and Kong (`render.sh`) read. A rotated secret needs a restart | No secrets in Helm values |
| Identity | Workload Identity, one account per role (as today) | No keys |
| Claude | The Anthropic API for the move; Vertex AI after phase 6, with each worker's own Google identity (the Claude 5 models appear to be served only from `global` and the US and EU multi-regions) | No Anthropic key to hold or leak; Google IAM, billing and audit logs |
| Images | Artifact Registry with Artifact Analysis (vulnerability scanning on push); cosign signs each image and attaches its SBOM, and a Binary Authorization attestation (same Cloud KMS key) admits it: the enforcing policy checks attestors, not cosign signatures. KEDA's images are allow-listed | Only our software runs, and every image has a parts list |
| Releases | Cloud Deploy, rendering the Helm chart, with a rollout record and one-step rollback; no approval (decided); its verify step runs the gateway checks | Every production change is recorded and reversible, without running a deploy tool inside the cluster |
| Monitoring | Managed Prometheus (PodMonitoring instead of sidecars), the same Grafana (in `observability`), Cloud Logging, Cloud Trace; the GKE security posture dashboard. The Cloud Run panels (`service_name`, `run_googleapis_com:*`, the cost panels) need GKE versions in `build_dashboards.py`; app metrics keep working because namespaces are named after the roles | Mostly what runs today; posture is free with GKE |
| Scheduled work | Cloud Scheduler's sweep (as today) | Nothing new |
| Backups | Cluster holds no state; Supabase point-in-time recovery matters | No cluster backup tool |
| Environments | dev (laptop, compose or kind) and prod | No staging (decided) |

**Not used, on purpose:** Cloud SQL, Firestore, Cloud Storage and Memorystore (Supabase Postgres and Storage, and no Redis, are settled); a service mesh (network policies suffice); GKE Enterprise (not needed for one cluster).

## 16. The Helm chart

- **Chart:** one chart, a template per kind; per-role values generated from `deploy/terraform/roles.json`. Values files: default, kind, prod.
- **Network:** default-deny per namespace; gateway → api and audit-metrics only; Kong takes traffic only from Google's front ends and health checks (`35.191.0.0/16`, `130.211.0.0/22`, also Kong's trusted IPs for the real client address); workers take no inbound traffic except the metrics scrape; egress only to DNS, Supabase, Vertex AI or Anthropic, Pub/Sub, registries.
- **Labels:** chosen once. Deployment selectors are immutable, so a changed label means deleting the Deployment.
- **KEDA** is installed once per cluster (a pinned `helm install`, in `deploy/terraform/README.md`), not by this chart.
- **Pods:** non-root (uid 10001), read-only root, no privilege escalation, CPU and memory limits; a pod disruption budget for Kong and api.
- **CI on kind:** Pub/Sub emulator, stub Supabase, Calico; smoke test (health, one event per worker, one call the policies must block); Checkov on the rendered chart. About 6–8 minutes, on chart changes and weekly.

## 17. Migration

Each worker's subscription switches from push to pull in place, so only one platform gets a role's messages at a time; rollback is switching back. Each phase is watched on the sensibility checks: a jump in warnings or failures holds the next phase.

| Phase | Moves | Check before next | Rollback |
|---|---|---|---|
| 1 | Cluster, NAT, load balancer, Cloud Armor (preview) and the chart, every worker at 0; subscriber rights granted, no subscription changed | Pods healthy; the smoke test's health and must-block calls against the cluster (it publishes nothing: the topic is shared, and Cloud Run's workers would take the events) | `gke_enabled = false`, apply |
| 2 | audit-metrics' events (the sweeper moves with them: only the pull subscriber gets sweeps). Cloud Run audit-metrics keeps serving `/audit` to Cloud Run's Kong until phase 5 | Every event stored; sweeps run | Push back |
| 3 | case-worker | p95 and failures within 10% of Cloud Run | Push back |
| 4 | detection-worker, then doc-worker (`sql/2026-11-14_cancel_requested_at.sql` applied first) | Same; no more recoveries or dead letters | Push back per role |
| 5 | api, Kong, frontend, Grafana at `sixsevens.me`. Both front doors run side by side (api only publishes, so two are safe); CORS gains the new origin | The gateway checks pass through the new load balancer | Officers use the `run.app` URL again |
| 6 | Remove the Cloud Run services, the push identity and its grants after a stable week (Terraform) | — | Re-apply them from git history |

A phase's Terraform change must plan the subscription as "update in-place", never "replace". Then the failure tests (§9), then Vertex AI.

## 18. CI/CD on GKE

Actions builds and pushes to Artifact Registry, signs each digest with cosign and attaches its SBOM (syft), creates the Binary Authorization attestation with the same KMS key, then creates a Cloud Deploy release that renders the chart with the image digests. Cloud Deploy rolls it out without approval and records it; its verify step runs the gateway checks; rollback is its previous release. Terraform owns the cloud pieces; the chart owns everything inside the cluster. Binary Authorization starts in dry-run, then enforces. The checks that block today (Checkov, pip-audit, npm audit) keep blocking. Until phase 6 the same images also roll out to Cloud Run (`deploy.yml`), with the GKE job behind a repository variable.

## 19. Tools: in and out

| Tool | Why | Cost | When |
|---|---|---|---|
| Cloud Deploy | Every production release recorded and reversible (MAS TRM) | Small per active pipeline; check the calculator | With the move |
| KEDA | Idle workers cost nothing | Free | With the move |
| Binary Authorization, cosign, SBOM, Artifact Analysis | Only our images run; a parts list for vulnerability reviews | Free to small | With the move |
| Cloud Armor | Web application firewall and edge rate limits | Per policy, rule and request; check the calculator | With the move |
| Secret Manager add-on | Secrets in one audited place | Included | With the move |
| NetworkPolicy | Services can't talk to each other unless allowed | Included | In the chart |

**Left out:** Argo CD (Cloud Deploy covers releases without running a tool in the cluster), a service mesh, Kyverno or Gatekeeper (Autopilot covers it), Velero (no state to back up), Falco (needs privileged pods; the posture dashboard covers basics), Litmus (scripted failure tests instead).

## 20. Open security items on GKE

| Security item (`CODEBASE_AUDIT.md`) | On GKE |
|---|---|
| 6.2 every role holds the database key | Still true, and the NAT allow-list doesn't help: Supabase's network restrictions don't cover the HTTPS APIs the backend uses |
| 6.6 pinning by tag | Closed by §18 (digests, Binary Authorization) |
| 6.1 any service can publish any event | Unchanged |

Kong's real-IP settings (`deploy/kong/render.sh`) need the load balancer's ranges as trusted IPs. The gateway key (backend `gateway.py`) stays until phase 6, as a second guard beside the network policies; drop it then or keep it.

## 21. Cost (rough; check Google's calculator before quoting)

| | Monthly |
|---|---|
| Helm chart on kind in CI | $0 |
| Production on GKE Autopilot (Kong, api, frontend, Grafana, audit-metrics always on; workers at 0) plus NAT, load balancer and Cloud Armor | roughly $200–260, plus per document; GKE's free-tier credit may cover one cluster's management fee, so check the calculator before quoting |
| Today on Cloud Run | about $0 idle, cents per document |

The cluster costs money while idle, unlike Cloud Run. The budget alert stays; delete the cluster after the assessment if nobody takes it over.

## 22. Risks

| Risk | Mitigation |
|---|---|
| No staging: production is the first cluster | The phased move (§17), one role at a time, each reversible; the kind test on every chart change |
| The chart drifts from what Cloud Run ran | Values generated from `roles.json` (CI checks) |
| A message processed twice during a switch | Handlers are idempotent; one subscription per role |
| Idle cost | KEDA to zero for workers; budget alert; delete the cluster when it's no longer needed |
| A wrong network policy or Cloud Armor rule breaks a flow | kind test has a must-pass and a must-block call; Cloud Armor rules start in preview mode |
| A slow start from zero trips the sweeper | Watch recoveries in phase 4; keep doc-worker at 1 for demos |
| Nobody to run it after handover | Autopilot, Cloud Deploy, a short runbook |
| Data leaves Singapore | Regional cluster and registry; Claude calls leave Singapore either way (Anthropic API, or Vertex's `global`), a stated limitation |
| The move doesn't finish before the deadline | Stop at the last stable phase; the remaining roles stay on Cloud Run (§11) |
| Too few officer verdicts to read accuracy | Show the count with each figure; a person decides below 30 cases |

## 23. Why each piece (in the bank's terms)

| Feature | Problem it solves | Expectation it meets |
|---|---|---|
| Evaluation harness | Catches agents going off track on every run; accuracy from officer verdicts | MAS FEAT (monitoring); TRM change management |
| Helm chart | GLDB can run it on its own platform | MAS outsourcing (exit, portability) |
| Pull workers + KEDA | No 10-minute ceiling; free when idle | TRM capacity |
| Load balancer + Cloud Armor, internal services | Nobody reaches a service directly; common web attacks stopped at the edge | TRM access control; OWASP |
| NetworkPolicies | A hijacked part can't reach the others | TRM segmentation |
| Workload Identity | No keys to leak | TRM least privilege |
| Secret Manager add-on | Secrets in one audited place | TRM; PDPA |
| Cloud NAT, static IP | All egress from one known address, which the bank's firewalls can allow-list | TRM network security |
| Cloud Deploy | Every release recorded and reversible; the merge to `main` is the approval | TRM change management |
| Signing, Binary Authorization, Artifact Analysis | Only our software runs, with known parts | TRM supply chain; OWASP A08 |
| Regional, Singapore only | Survives a zone outage; the cluster, registry and stored data stay in Singapore | TRM; PDPA; MAS outsourcing |
| Claude through Vertex AI (after phase 6) | No model key to leak; calls billed and audited in Google Cloud | TRM least privilege; MAS outsourcing |

## 24. Assumptions (decided 2026-11-14)

We have little access to GLDB's technical side, so these stand until the bank says otherwise.

- **GLDB's platform:** unknown. GKE stands in for the bank's own Kubernetes: the chart is what its platform team would install.
- **The demo line:** "Production runs on Kubernetes (GKE Autopilot). We have no access to GLDB's infrastructure, so we run the same Helm chart on our own Google Cloud project; the bank's platform team would install it on theirs, on-premises or in their cloud, with no code changes."
- **Ownership after handover:** GLDB's platform team, if it adopts the chart. Otherwise the cluster is deleted after the assessment.
- **Claude through Vertex AI** is in scope, after phase 6.
