# Terraform: the cloud deployment (GKE)

Creates everything the services need on GCP (design: `HOW_IT_WORKS.md` §9; open work: `CLOUD_ARCHITECTURE_PLAN.md`). Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`. A green push to `main` deploys; `terraform apply` is manual and must come **before** a push that needs new infrastructure.

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry and cleanup, the role table (size, scaling, secrets, env per backend role) |
| `iam.tf` | One service account per service, secrets and who reads them, publish and metric rights |
| `pubsub.tf` | `domain-events`, dead-letter topic, one filtered subscription per role, pulled on GKE (filters from `subscriptions.json`), the sweeper's scheduler job |
| `observability.tf` | Grafana's admin password and read rights (Grafana itself runs on GKE) |
| `github.tf` | Workload Identity Federation for Actions; the `github-deployer` account and its push to the registry |
| `monitoring.tf` | Budget alert (if `billing_account` set) and four emailed alerts |
| `billing.tf` | `billing_export` dataset and the `costs` view Grafana reads |
| `roles.json` | The role table (Terraform and the Helm chart both read it); `migration.json`: the consumers GKE runs |
| `gke.tf`, `network.tf` | GKE: the Autopilot cluster and its nodes' account; the network, Cloud NAT and its static address |
| `edge.tf` | GKE: `sixsevens.me` in Cloud DNS, the certificate, the load balancer's address, Cloud Armor |
| `workload_identity.tf` | GKE: pods act as their role's account; pull rights; KEDA's account |
| `supply_chain.tf` | GKE: the signing key, Binary Authorization, Cloud Deploy, the deployer's extra rights |

Terraform makes the cloud pieces; the Helm chart (`deploy/helm`) makes everything inside the cluster, released by the deploy. Running locally needs nothing here. Cloud Run ran the app until 2026-11-18 (phase 6, below).

**Use a fresh terminal:** Terraform (1.16.6, same as CI) is installed with winget; a terminal opened before that can't find it.

## One-time setup (done for this project)

From `deploy/terraform`:

```bash
gcloud auth login
gcloud auth application-default login      # tick "Select all" on the consent page
gcloud config set project <project-id>
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project <project-id>
gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access
cp terraform.tfvars.example terraform.tfvars   # project_id, github_repo, supabase_url; optional billing_account, alert_email
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply
```

- **Existing account or registry:** import instead of creating (`terraform import google_service_account.deployer projects/<id>/serviceAccounts/github-deployer@<id>.iam.gserviceaccount.com`; `terraform import google_artifact_registry_repository.app projects/<id>/locations/asia-southeast1/repositories/app`).
- **Secret values:** every secret needs a version or its service won't start (`terraform output secrets_to_fill`). For a key you don't have, store one space. In PowerShell write the value to a file first (piping adds a newline): `$f = New-TemporaryFile; [IO.File]::WriteAllText($f, 'the-value'); gcloud secrets versions add anthropic-api-key --data-file=$f; Remove-Item $f`. Then apply again. `auth-secret-key` can be any long random value. `gateway-secret` (the key Kong sends api and audit-metrics, which refuse any request without it) must be letters and digits only: `openssl rand -hex 32`.
- **GitHub:** `terraform output github_variables`; set them as repository **variables** (not secrets). The only repo secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`.
- **Alerts:** set `alert_email`, apply, confirm Google's email.

## What's running, and where to look

- **Grafana:** `https://grafana.sixsevens.me`; user `admin`, password `terraform output -raw grafana_admin_password`. Dashboards: Overview (home), Services, Pipeline, Agents, Cost. Data appears minutes after traffic; the Cost dashboard's per-namespace rows only after cost allocation (`gke.tf`) has run a day, since the billing export lags.
- **Traces:** Cloud Console → Trace explorer. One upload is one trace across services; Claude spans carry model, tokens and `llm.cost_usd`.
- **Alerts:** dead-lettered events (10 min); API 5xx over 5% for 10 min; Claude spend over `alert_daily_claude_usd` (default $10) in 24 h; unrecoverable stuck work (30 min); and on GKE (`slo.tf`): the site or API down from more than one region for 10 min, and an SLO's error budget burning 14.4× too fast over 1 h or 6× over 6 h. SLOs and their budgets: Monitoring → SLOs, service `six-sevens`.
- **Disaster recovery drill:** with Docker running and `SUPABASE_DB_URL` set (Supabase → Connect → Session pooler), `python scripts\dr_drill.py` from `backend\`. It reads production only and restores into a local container (`dr-drill-postgres`, removed after).
- **Claude cost:** every end-of-run event carries `detail.llm_cost_usd`, `llm_calls` and tokens. In the app: the case's **Claude Cost** tab. One case: `GET <Kong URL>/audit/cases/<case_id>/cost`. All cases (SQL editor): `select case_id, round(sum((detail->>'llm_cost_usd')::numeric), 4) as usd, count(*) as runs from audit_events where detail ? 'llm_cost_usd' group by case_id order by usd desc;`. These are list-price estimates (`backend/app/pricing.py`); the Anthropic console has the invoice.
- **Sweeper:** runs in audit-metrics every 5 minutes. Pause: `gcloud scheduler jobs pause sweep --location asia-southeast1` (`resume` likewise).

## Deploying

A push to `main` runs CI and then `deploy.yml` (docs-only pushes run nothing; deploys queue, never cancel halfway): build and scan, then the `gke` job (sign, release through Cloud Deploy). By hand: Actions → **Deploy to GKE**.

- Images a commit already has aren't rebuilt; a change under `backend/app` rebuilds the zoning index (~1 min).
- Only the gke job failed? **Re-run failed jobs** reuses the images.
- A failed rollout or verify step rolls back to the last good release; the verify log's failing `expect` line says which check.
- After a deploy: log in, create a case, upload, approve, wait for the analysis, read `GET https://api.sixsevens.me/audit/events?case_id=<id>`.
- A changed secret applies when the pods restart (GKE notes below). Sizes and env live in `roles.json` (`gen_values.py` for the chart), never in the workflow.

## Notes

- **Public:** only the load balancer: `sixsevens.me`, `api.sixsevens.me` (Kong; the app's session cookie) and `grafana.sixsevens.me` (its own login). Everything else is internal to the cluster.
- **Don't delete** the default compute account or `github-deployer` (used keylessly by the deploy). The service accounts are still named `run-<service>` from the Cloud Run days; GKE's pods act as them.
- **Keep in step:** Grafana with its BigQuery plugin; the query proxy beside Grafana (in the chart; list tags with `curl.exe -s https://gke.gcr.io/v2/prometheus-engine/frontend/tags/list`).
- `versions.tf` bills API calls to this project (`billing_project`, `user_project_override`).

## Problems met, and their fixes

Fixed in the code unless the fix says otherwise. Rows naming Cloud Run, `run.tf` or push delivery are from before phase 6 (2026-11-18).

| Symptom | Cause | Fix |
|---|---|---|
| `application-default login`: "cloud-platform scope is required" | Consent box not ticked | Log in again, tick "Select all" (`--no-browser` if needed) |
| Budget: "requires a quota project" | Local credentials billed elsewhere | `billing_project` + `user_project_override`; if then denied, remove `billing_account` |
| "Cloud Resource Manager API has not been used" | Needed before Terraform can enable APIs | Enable it by hand once (setup above) |
| 409 "already exists" for `github-deployer` or `app` | Made by an earlier deploy | `terraform import` |
| "Secret …/versions/latest was not found" | Secret has no value | Add values, apply again |
| `auth`: "exactly one of workload_identity_provider or credentials_json" | Values set as secrets, not variables | Use repository **variables** |
| `services update`: "unrecognized arguments: --quiet" | Flags after `--container` belong to that container | Service-wide flags before `--container` |
| Google's 404 on `/healthz` | Cloud Run reserves paths ending in `z` | Checks use `/health` |
| "Unable to retrieve Identity Pool subject token… timeout" | gcloud fetched a new GitHub token mid-run | One access token per job |
| `/cases` 200 without login; "Congratulations" page | Rollback pinned traffic to the placeholder | Rollout runs `update-traffic --to-latest`; check needs `{"status":"ok"}` |
| Push "unauthorized" late in a long build | Build token expired | `access_token_lifetime` 1200s |
| Deploy fails at "Record the revisions" | Workflow lists a service Terraform hasn't made | `terraform apply` first |
| Apply wants to recreate a subscription | Its filter changed (filters are immutable) | Expected; apply when idle |
| Grafana "No data" for Cloud Run/Pub/Sub only | Query proxy can't read Monitoring | Check its logs; needs `roles/monitoring.viewer` |
| Grafana BigQuery plugin 404 on `react/jsx-runtime` | Plugin newer than Grafana supports | Grafana 12.4.10 + plugin 3.4.6; bump together |
| Every service shows changes though nothing changed | Cloud Run reports a scaling block and sidecar CPU boost the config didn't set | `scaling` in `ignore_changes`; boost set on sidecars |
| Registry cleanup dry run logs nothing | Results don't reach Logging | Compare `gcloud artifacts docker images list --include-tags` with serving digests |
| `'terraform' is not recognized` | Terminal opened before install | New terminal, or restart VS Code |
| A plan replaces heredoc secrets | `.tf` file saved with LF endings | Keep `.tf` files CRLF |
| Every request through Kong gets Google's 404 | Kong forwards the original `Host` (Cloud Run routes by it) | `preserve_host: false`; Kong uses HTTPS upstream |
| A worker gets no messages; Pub/Sub shows 403 | Push account lacks `roles/run.invoker` on that worker | Grant it in `iam.tf` |
| The same message is processed again and again | Handler ran past 600 s, or returned non-2xx | Make it faster or return 2xx after recording the failure |
| CORS error in the browser | Frontend origin missing from Kong's CORS plugin | Add it in `deploy/kong`; CORS lives only in Kong (`CORS_IN_APP=false`), never `*` |
| Live updates stop after an hour | Cloud Run's 60-minute WebSocket limit | Expected; the client reconnects |
| A document stays "Processing stuck" | Should recover within a sweep plus its stage limit | Check worker logs and dead letters, then Retry |
| Events never reach a worker in compose | `pubsub-init` failed | `docker compose logs pubsub-init`, then `docker compose up pubsub-init` |

## Testing the split and rolling back

- **Debug workers in the IDE:** `PUSH_HOST=host.docker.internal PUSH_PORT=8001 docker compose up --build --scale workers=0`, then uvicorn on :8001 from `backend/` with `ROLE=doc-worker,detection-worker,case-worker EVENT_TRANSPORT=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local`. The emulator has no auth, so `/push` takes unsigned requests locally.
- **Pull delivery (as on GKE):** `EVENT_DELIVERY=pull docker compose up --build`. The subscriptions get no push endpoint and the workers pull them. Switching modes recreates the emulator's subscriptions, so run `docker compose up pubsub-init` again after changing it.
- **Before a risky change** (real Supabase and Claude): upload → `analyzed`; approve → `detected`; wait for the analysis; read `GET /audit/events?case_id=<id>`. Then `docker compose restart workers` mid-detection (it should still reach `detected`); stop and start audit-metrics (missed events appear); `docker compose run --rm pubsub-init python -m app.events.emulator_setup --poison` (retried, then dead-lettered).
- **Manual rollback:** see "Rollback of a release" under GKE. Config changes go in Terraform or the chart.

## GKE

Everything for GKE sits behind `gke_enabled` (Terraform) and the repository variable `GKE_ENABLED`; since phase 6 it is the only platform, so both stay on. The move went one role at a time (`HOW_IT_WORKS.md` §9, "How the move went"). Commands are for cmd, from the repository root unless they say otherwise.

### Before phase 1

1. **Tools:** `winget install Helm.Helm`, then `gcloud components install gke-gcloud-auth-plugin kubectl`. Open a new terminal afterwards.
2. **Budget:** the cluster costs money idle. On the lean profile that's roughly $130–150 a month, about $4–5 a day; the sizes are in `roles.json`'s `gke` blocks and the chart's `values.yaml`. Raise `budget_amount_sgd` in `terraform.tfvars` to about 250, or the alert fires every month. On the $300 free trial that's about two months, and the trial caps the project at 8 vCPUs at once (the lean profile peaks at 4.85).

### Phase 1: the cluster, with nothing taking events

1. In `deploy/terraform/terraform.tfvars`, add `gke_enabled = true`.
2. From `deploy\terraform`, run `terraform plan`. Expect only additions (the cluster, network, DNS, certificate, Cloud Armor, signing, Cloud Deploy, IAM) and the new APIs. Nothing on Cloud Run or Pub/Sub should change. If it plans to replace `google_secret_manager_secret_version.kong_gmp_config`, convert `observability.tf` to CRLF and plan again.
3. Run `terraform apply`. **Billing starts here.**
4. **DNS:** run `terraform output dns_name_servers` and set those four as `sixsevens.me`'s name servers at the registrar. The certificate is issued once they resolve, from minutes up to a few hours. Check with `gcloud certificate-manager certificates describe sixsevens-me --format="value(managed.state)"` until it says `ACTIVE`.
5. **Cluster access:** run `gcloud container clusters get-credentials six-sevens --region asia-southeast1`.
6. **KEDA** (once per cluster). It reads backlogs as `gke-keda`; `terraform output gke` shows the account:
   ```
   helm repo add kedacore https://kedacore.github.io/charts
   helm install keda kedacore/keda --version 2.16.1 --namespace keda --create-namespace --set podIdentity.gcp.enabled=true --set podIdentity.gcp.gcpIAMServiceAccount=gke-keda@fyp-six-sevens.iam.gserviceaccount.com
   ```
7. **Repository variables:** set `GKE_ENABLED=true` and `SUPABASE_URL` (the same URL as `supabase_url`).
8. **Release:** push a commit, or run Actions → Deploy to GKE. The `gke` job signs the images and releases them through Cloud Deploy. Its verify step needs the certificate `ACTIVE`, so wait for step 4.
9. **Check:**
   - `kubectl get pods -A`: one each of Kong, api, the frontend, Grafana, and audit-metrics (HTTP only) is running; the workers are at 0.
   - `https://sixsevens.me` loads and logs in.
   - No Binary Authorization dry-run violations. In Logs Explorer: `resource.type="k8s_cluster" protoPayload.response.metadata.annotations."alpha.image-policy.k8s.io/policy-dry-run"="true"` should return nothing for our images.
   - A pod stuck `Pending` with `GCE quota exceeded` in `kubectl get events -n <namespace>`: the trial's 8-vCPU cap stopped Autopilot adding a node. Check what holds nodes (`kubectl get nodes`) before raising anything; a pod with `safe-to-evict: "false"` takes a node of its own, which is why the chart never sets it.
   - Kong `OOMKilled`: nginx starts a worker per node CPU; the chart pins `KONG_NGINX_WORKER_PROCESSES=1`.
   - Verify failed on `https://sixsevens.me/ -> 503` right after a deploy: the new frontend pod (in another zone) was marked ready before the load balancer had health-checked it, and the old one went 8 s later. Kong, the frontend and Grafana now keep the old pod for 45 s (`gateway.handoverSeconds`), and verify waits for all three hosts and retries each check.
   - An object the chart no longer renders stays on the cluster: Cloud Deploy applies, it never deletes. A stale KEDA ScaledObject once kept case-worker at 0 against its new minimum. Find leftovers with `kubectl get scaledobject,hpa -A` and delete them by hand (`kubectl -n <ns> delete scaledobject <name>`).
10. **Then enforce:** set `binauthz_enforce = true` and apply. Once the Cloud Armor logs (Network Security → Cloud Armor → the policy's logs) show no false positives on normal use, set `cloud_armor_preview = false` and apply.

Officers can use `https://sixsevens.me` from here on.

### Phases 2–4: move one consumer

The order is audit-metrics, then case-worker, then detection-worker, then doc-worker. Do each at a quiet time.

1. Add the role to `pull_roles` in `deploy/terraform/migration.json`.
2. Run `.venv\Scripts\python.exe deploy\helm\gen_values.py` (CI fails if you skip it).
3. From `deploy\terraform`, run `terraform plan`. It must show `google_pubsub_subscription.push["<role>"]` as **update in-place** (its `push_config` removed), never "must be replaced". Then `terraform apply`. From here Cloud Run gets none of the role's events; they wait in the subscription.
4. Push the two changed files. The release starts the role's pods, which pull the backlog.
5. **Watch:** `kubectl -n <role> get pods`; the Pipeline and Agents dashboards; `domain-events-dead-letter-pull` stays empty; for audit-metrics, sweeps still run every 5 minutes (`sweep.requested` in the audit trail).

### Phase 6: remove Cloud Run (prepared 2026-11-18)

The code no longer has `run.tf`, the push identity (`pubsub-push`), the subscriptions' push config, the Cloud Run Grafana, Kong's sidecar config (`kong-gmp-config`), the deployer's Cloud Run rights or the Cloud Run deploy job.

1. From `deploy\terraform`, run `terraform plan`. Expect only **destroys** (the Cloud Run services and their IAM members, `pubsub-push` and its token grant, `kong-gmp-config` and its version and grant, `deployer_run`, `deployer_act_as`, the `run.googleapis.com` API entry, which stays on). The subscriptions shouldn't change: they're already pulled. Nothing may be "must be replaced", and nothing under GKE may change. If a heredoc secret is replaced, a `.tf` file lost its CRLF endings.
2. `terraform apply`, then push. The deploy builds one `frontend` image (no more `frontend-gke`) and goes straight from build to the gke job.
3. Check: `gcloud run services list --region asia-southeast1` lists nothing; `https://sixsevens.me` signs in; Grafana's Services and Cost dashboards fill their GKE rows.

**Rollback:** restore the files from git (`git checkout <commit before phase 6> -- deploy/terraform .github/workflows/deploy.yml`), apply, push. The services come back on the placeholder image until the deploy rolls out.

### Failure tests (a deliverable)

On production, at a quiet time, on synthetic documents only, one test at a time (the sweeper runs in audit-metrics, so test 3 pauses it). Uploads spend Claude tokens: about $1–2 for the whole run. Test 5 writes one document's status in the shared database. For each test, note the start time, what you saw and how long recovery took, and screenshot Grafana's Overview; the results go in `HOW_IT_WORKS.md` §9.

**Before:** `gcloud container clusters get-credentials six-sevens --region asia-southeast1`; on `sixsevens.me` create a case named `Failure test <date>`; open Grafana's Overview (last 1 hour) and the case's audit trail.

1. **A worker killed mid-run.** Upload one synthetic payslip, approve it, and once it shows *Detection in progress*:
   ```
   kubectl -n detection-worker delete pod -l app.kubernetes.io/name=detection-worker --grace-period=0 --force
   ```
   *Expect:* a new pod starts within a minute; the run's message wasn't acked, so it comes back (after its ack deadline, or the sweeper's 10-minute limit for `detecting`); the document reaches *Detection complete*; the audit trail has one `document.detected` and possibly a `recovery.requested`; no `recovery.exhausted`. *Record:* the time from the kill to *Detection complete*.
2. **A flood of uploads.** Upload 10 synthetic documents in one go, then approve them all together. Watch `kubectl -n doc-worker get pods -w` (then `-n detection-worker`).
   *Expect:* Overview's "Waiting events by worker" rises; a second pod within about 4 minutes; every document reaches *Detection complete*; no restarts (`RESTARTS` 0, no `OOMKilled`); back to 1 pod about 5 minutes after the queue empties. *Record:* when the second pod appeared, the last document's finish time, the peak backlog.
3. **audit-metrics stopped.** Pause it (KEDA keeps it at 1 otherwise):
   ```
   kubectl -n audit-metrics annotate scaledobject/audit-metrics autoscaling.keda.sh/paused-replicas=0 --overwrite
   ```
   Then on the case add a note and rename the case. The audit trail page fails while it's down (audit-metrics serves it): expected. After 5 minutes, resume:
   ```
   kubectl -n audit-metrics annotate scaledobject/audit-metrics autoscaling.keda.sh/paused-replicas-
   ```
   *Expect:* the officer actions still worked; `audit-metrics-push`'s backlog rose and drained on restart; the note and rename appear in the audit trail with their original times; sweeps resume (`sweep.requested` every 5 minutes). *Record:* the backlog peak and the time to drain. (If `scaledobject/audit-metrics` doesn't exist, use `kubectl -n audit-metrics scale deploy/audit-metrics --replicas=0`, then `--replicas=1`.)
4. **A poison message.**
   ```
   gcloud pubsub topics publish domain-events --message="not an event" --attribute=type=document.approved
   ```
   *Expect:* detection-worker and audit-metrics log a malformed message and nack it; after 5 attempts (backoff 10 s to 10 min, so up to about 20 minutes) it is in the dead-letter topic; Overview's "Failed events" shows 1 and the dead-letter alert emails; other work keeps flowing (upload one document meanwhile). Read it, then clear it: `gcloud pubsub subscriptions pull domain-events-dead-letter-pull --limit=5 --auto-ack`. *Record:* the time to dead-letter, and that the upload went through.
5. **A lost publish.** Take one synthetic document from test 2 that is *Ready for approval* (not approved). In Supabase's SQL editor, with its id:
   ```sql
   update documents set status = 'queued', queued_at = now(), processing_started_at = null where id = '<document id>';
   ```
   No event is published, as if the api crashed between its write and its publish. *Expect:* within about 10 minutes (the 5-minute limit plus up to one sweep), the audit trail shows the sweeper's `recovery.requested`, the document is processed again and is back to *Ready for approval*; Overview's "Stuck work and recoveries" shows it stuck, then recovered. *Record:* the time from the update to *Ready for approval*.

**After:** delete the test case if you don't want it in the dashboards (its audit events stay).

### Notes

- **Idle overnight (saves credits):** scale everything to 0, which leaves about $1.50 a day for the load balancer, Cloud Armor and NAT. The site is down until you scale back, and a consumer that has moved to GKE takes no events meanwhile; they wait in its subscription.
  ```
  for %n in (api audit-metrics) do kubectl -n %n scale deploy/%n --replicas=0
  kubectl -n gateway scale deploy/kong --replicas=0
  kubectl -n frontend scale deploy/frontend --replicas=0
  kubectl -n observability scale deploy/grafana --replicas=0
  for %n in (doc-worker detection-worker case-worker) do kubectl -n %n annotate scaledobject/%n autoscaling.keda.sh/paused-replicas=0 --overwrite
  ```
  The HPAs for api and Kong put them back to 1 within a minute, so also run `kubectl -n api patch hpa api -p "{\"spec\":{\"minReplicas\":1,\"maxReplicas\":1}}"` and the same for `gateway`/`kong`, or simply delete both HPAs (`kubectl -n api delete hpa api`, `kubectl -n gateway delete hpa kong`). The next release puts all of it back as the chart says. To scale back without a release, re-run the last one: `gcloud deploy releases promote --release=<latest> --delivery-pipeline=six-sevens --region=asia-southeast1 --to-target=prod`, then remove the KEDA pause with `kubectl -n <worker> annotate scaledobject/<worker> autoscaling.keda.sh/paused-replicas-`.
- **Pods by role:** `kubectl -n <role> logs deploy/<role>`. Each role has its own namespace; Kong is in `gateway` and Grafana in `observability`.
- **Rollback of a release:** `gcloud deploy targets rollback prod --delivery-pipeline=six-sevens --region=asia-southeast1`. The deploy does this itself when a rollout fails.
- **A changed secret:** `kubectl -n <role> rollout restart deploy/<role>`. The add-on mounts secrets as files, which are read at start.
- **No paid image scanning:** Trivy scans in the deploy instead. Terraform doesn't turn an API off, so Container Scanning was disabled by hand (`gcloud services disable containerscanning.googleapis.com --project fyp-six-sevens`); enabling it again costs about $0.26 per pushed image, build caches included.
- **Turning it all off:** set `GKE_ENABLED` to false, then `gke_enabled = false` and apply. With Cloud Run gone, nothing serves the app after that. The KMS key ring stays, since key rings can't be deleted.
- **Usage so far:** `bq query --use_legacy_sql=false "SELECT service, ROUND(SUM(cost),2) AS cost, ROUND(SUM(credits),2) AS credits FROM fyp-six-sevens.billing_export.costs WHERE time >= '2026-11-14' GROUP BY service ORDER BY cost DESC"` (lags a few hours; credits left: console, Billing → Credits). vCPU against the trial's 8: `gcloud compute project-info describe --flatten=quotas --filter="quotas.metric=CPUS_ALL_REGIONS" --format="table(quotas.usage,quotas.limit)"`.
