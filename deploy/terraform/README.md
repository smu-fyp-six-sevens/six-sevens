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
- **Before a risky change** (real Supabase and Claude): upload → `analyzed`; approve → `detected`; wait for the analysis; read `GET /audit/events?case_id=<id>`. Then `docker compose restart workers` mid-detection (it should still reach `detected`); stop and start audit-metrics (missed events appear); `docker compose run --rm pubsub-init python -m app.events.emulator_setup --poison` (retried, then dead-lettered).
- **Manual rollback:** `gcloud run services update-traffic <service> --to-revisions=<revision>=100 --region asia-southeast1`. Config changes go in Terraform.
