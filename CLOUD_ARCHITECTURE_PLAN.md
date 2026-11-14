# Cloud architecture: what's left

Planned and open cloud work only. Delete an item once it's done. Updated 2026-11-14.

**Where it stands:** the cloud as built is summarised in `HOW_IT_WORKS.md` §9. Operations and every problem met are in `deploy/terraform/README.md`; running the split locally is in `README.md`. Sections 1–7 and 12 moved there, so the numbers from 8 on are unchanged. Adding a service: `docs/ADDING_A_SERVICE.md`. Kubernetes (planned): §13–24.

## 8. Open decisions

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

**Kubernetes** (decided 2026-11-02): production stays on Cloud Run; a Helm chart is built as a handover kit and tested on `kind` in CI. Measured 2026-11-02: detection p95 92 s, extraction 36 s, analysis 26 s, far below the 600 s limit. Triggers, design and cost: §13–24. Ask GLDB what its platform team runs.

## 11. Risks

- **Database written, publish lost:** the sweeper recovers it.
- **Supabase Realtime** carries all live updates; untested under load.
- **Kong** is a single entry point: a wrong `Host` or CORS rule breaks the frontend; test with compose first.
- **One environment** until staging: the post-deploy check and rollback are the safety net.
- **Shared Supabase:** local and cloud see each other's data; whole-database scans (the sweeper) run only in the cloud.
- **Time:** staging, load and failure tests compete with the Helm chart before the deadline.

# Kubernetes deployment (planned only)

How Six Sevens would move from Cloud Run to Kubernetes, and when. Nothing here is built. Written 2026-11-13; builds on §10 (the Helm handover kit). Sections 13–24.

**The rule:** every later step is watched on the agents' sensibility checks (built, §13) and, once built, the officer-verdict accuracy trend. Production stays on Cloud Run until a measured trigger fires. The Helm chart is built anyway, as the kit GLDB's platform team would take.

## 13. First: the evaluation harness

*In plain words:* the agents are officer-facing, and the officer decides every case. So the harness has one job: catch an agent going off track before the officer relies on it. The sensibility checks on every agent run are built (`backend/app/evals/`, the trace view and the Agents dashboard; how they work: `HOW_IT_WORKS.md` §3). Accuracy comes from the officers' own verdicts (§13.3), not from a golden set. No golden set, no nightly evals, no tokens spent on evals.

### 13.1 Open on the sensibility checks

- **Tests:** `PENDING_TESTS.md` rows 34–39, then one real run per run kind (a payslip, a bank statement, a case) to see the checks on a live trace.
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

## 14. When to move

| Stage | Trigger (any one) | Today | Until then |
|---|---|---|---|
| A. Helm kit, tested on kind in CI | Already decided | — | Cloud Run serves everything |
| B. Staging on GKE | GLDB confirms it runs Kubernetes, or the assessment needs a live cluster | Neither answered | Staging on Cloud Run |
| C. Production on GKE | A stage's p95 passes 480 s (push limit 600 s) | Detection 92 s, extraction 36 s, analysis 26 s | Push delivery |
| | Load above what Cloud Run handles at current `max_instances` | Not measured | Raise `max_instances` |
| | Cloud Run costs over 1.2× GKE Autopilot for two months | ~$0 idle | Scale to zero |
| | GLDB needs its own cluster or network rules Cloud Run can't express | Not asked | Kong and IAM |

## 15. Target design

| Concern | Choice | Why |
|---|---|---|
| Cluster | GKE Autopilot, regional, `asia-southeast1`, one per environment | No nodes to manage; survives a zone loss |
| Namespaces | One per role, plus `gateway`, `frontend`, `observability` | A boundary each; dashboards already key on `namespace` |
| Ingress | One Google load balancer → Kong; services internal (ClusterIP) | One front door, by the network rather than Cloud Run's gateway key (backend `gateway.py`) |
| Events | Same topic; workers switch from push to pull | Private services can't take push; removes the 600 s limit |
| Scaling | KEDA on undelivered messages, 0 to max | Keeps scale to zero |
| Database | Supabase outside; egress via Cloud NAT on a static IP, allow-listed | A leaked key is useless elsewhere |
| Secrets | Secret Manager add-on | No secrets in Helm values |
| Identity | Workload Identity, one account per role (as today) | No keys |
| Claude | Through Vertex AI, in `asia-southeast1` if the models are offered there (else the nearest region that has them), with each worker's own Google identity; the public Claude API as the fallback. Same move on Cloud Run first (§10 step 6) | No Anthropic key to hold or leak; Google billing; regional processing |
| Monitoring | Managed Prometheus, same Grafana, Cloud Trace | Nothing new |
| Backups | Cluster holds no state; Supabase point-in-time recovery matters | No cluster backup tool |
| Environments | dev (laptop), staging (Claude stubbed, no Anthropic key), prod | The chart refuses staging with a key |

## 16. The Helm kit (Stage A)

- **Chart:** one chart, a template per kind; per-role values generated from Terraform's `local.roles`. Values files: default, kind, staging, prod.
- **Network:** default-deny per namespace; gateway → api and audit-metrics only; workers take no inbound traffic; egress only to DNS, Supabase, Anthropic, Pub/Sub, registries.
- **Pods:** non-root (uid 10001), read-only root, no privilege escalation, CPU and memory limits.
- **CI on kind:** Pub/Sub emulator, stub Supabase, Calico; smoke test (health, one event per worker, one call the policies must block); Checkov on the rendered chart. ~6–8 min, on chart changes and weekly.

## 17. Migration (Stages B and C)

Each worker's subscription switches push → pull in place, so only one platform gets a role's messages; rollback is switching back. Each phase is also watched on the sensibility checks and the accuracy trend (§13): a jump in warnings or failures after a move holds the next phase.

| Phase | Moves | Check before next | Rollback |
|---|---|---|---|
| 1 | Staging, all roles | Failure tests pass; a green week | Delete the cluster |
| 2 | audit-metrics (sweeper moves with it) | Every event stored; sweeps run | Push back; sweeper back |
| 3 | case-worker | p95 and failures within 10% | Push back |
| 4 | detection-worker, then doc-worker | Same; no more recoveries or dead letters | Push back per role |
| 5 | api, Kong, frontend; DNS switched | Deploy gateway check passes | DNS back |
| 6 | Remove Cloud Run after 30 stable days | — | — |

## 18. CI/CD on GKE

Actions builds, signs (cosign) and attaches an SBOM, then opens a PR bumping the image digest. Argo CD syncs the cluster from the repo; rollback = revert the PR. Terraform owns the cloud pieces; Helm via Argo CD owns everything inside the cluster. Only Argo CD deploys; Binary Authorization admits only signed images.

## 19. Tools: in and out

| Tool | Why | Cost | When |
|---|---|---|---|
| Argo CD | Every production change is a reviewed commit (MAS TRM) | Free; a day | Stage B |
| KEDA | Idle cost near zero | Free | Stage B |
| Binary Authorization, cosign, SBOM | Only our images run; parts list for vulnerability reviews | Free; a day | Stage B |
| Secret Manager add-on | Secrets in one audited place | Included | Stage B |
| NetworkPolicy | Services can't talk to each other unless allowed | Included | Stage A |

**Left out:** a service mesh (NetworkPolicies suffice; too much to run), Kyverno/Gatekeeper (Autopilot covers it), Velero (no state to back up), Falco (needs privileged pods; GKE's posture dashboard covers basics), Litmus (scripted failure tests instead).

## 20. Open security items on GKE

| Security item (`CODEBASE_AUDIT.md`) | On GKE |
|---|---|
| 6.2 every role holds the database key | Still true; the NAT allow-list limits where it works |
| 6.6 pinning by tag | Closed by §18 (digests, Binary Authorization) |
| 6.1 any service can publish any event | Unchanged |
| Cross-instance detection cancel: a cancel that lands on another instance is seen only through the audit trail, so not while audit-metrics is down | Do it with the move: a `cancel_requested_at` column on `documents` that the detection run polls (`workers/detection.py`, `_cancel_requested_since`), so a cancel needs only the database. More pods make a cross-instance cancel likelier. M |

Kong's real-IP settings (`deploy/kong/render.sh`) need the load balancer's ranges as trusted IPs on GKE, and the gateway key (backend `gateway.py`) can go once the services are internal.

## 21. Cost (rough; check Google's calculator before quoting)

| Stage | Monthly |
|---|---|
| A. Helm kit on kind in CI | $0 |
| B. Staging on GKE (public services always on, workers at 0) | $100–130 |
| C. Production on GKE (two replicas of public services) | $200–260 plus per document |
| Today on Cloud Run | ~$0 idle, cents per document |

Below the triggers, Cloud Run is cheaper.

## 22. Risks

| Risk | Mitigation |
|---|---|
| The chart drifts from Cloud Run | Values generated from `local.roles`; kind test on every chart change |
| Staging calls Claude by mistake | No key in staging; chart refuses one |
| A message processed twice during a switch | Handlers are idempotent; one subscription per role |
| Idle cost surprises | KEDA to zero; budget alerts; Stage B only on a trigger |
| A wrong network policy breaks a flow | kind test has a must-pass and a must-block call; staging first |
| Supabase can't allow-list the NAT IP on our plan | Confirm before Stage B |
| Nobody to run it after handover | Autopilot, Argo CD, a short runbook |
| Data leaves Singapore | Regional cluster and registry; Claude via Vertex AI in-region |
| Too few officer verdicts to read accuracy | Show the count with each figure; a person decides below 30 cases |

## 23. Why each piece (in the bank's terms)

| Feature | Problem it solves | Expectation it meets |
|---|---|---|
| Evaluation harness | Catches agents going off track on every run; accuracy from officer verdicts | MAS FEAT (monitoring); TRM change management |
| Helm kit | GLDB can run it on its own platform | MAS outsourcing (exit, portability) |
| Staging without Claude | Changes tested without spend or real data | TRM; PDPA |
| Pull workers + KEDA | No 10-minute ceiling; free when idle | TRM capacity |
| One gateway, internal services | Nobody bypasses rate limits | TRM access control |
| NetworkPolicies | A hijacked part can't reach the others | TRM segmentation |
| Workload Identity | No keys to leak | TRM least privilege |
| Secret Manager add-on | Secrets in one audited place | TRM; PDPA |
| NAT allow-list | Leaked database key useless elsewhere | TRM; PDPA |
| Argo CD | Every change reviewed and reversible | TRM change management |
| Signing and Binary Authorization | Only our software runs | TRM supply chain; OWASP A08 |
| Regional, Singapore only | Survives a zone outage; data stays in Singapore | TRM; PDPA; MAS outsourcing |
| Claude through Vertex AI | No model key to leak; calls billed and audited in Google Cloud; processing in-region where offered | TRM least privilege; PDPA; MAS outsourcing |

## 24. Assumptions (decided 2026-11-14)

We have little access to GLDB's technical side, so these stand until the bank says otherwise.

- **GLDB's platform:** assumed unknown. The Helm kit (Stage A) is built and tested on kind as the handover; Stages B and C wait for a trigger in §14.
- **No paid test cluster** (~$100–130 a month idle) unless the bank or the assessment asks for a live one.
- **Ownership after handover:** GLDB's platform team, if it adopts the kit. Until someone owns a cluster, production stays on Cloud Run.
- **Claude through Vertex AI** (§10 step 6, §15) is in scope, after checking which Claude models Vertex offers in `asia-southeast1`.
