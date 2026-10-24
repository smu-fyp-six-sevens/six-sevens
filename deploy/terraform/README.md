# Terraform: the Cloud Run deployment

Creates everything the services need on GCP (design: `CLOUD_ARCHITECTURE_PLAN.md`). Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`. Every push to `main` deploys automatically once CI passes; Terraform `apply` stays manual and must come **before** a push that needs new infrastructure.

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry (with cleanup policies, in dry-run mode), the role table (size, scaling, secrets, environment per backend role) |
| `iam.tf` | a service account per service, Secret Manager secrets and who may read each, publish rights, metric writing, Pub/Sub's service agent |
| `pubsub.tf` | `domain-events`, its dead-letter topic, one filtered push subscription per role (filters from `subscriptions.json`, exported by `python -m app.events.types`), the Cloud Scheduler job for the sweeper |
| `run.tf` | Cloud Run: the five backend roles and Kong (each with the Managed Prometheus sidecar), the frontend |
| `observability.tf` | Grafana (with Google's Prometheus query proxy as a sidecar), its generated admin password, the scrape config for Kong |
| `github.tf` | Workload Identity Federation for GitHub Actions and the `github-deployer` account |
| `monitoring.tf` | the budget alert (when `billing_account` is set) and four alert policies emailed to `alert_email` |
| `billing.tf` | the `billing_export` dataset, and the `costs` view Grafana reads once `billing_export_table` is set |

Services are created on a placeholder image (Cloud Run's "Congratulations" page); the deploy workflow rolls out the real images and Terraform ignores image changes. Teammates who run `uvicorn` + `npm run dev` need nothing here.

**Run Terraform from a fresh terminal.** It's installed with winget (1.16.6, the same version CI checks with); a VS Code terminal opened before the install doesn't see it. Restart VS Code fully, or use a separate Command Prompt.

## Open

- **Billed cost in Grafana.** The Cloud Billing export to `billing_export` is on (the dataset lists `billing-export-bigquery@system.gserviceaccount.com` as an owner). Once `bq ls fyp-six-sevens:billing_export` shows a `gcp_billing_export_v1_...` table, set `billing_export_table = "<that name>"` in `terraform.tfvars` and `terraform apply`. The Cost dashboard's "Billed by Google" row then fills in (it needs the Grafana 12 image, deployed by the next push).
- **Artifact Registry cleanup** is in dry-run mode: see `PENDING_WORK.md`.

## One-time setup (already done for this project; for a new one)

From `deploy/terraform`:

```bash
gcloud auth login
gcloud auth application-default login      # tick "Select all" on the consent page, or it fails
gcloud config set project <project-id>
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project <project-id>
gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access
cp terraform.tfvars.example terraform.tfvars   # project_id, github_repo, supabase_url; optional billing_account, alert_email, min_instances
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply
```

- An account or repository that already exists is imported, not recreated: `terraform import google_service_account.deployer projects/<project-id>/serviceAccounts/github-deployer@<project-id>.iam.gserviceaccount.com`, and `terraform import google_artifact_registry_repository.app projects/<project-id>/locations/asia-southeast1/repositories/app`.
- **Secret values.** Every secret needs a version or its service won't start: `terraform output secrets_to_fill`. For a key you don't have, store a single space (the app treats it as empty). In PowerShell write the value to a file first, since piping adds a newline: `$f = New-TemporaryFile; [IO.File]::WriteAllText($f, 'the-value'); gcloud secrets versions add anthropic-api-key --data-file=$f; Remove-Item $f`. Then apply again. `auth-secret-key` only signs login tokens: any long random value works.
- **GitHub.** `terraform output github_variables`, then set `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER` and `GCP_DEPLOYER_SA` as repository **variables** (the workflows read `vars.*`). The only repository secrets are `DATA_GOV_SG_API_KEY` (the zoning index, at build) and `SNYK_TOKEN`. Services read their keys from Secret Manager, never from GitHub.
- **Alerts:** set `alert_email`, apply, and confirm Google's email; nothing arrives until then.

## What's on, and how to read it

- **Grafana:** the Grafana URL (in the deploy run's summary), user `admin`, password from `terraform output -raw grafana_admin_password`. Four dashboards: Services, Pipeline, Agents, Cost. Data appears a few minutes after traffic; the first load after idle waits for a cold start. The same PromQL works in Cloud Console → Monitoring → Metrics Explorer.
- **Traces:** Cloud Console → Trace → Trace explorer; one upload is one trace across api, doc-worker and detection-worker, and Claude spans carry the model, tokens and `llm.cost_usd`.
- **Alerts** (`monitoring.tf`): events dead-lettered (last 10 min); API 5xx above 5% for 10 min with traffic; Claude spend over `alert_daily_claude_usd` (default 10) in 24 h; stuck work that couldn't be recovered (last 30 min). Each closes 30 minutes after its condition clears.
- **Claude cost per document and case:** each end-of-run event (`document.analyzed`/`failed`, `document.detected`, `detection.failed`, `case.analysis.completed`) carries `detail.llm_cost_usd`, `llm_calls`, token counts and `run`.
  - In the app: the case page's **Claude Cost** tab (placeholder design).
  - One case: `GET <Kong URL>/audit/cases/<case_id>/cost` (with your token): total, per document, case analysis, every run.
  - Across cases (Supabase SQL editor): `select case_id, round(sum((detail->>'llm_cost_usd')::numeric), 4) as usd, count(*) as runs from audit_events where detail ? 'llm_cost_usd' group by case_id order by usd desc;`
  - Claude isn't billed through Google: the dashboard's Claude figures are the app's own count at list prices (`backend/app/pricing.py`); the Anthropic console has the invoice.
- **The sweeper** runs in audit-metrics on Cloud Scheduler's tick every 2 minutes. Pause with `gcloud scheduler jobs pause sweep --location asia-southeast1` (`resume` the same way). To see it work: the Pipeline dashboard's "Stuck work and recovery" row, or `GET <Kong URL>/audit/events?case_id=<id>`.

## Deploying and checking a deploy

**Automatic:** a push to `main` runs CI (backend, frontend, security, Terraform fmt/validate) and, when all pass, `deploy.yml`. Docs-only pushes run nothing. Deploys queue one at a time and are never cancelled halfway. **By hand:** Actions → **Deploy to Cloud Run** → Run workflow.

- Each job mints one Google access token at its start (20 minutes for build, 10 for deploy), so nothing returns to GitHub's token service mid-run.
- An image the commit already has isn't rebuilt; unchanged layers come from the registry cache (`buildcache:<image>`). A change under `backend/app` rebuilds the zoning index (about a minute).
- When only the deploy job failed and no code changed, use **"Re-run failed jobs"**: it reuses the built images.
- If the gateway check fails, every service goes back to the revision that was serving before; the failing `expect` line says which check failed.
- After a deploy: open the frontend, log in with an `AUTH_USERS` account, create a case, upload, approve, run the analysis, and read `GET <Kong URL>/audit/events?case_id=<id>`.

A changed secret reaches a service on its next revision (the next deploy, or `gcloud run services update <service> --region asia-southeast1`). Sizes, scaling, environment and secrets change here, never in the workflow.

## Notes

- **Scale to zero.** Every service defaults to 0 minimum instances; Cloud Run keeps an idle instance for up to about 15 minutes, then the next event waits for a cold start (10-30 s here, from the LangGraph and OCR imports). For a demo: `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }`, then apply.
- **Public services:** api, audit-metrics, Kong, the frontend (each checks the app's token) and Grafana (its own login). Workers can be invoked only by `pubsub-push`; audit-metrics' `/push` checks its OIDC token itself.
- **Don't delete** the default compute account (`<number>-compute@developer.gserviceaccount.com`) or `github-deployer` (the deploy uses it through Workload Identity Federation, with no key; no account should have a user-managed key).
- **Images versions to keep in step:** the Managed Prometheus sidecar (`gmp_sidecar_image`), Google's query proxy (`grafana_proxy_image`; list tags with `curl.exe -s https://gke.gcr.io/v2/prometheus-engine/frontend/tags/list`, since `gcloud artifacts` refuses `gke.gcr.io`), and Grafana with its BigQuery plugin (`deploy/grafana/Dockerfile`).
- **Provider billing project.** `versions.tf` sets `billing_project` and `user_project_override`, so API calls bill to this project (the budget API refuses local user credentials otherwise).
- To let only `main` deploy, add `assertion.ref == "refs/heads/main"` to the `attribute_condition` in `github.tf`.

## Problems met, and their fixes

| Symptom | Cause | Fix (already in the code unless it says otherwise) |
|---|---|---|
| `application-default login`: "cloud-platform scope is required but not consented" | The Google Cloud box on the consent page was not ticked | Log in again and tick "Select all" (`--no-browser` if the browser flow fails) |
| Budget: "requires a quota project" | Local credentials billed to gcloud's own project | `billing_project` + `user_project_override` in `versions.tf`; if it then says permission denied, remove `billing_account` |
| "Cloud Resource Manager API has not been used in project" | Needed before Terraform can enable anything | `gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com` (by hand, once) |
| 409 "already exists" for `github-deployer` or repository `app` | Created by the earlier single-service deploy | `terraform import` (above) |
| Cloud Run: "Secret …/versions/latest was not found" | A secret with no value yet | Add the values, apply again |
| `auth`: "must specify exactly one of workload_identity_provider or credentials_json" | The values are secrets or environment variables instead of repository variables | Set repository **variables** |
| `gcloud run services update`: "unrecognized arguments: --quiet" | Every flag after `--container` belongs to that container | Service-wide flags go before `--container` |
| Google's HTML 404 for `/healthz` on any Cloud Run URL | Cloud Run reserves paths ending in `z` | The check uses `/health`; `/healthz` stays for Kubernetes later |
| "Unable to retrieve Identity Pool subject token… upstream request timeout" | gcloud fetched a new GitHub token mid-run and GitHub's token service timed out | One access token per job (`token_format: access_token`) |
| `/cases` answers 200 without a login; the "Congratulations" page | A rollback pinned traffic to the placeholder revision, and `services update` doesn't move traffic | The rollout runs `update-traffic --to-latest`; the check requires `{"status":"ok"}` from `/health` |
| A push fails "unauthorized"/"denied" late in a long build | The build job's token expired | `access_token_lifetime` 1200s in the build job |
| Deploy fails at "Record the revisions" with a service not found | The workflow lists a service Terraform hasn't created | `terraform apply` first (`docs/ADDING_A_SERVICE.md`) |
| `terraform apply` wants to destroy and recreate a subscription | Its filter changed (filters are immutable) | Expected after an event-type change; apply when nothing is processing |
| Grafana panels say "No data" for Cloud Run or Pub/Sub only | The query proxy can't read Cloud Monitoring | Check the `grafana` service's `prometheus` container logs; its account needs `roles/monitoring.viewer` |
| Grafana: "BigQuery plugin failed … 404 loading react/jsx-runtime" | The plugin was newer than Grafana supported (`grafana cli` doesn't enforce plugin requirements) | Grafana 12.4.10 with the plugin pinned to 3.4.6 (`deploy/grafana/Dockerfile`); bump them together |
| `'terraform' is not recognized` | The terminal started before winget put Terraform on the PATH | Restart VS Code fully, or use a new Command Prompt |
