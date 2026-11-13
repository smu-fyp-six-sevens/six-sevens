# Kubernetes deployment plan

How Six Sevens would move from Cloud Run to Kubernetes, and when. Planned only: nothing here is built. Written 2026-11-13; builds on `CLOUD_ARCHITECTURE_PLAN.md` (§10: the Helm handover kit).

**The rule:** build the evaluation harness first, so every later step can show detection didn't get worse. Production stays on Cloud Run until a measured trigger fires. The Helm chart is built anyway, as the kit GLDB's platform team would take.

## 0. First: the evaluation harness

*In plain words:* a scorecard of how well the agents did on each finished case, marked against the officer's verdicts. No model calls.

| Part | Design |
|---|---|
| Which cases | Every case created with reviews on (`cases.reviews_required`) |
| When | When the case is finalised (`case.finalised`): every verdict is in |
| Who writes it | audit-metrics: one handler, idempotent on the case id |
| What it stores | One `evaluation_results` row per case: per check and document type, true positives, false alarms, wrong dismissals, missed issues; prompt version, models, cost, time. Counts only, no personal data. Cascades from the case |
| Figures | Precision and recall per check and document type (weighted to income documents and bank statements), dismissal accuracy, cost and time, by prompt version and model |
| Where it shows | The dashboard's accuracy panel reads stored rows (also removes the `list_reviews` load); one Grafana panel per prompt version |
| The gate | Baseline = last 30 evaluated cases. A change passes if precision and recall on income documents and bank statements each stay within 5 points. Under 30 cases, a person decides |
| Never | Changes a score, severity or verdict, or reaches a prompt |
| Size | M: one table (dated SQL file), one handler, one route, one panel |

## 1. When to move

| Stage | Trigger (any one) | Today | Until then |
|---|---|---|---|
| A. Helm kit, tested on kind in CI | Already decided | — | Cloud Run serves everything |
| B. Staging on GKE | GLDB confirms it runs Kubernetes, or the assessment needs a live cluster | Neither answered | Staging on Cloud Run |
| C. Production on GKE | A stage's p95 passes 480 s (push limit 600 s) | Detection 92 s, extraction 36 s, analysis 26 s | Push delivery |
| | Load above what Cloud Run handles at current `max_instances` | Not measured | Raise `max_instances` |
| | Cloud Run costs over 1.2× GKE Autopilot for two months | ~$0 idle | Scale to zero |
| | GLDB needs its own cluster or network rules Cloud Run can't express | Not asked | Kong and IAM |

## 2. Target design

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
| Monitoring | Managed Prometheus, same Grafana, Cloud Trace | Nothing new |
| Backups | Cluster holds no state; Supabase point-in-time recovery matters | No cluster backup tool |
| Environments | dev (laptop), staging (Claude stubbed, no Anthropic key), prod | The chart refuses staging with a key |

## 3. The Helm kit (Stage A)

- **Chart:** one chart, a template per kind; per-role values generated from Terraform's `local.roles`. Values files: default, kind, staging, prod.
- **Network:** default-deny per namespace; gateway → api and audit-metrics only; workers take no inbound traffic; egress only to DNS, Supabase, Anthropic, Pub/Sub, registries.
- **Pods:** non-root (uid 10001), read-only root, no privilege escalation, CPU and memory limits.
- **CI on kind:** Pub/Sub emulator, stub Supabase, Calico; smoke test (health, one event per worker, one call the policies must block); Checkov on the rendered chart. ~6–8 min, on chart changes and weekly.

## 4. Migration (Stages B and C)

Each worker's subscription switches push → pull in place, so only one platform gets a role's messages; rollback is switching back. Each phase must also pass the evaluation gate.

| Phase | Moves | Check before next | Rollback |
|---|---|---|---|
| 1 | Staging, all roles | Failure tests pass; a green week | Delete the cluster |
| 2 | audit-metrics (sweeper moves with it) | Every event stored; sweeps run | Push back; sweeper back |
| 3 | case-worker | p95 and failures within 10% | Push back |
| 4 | detection-worker, then doc-worker | Same; no more recoveries or dead letters | Push back per role |
| 5 | api, Kong, frontend; DNS switched | Deploy gateway check passes | DNS back |
| 6 | Remove Cloud Run after 30 stable days | — | — |

## 5. CI/CD on GKE

Actions builds, signs (cosign) and attaches an SBOM, then opens a PR bumping the image digest. Argo CD syncs the cluster from the repo; rollback = revert the PR. Terraform owns the cloud pieces; Helm via Argo CD owns everything inside the cluster. Only Argo CD deploys; Binary Authorization admits only signed images.

## 6. Tools: in and out

| Tool | Why | Cost | When |
|---|---|---|---|
| Argo CD | Every production change is a reviewed commit (MAS TRM) | Free; a day | Stage B |
| KEDA | Idle cost near zero | Free | Stage B |
| Binary Authorization, cosign, SBOM | Only our images run; parts list for vulnerability reviews | Free; a day | Stage B |
| Secret Manager add-on | Secrets in one audited place | Included | Stage B |
| NetworkPolicy | Services can't talk to each other unless allowed | Included | Stage A |

**Left out:** a service mesh (NetworkPolicies suffice; too much to run), Kyverno/Gatekeeper (Autopilot covers it), Velero (no state to back up), Falco (needs privileged pods; GKE's posture dashboard covers basics), Litmus (scripted failure tests instead).

## 7. Open security items on GKE

| Audit row | On GKE |
|---|---|
| 6.3.1 every role holds the database key | Still true; the NAT allow-list limits where it works |
| 6.7.2 pinning by tag | Closed by §5 (digests, Binary Authorization) |
| 6.1.5 any service can publish any event | Unchanged |
| Cross-instance detection cancel (was CODEBASE_AUDIT 2.18): a cancel that lands on another instance is seen only through the audit trail, so not while audit-metrics is down | Do it with the move: a `cancel_requested_at` column on `documents` that the detection run polls (`workers/detection.py`, `_cancel_requested_since`), so a cancel needs only the database. More pods make a cross-instance cancel likelier. M |

Kong's real-IP settings (`deploy/kong/render.sh`) need the load balancer's ranges as trusted IPs on GKE, and the gateway key (backend `gateway.py`) can go once the services are internal.

## 8. Cost (rough; check Google's calculator before quoting)

| Stage | Monthly |
|---|---|
| A. Helm kit on kind in CI | $0 |
| B. Staging on GKE (public services always on, workers at 0) | $100–130 |
| C. Production on GKE (two replicas of public services) | $200–260 plus per document |
| Today on Cloud Run | ~$0 idle, cents per document |

Below the triggers, Cloud Run is cheaper.

## 9. Risks

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
| Too few cases to judge a change | Under 30, a person decides; golden set fills the gap |

## 10. Why each piece (in the bank's terms)

| Feature | Problem it solves | Expectation it meets |
|---|---|---|
| Evaluation harness | Proves a change didn't make detection worse | MAS FEAT (monitoring); TRM change management |
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

## 11. Decisions needed

- **How much worse may a change get before it's held back?** Options: any drop; more than 5 points on the highest-risk documents; or a person always decides. *Recommended: 5 points, with a person deciding under 30 reviewed cases.*
- **Does GLDB's IT run Kubernetes?** If yes, the kit is what they'd install and staging on GKE is worth it; if not, Cloud Run stays. *Recommended: ask now; it decides everything past Stage A.*
- **Pay for a test cluster before anyone needs it?** ~$100–130 a month idle. *Recommended: only if the bank or the assessment asks.*
- **Who looks after the cluster after handover?** *Recommended: agree an owner before any production move; without one, stay on Cloud Run.*
