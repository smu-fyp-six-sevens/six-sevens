# Terraform: the Cloud Run deployment

Creates everything the services need on GCP (design: `CLOUD_ARCHITECTURE_PLAN.md`). Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`. A green push to `main` deploys; `terraform apply` is manual and must come **before** a push that needs new infrastructure.

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry and cleanup, the role table (size, scaling, secrets, env per backend role) |
| `iam.tf` | One service account per service, secrets and who reads them, publish and metric rights |
| `pubsub.tf` | `domain-events`, dead-letter topic, one filtered push subscription per role (filters from `subscriptions.json`), the sweeper's scheduler job |
| `run.tf` | Cloud Run: five backend roles and Kong (each with the Prometheus sidecar), the frontend |
| `observability.tf` | Grafana (with Google's query proxy), its admin password, Kong's scrape config |
| `github.tf` | Workload Identity Federation for Actions; the `github-deployer` account |
| `monitoring.tf` | Budget alert (if `billing_account` set) and four emailed alerts |
| `billing.tf` | `billing_export` dataset and the `costs` view Grafana reads |
| `roles.json` | The role table (Terraform and the Helm chart both read it); `migration.json`: the consumers GKE runs |
| `gke.tf`, `network.tf` | GKE: the Autopilot cluster and its nodes' account; the network, Cloud NAT and its static address |
| `edge.tf` | GKE: `sixsevens.me` in Cloud DNS, the certificate, the load balancer's address, Cloud Armor |
| `workload_identity.tf` | GKE: pods act as their role's account; pull rights; KEDA's account |
| `supply_chain.tf` | GKE: the signing key, Binary Authorization, Cloud Deploy, the deployer's extra rights |

Services start on a placeholder image; the deploy rolls out real images and Terraform ignores image changes. Running locally needs nothing here.

**Use a fresh terminal:** Terraform (1.16.6, same as CI) is installed with winget; a terminal opened before that can't find it.

## One-time setup (done for this project)

From `deploy/terraform`:

```bash
gcloud auth login
gcloud auth application-default login      # tick "Select all" on the consent page
gcloud config set project <project-id>
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project <project-id>
gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access
cp terraform.tfvars.example terraform.tfvars   # project_id, github_repo, supabase_url; optional billing_account, alert_email, min_instances
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply
```

- **Existing account or registry:** import instead of creating (`terraform import google_service_account.deployer projects/<id>/serviceAccounts/github-deployer@<id>.iam.gserviceaccount.com`; `terraform import google_artifact_registry_repository.app projects/<id>/locations/asia-southeast1/repositories/app`).
- **Secret values:** every secret needs a version or its service won't start (`terraform output secrets_to_fill`). For a key you don't have, store one space. In PowerShell write the value to a file first (piping adds a newline): `$f = New-TemporaryFile; [IO.File]::WriteAllText($f, 'the-value'); gcloud secrets versions add anthropic-api-key --data-file=$f; Remove-Item $f`. Then apply again. `auth-secret-key` can be any long random value. `gateway-secret` (the key Kong sends api and audit-metrics, which refuse any request without it) must be letters and digits only: `openssl rand -hex 32`.
- **GitHub:** `terraform output github_variables`; set them as repository **variables** (not secrets). The only repo secrets are `DATA_GOV_SG_API_KEY` and `SNYK_TOKEN`.
- **Alerts:** set `alert_email`, apply, confirm Google's email.

## What's running, and where to look

- **Grafana:** URL in the deploy summary; user `admin`, password `terraform output -raw grafana_admin_password`. Dashboards: Services, Pipeline, Agents, Cost. Data appears minutes after traffic.
- **Traces:** Cloud Console → Trace explorer. One upload is one trace across services; Claude spans carry model, tokens and `llm.cost_usd`.
- **Alerts:** dead-lettered events (10 min); API 5xx over 5% for 10 min; Claude spend over `alert_daily_claude_usd` (default $10) in 24 h; unrecoverable stuck work (30 min).
- **Claude cost:** every end-of-run event carries `detail.llm_cost_usd`, `llm_calls` and tokens. In the app: the case's **Claude Cost** tab. One case: `GET <Kong URL>/audit/cases/<case_id>/cost`. All cases (SQL editor): `select case_id, round(sum((detail->>'llm_cost_usd')::numeric), 4) as usd, count(*) as runs from audit_events where detail ? 'llm_cost_usd' group by case_id order by usd desc;`. These are list-price estimates (`backend/app/pricing.py`); the Anthropic console has the invoice.
- **Sweeper:** runs in audit-metrics every 5 minutes. Pause: `gcloud scheduler jobs pause sweep --location asia-southeast1` (`resume` likewise).

## Deploying

A push to `main` runs CI and then `deploy.yml` (docs-only pushes run nothing; deploys queue, never cancel halfway). By hand: Actions → **Deploy to Cloud Run**.

- Images a commit already has aren't rebuilt; a change under `backend/app` rebuilds the zoning index (~1 min).
- Only the deploy job failed? **Re-run failed jobs** reuses the images.
- A failed gateway check rolls every service back; the failing `expect` line says which.
- After a deploy: log in, create a case, upload, approve, wait for the analysis, read `GET <Kong URL>/audit/events?case_id=<id>`.
- A changed secret applies on the next revision (next deploy, or `gcloud run services update <service> --region asia-southeast1`). Sizes, scaling, env and secrets change here, never in the workflow.

## Notes

- **Scale to zero:** idle instances stop after ~15 min; a cold start takes 10–30 s. For a demo: `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }`, apply.
- **Public:** api, audit-metrics, Kong, frontend (app token) and Grafana (own login). Workers take calls only from `pubsub-push`.
- **Don't delete** the default compute account or `github-deployer` (used keylessly by the deploy).
- **Keep in step:** the Prometheus sidecar (`gmp_sidecar_image`), the query proxy (`grafana_proxy_image`; list tags with `curl.exe -s https://gke.gcr.io/v2/prometheus-engine/frontend/tags/list`), and Grafana with its BigQuery plugin.
- `versions.tf` bills API calls to this project (`billing_project`, `user_project_override`).

## Problems met, and their fixes

Fixed in the code unless the fix says otherwise.

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
- **Manual rollback:** `gcloud run services update-traffic <service> --to-revisions=<revision>=100 --region asia-southeast1`. Config changes go in Terraform.

## GKE

Everything for GKE sits behind `gke_enabled` (Terraform) and the repository variable `GKE_ENABLED`; with both off nothing changes. The move goes one role at a time (`CLOUD_ARCHITECTURE_PLAN.md` §17). Commands are for cmd, from the repository root unless they say otherwise.

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
8. **Release:** push a commit, or run Actions → Deploy to Cloud Run. The `gke` job signs the images and releases them through Cloud Deploy. Its verify step needs the certificate `ACTIVE`, so wait for step 4.
9. **Check:**
   - `kubectl get pods -A`: one each of Kong, api, the frontend, Grafana, and audit-metrics (HTTP only) is running; the workers are at 0.
   - `https://sixsevens.me` loads and logs in.
   - No Binary Authorization dry-run violations. In Logs Explorer: `resource.type="k8s_cluster" protoPayload.response.metadata.annotations."alpha.image-policy.k8s.io/policy-dry-run"="true"` should return nothing for our images.
   - A pod stuck `Pending` with `GCE quota exceeded` in `kubectl get events -n <namespace>`: the trial's 8-vCPU cap stopped Autopilot adding a node. Check what holds nodes (`kubectl get nodes`) before raising anything; a pod with `safe-to-evict: "false"` takes a node of its own, which is why the chart never sets it.
   - Kong `OOMKilled`: nginx starts a worker per node CPU; the chart pins `KONG_NGINX_WORKER_PROCESSES=1`.
10. **Then enforce:** set `binauthz_enforce = true` and apply. Once the Cloud Armor logs (Network Security → Cloud Armor → the policy's logs) show no false positives on normal use, set `cloud_armor_preview = false` and apply.

Officers can use `https://sixsevens.me` from here on. Cloud Run's URLs keep working, since both front doors share the database and the topic.

### Phases 2–4: move one consumer

The order is audit-metrics, then case-worker, then detection-worker, then doc-worker. Do each at a quiet time.

1. Add the role to `pull_roles` in `deploy/terraform/migration.json`.
2. Run `.venv\Scripts\python.exe deploy\helm\gen_values.py` (CI fails if you skip it).
3. From `deploy\terraform`, run `terraform plan`. It must show `google_pubsub_subscription.push["<role>"]` as **update in-place** (its `push_config` removed), never "must be replaced". Then `terraform apply`. From here Cloud Run gets none of the role's events; they wait in the subscription.
4. Push the two changed files. The release starts the role's pods, which pull the backlog.
5. **Watch:** `kubectl -n <role> get pods`; the Pipeline and Agents dashboards; `domain-events-dead-letter-pull` stays empty; for audit-metrics, sweeps still run every 5 minutes (`sweep.requested` in the audit trail).

**Rollback:** remove the role from `migration.json`, run `gen_values.py`, apply (push again, in place), then push.

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
- **Costs per push:** Artifact Analysis scans each pushed image, about $0.26 per image, so roughly $2–3 a deploy.
- **Turning it all off:** set `GKE_ENABLED` to false, then `gke_enabled = false` and apply. First empty `migration.json` (and apply), so Cloud Run takes the events again. The KMS key ring stays, since key rings can't be deleted.
- **Still to do:** Cloud Armor out of preview, phase 6 (removing Cloud Run after a stable week), and the GKE versions of Grafana's Cloud Run panels.
