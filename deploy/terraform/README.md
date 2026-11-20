# Terraform: the cloud deployment (GKE)

Creates everything the app needs on GCP (design: `HOW_IT_WORKS.md` §9; open work: `CLOUD_ARCHITECTURE_PLAN.md`). Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`. A green push to `main` deploys; `terraform apply` is manual and comes **before** a push that needs new infrastructure. Terraform makes the cloud pieces; the Helm chart (`deploy/helm`) makes everything inside the cluster. Commands are for cmd, from the repository root unless they say otherwise.

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry and its cleanup (3 newest versions per image kept), the role table |
| `iam.tf` | One service account per role, secrets and who reads them |
| `pubsub.tf` | `domain-events`, the dead-letter topic, one filtered pull subscription per role, the sweeper's scheduler job |
| `gke.tf`, `network.tf` | The Autopilot cluster; the network, Cloud NAT and its static address |
| `edge.tf` | `sixsevens.me` in Cloud DNS, the certificate, the load balancer's address, Cloud Armor |
| `workload_identity.tf` | Pods act as their role's account; KEDA's account |
| `supply_chain.tf` | The signing key, Binary Authorization, Cloud Deploy |
| `github.tf` | Keyless sign-in for Actions; the `github-deployer` account |
| `monitoring.tf`, `slo.tf` | Budget alert, emailed alerts, uptime checks and SLOs |
| `observability.tf`, `billing.tf` | Grafana's password and read rights; the `billing_export.costs` view |
| `roles.json` | The role table (sizes, scaling, secrets, env); `migration.json`: the consumers the chart runs |

## Setting up from scratch

1. **Tools:** gcloud, Terraform 1.16.6 (winget; open a new terminal after installing), `winget install Helm.Helm`, `gcloud components install gke-gcloud-auth-plugin kubectl`.
2. **Project and state**, from `deploy\terraform`:
   ```
   gcloud auth login
   gcloud auth application-default login      (tick "Select all" on the consent page)
   gcloud config set project <project-id>
   gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project <project-id>
   gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access
   copy terraform.tfvars.example terraform.tfvars   (project_id, github_repo, supabase_url, gke_enabled = true; optional billing_account, alert_email)
   terraform init -backend-config="bucket=<project-id>-tfstate"
   terraform apply
   ```
   Billing starts here. An account or registry that already exists: `terraform import` it.
3. **Secrets:** every secret needs a value or its service won't start (`terraform output secrets_to_fill`; one space for a key you don't have). Write the value to a file first, since piping adds a newline: `gcloud secrets versions add <id> --data-file=<file>`. `auth-secret-key`: any long random value. `gateway-secret`: letters and digits only (`openssl rand -hex 32`).
4. **DNS:** set the four `terraform output dns_name_servers` at the registrar. Wait until `gcloud certificate-manager certificates describe sixsevens-me --format="value(managed.state)"` says `ACTIVE`.
5. **Cluster access and KEDA** (once per cluster):
   ```
   gcloud container clusters get-credentials six-sevens --region asia-southeast1
   helm repo add kedacore https://kedacore.github.io/charts
   helm install keda kedacore/keda --version 2.16.1 --namespace keda --create-namespace --set podIdentity.gcp.enabled=true --set podIdentity.gcp.gcpIAMServiceAccount=gke-keda@fyp-six-sevens.iam.gserviceaccount.com
   ```
6. **GitHub:** set `terraform output github_variables` as repository **variables** (not secrets), plus `GKE_ENABLED=true` and `SUPABASE_URL`. The only repository secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`.
7. **Release:** push, or Actions → **Deploy to GKE**. Then `kubectl get pods -A` shows Kong, api, the frontend and audit-metrics running, the workers at 0, and `https://sixsevens.me` logs in.
8. **Alerts:** set `alert_email`, apply, confirm Google's email.

## Where to look

- **Logs:** `kubectl -n <role> logs deploy/<role>`. Each role has its own namespace; Kong is in `gateway`, Grafana in `observability`.
- **Grafana** (off since 2026-11-20 to save credits; turn on in `values-prod.yaml` and add its checks back to `deploy/clouddeploy/skaffold.yaml`): `https://grafana.sixsevens.me`, user `admin`, password `terraform output -raw grafana_admin_password`.
- **Traces:** Cloud Console → Trace explorer. One upload is one trace; Claude spans carry model, tokens and `llm.cost_usd`.
- **Alerts:** dead letters (10 min); API 5xx over 5% for 10 min; Claude spend over `alert_daily_claude_usd` (default $10) in 24 h; unrecoverable stuck work; site or API down from more than one region for 10 min; an SLO's error budget burning too fast.
- **Dead letters:** look with `gcloud pubsub subscriptions pull domain-events-dead-letter-pull --limit=10 --project fyp-six-sevens`, check the worker's logs, redo the action in the app if the sweeper hasn't, then clear them with the same command plus `--auto-ack`.
- **Claude cost:** the case's **Claude Cost** tab, or in Supabase's SQL editor: `select case_id, round(sum((detail->>'llm_cost_usd')::numeric), 4) as usd, count(*) as runs from audit_events where detail ? 'llm_cost_usd' group by case_id order by usd desc;` (list-price estimates; the Anthropic console has the invoice).
- **Cloud cost:** `bq query --use_legacy_sql=false --project_id=fyp-six-sevens "SELECT DATE(time) AS day, service, ROUND(SUM(net_cost - credits),2) AS gross, ROUND(SUM(net_cost),2) AS billed FROM billing_export.costs WHERE time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) GROUP BY day, service ORDER BY day, gross DESC"` (a few hours behind). Credits left: Billing → Credits. vCPU against the trial's 8: `gcloud compute project-info describe --flatten=quotas --filter="quotas.metric=CPUS_ALL_REGIONS" --format="table(quotas.usage,quotas.limit)"`.
- **Disaster recovery drill:** with Docker running and `SUPABASE_DB_URL` set, `python scripts\dr_drill.py` from `backend\`. Reads production only.

## Deploying and rolling back

A push to `main` runs CI, then `deploy.yml`: build and scan, sign, release through Cloud Deploy, verify through the load balancer. Docs-only pushes run nothing; deploys queue, never cancel halfway.

- Only the last job failed? **Re-run failed jobs** reuses the images.
- A failed rollout or verify step rolls back by itself; the verify log's failing `expect` line says which check.
- **Roll back by hand:** `gcloud deploy targets rollback prod --delivery-pipeline=six-sevens --region=asia-southeast1`.
- **A changed secret:** `kubectl -n <role> rollout restart deploy/<role>` (secrets are read at start).
- **An object the chart no longer renders stays on the cluster:** Cloud Deploy never deletes. Find leftovers with `kubectl get scaledobject,hpa,podmonitoring -A` and delete them by hand.
- Sizes and env live in `roles.json` (then `.venv\Scripts\python.exe deploy\helm\gen_values.py`), never in the workflow.

## Saving credits

The cluster costs money while idle: about S$21 a day measured with every worker kept running, an estimated S$13 with the workers scaling from zero (as now), and about S$10 with Grafana off too.

- **Everything to 0** (site down, an estimated S$8 a day):
  ```
  for %n in (api audit-metrics) do kubectl -n %n scale deploy/%n --replicas=0
  kubectl -n gateway scale deploy/kong --replicas=0
  kubectl -n frontend scale deploy/frontend --replicas=0
  for %n in (doc-worker detection-worker case-worker) do kubectl -n %n annotate scaledobject/%n autoscaling.keda.sh/paused-replicas=0 --overwrite
  kubectl -n api delete hpa api
  kubectl -n gateway delete hpa kong
  ```
  The next release puts it all back. Without a release: `gcloud deploy releases promote --release=<latest> --delivery-pipeline=six-sevens --region=asia-southeast1 --to-target=prod`, then remove each pause with `kubectl -n <worker> annotate scaledobject/<worker> autoscaling.keda.sh/paused-replicas-`.
- **Turning it all off:** set `GKE_ENABLED` to false, then `gke_enabled = false` and apply. The KMS key ring stays (key rings can't be deleted).
- Container Scanning stays disabled (`gcloud services disable containerscanning.googleapis.com`): it cost S$6–33 a day, and Trivy scans in the deploy instead.

## Failure tests

Run on production, on synthetic documents, one at a time (results in `HOW_IT_WORKS.md` §9). About $1–2 of Claude in all.

1. **A worker killed mid-run:** approve a document, and while detection runs: `kubectl -n detection-worker delete pod -l app.kubernetes.io/name=detection-worker --grace-period=0 --force`. It should still reach *Detection complete*.
2. **A flood:** upload and approve 10 documents together; `kubectl -n detection-worker get pods -w` should show a second pod, then back to 1 about 5 minutes after the queue empties.
3. **audit-metrics down:** `kubectl -n audit-metrics annotate scaledobject/audit-metrics autoscaling.keda.sh/paused-replicas=0 --overwrite`, add a note and rename the case, wait 5 minutes, then the same with `paused-replicas-`. The note and rename should appear in the audit trail.
4. **A poison message:** `gcloud pubsub topics publish domain-events --message="not an event" --attribute=type=document.approved`. After 5 attempts it is dead-lettered and the alert emails.
5. **A lost publish:** on a document waiting for approval, `update documents set status = 'queued', queued_at = now(), processing_started_at = null where id = '<id>';`. The sweeper should reprocess it within about 10 minutes.

## Testing locally with compose

- **Debug workers in the IDE:** `PUSH_HOST=host.docker.internal PUSH_PORT=8001 docker compose up --build --scale workers=0`, then uvicorn on :8001 from `backend/` with `ROLE=doc-worker,detection-worker,case-worker EVENT_TRANSPORT=pubsub PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local`.
- **Pull delivery (as on GKE):** `EVENT_DELIVERY=pull docker compose up --build`; after switching modes, run `docker compose up pubsub-init` again.
- **Before a risky change:** upload, approve, wait for the analysis, read `GET /audit/events?case_id=<id>`; then `docker compose restart workers` mid-detection (it should still finish).

## Problems met, and their fixes

| Symptom | Fix |
|---|---|
| `application-default login`: "cloud-platform scope is required" | Log in again and tick "Select all" |
| Budget: "requires a quota project" | `billing_project` + `user_project_override` (`versions.tf`); if then denied, remove `billing_account` |
| "Cloud Resource Manager API has not been used" | Enable it by hand once (setup, step 2) |
| 409 "already exists" for `github-deployer` or `app` | `terraform import` |
| `auth`: "exactly one of workload_identity_provider or credentials_json" | Set the GitHub values as repository **variables**, not secrets |
| A plan replaces heredoc secrets | A `.tf` file lost its CRLF line endings |
| Apply wants to recreate a subscription | Its filter changed (filters are immutable): expected, apply when idle |
| Grafana BigQuery plugin 404 on `react/jsx-runtime` | Bump Grafana and the plugin together (12.4.10 + 3.4.6) |
| Pod `Pending`, `GCE quota exceeded` | The trial's 8-vCPU cap; check `kubectl get nodes` before raising anything (never set `safe-to-evict: "false"`) |
| Kong `OOMKilled` | The chart pins `KONG_NGINX_WORKER_PROCESSES=1` |
| CORS error in the browser | Add the origin to Kong's CORS plugin (`deploy/kong`); never `*` |
| A document stays "Processing stuck" | Check worker logs and dead letters, then Retry |
| `'terraform' is not recognized` | Open a new terminal (it was opened before the install) |

**Don't delete** the default compute account or `github-deployer`. The service accounts are named `run-<role>`; the pods act as them.
