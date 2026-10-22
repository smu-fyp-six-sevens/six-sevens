# Terraform: the Cloud Run deployment

Creates everything the services need on GCP (CLOUD_ARCHITECTURE_PLAN.md sections 6-8):

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry, the role table (size, scaling, secrets, environment per service) |
| `iam.tf` | a service account per service, Secret Manager secrets and who may read each, publish rights, metric writing, Pub/Sub's service agent |
| `pubsub.tf` | `domain-events`, its dead-letter topic, one filtered push subscription per role (filters from `subscriptions.json`, exported by `python -m app.events.types`), and the Cloud Scheduler job that publishes the sweeper's tick |
| `run.tf` | Cloud Run: api, doc-worker, detection-worker, case-worker, audit-metrics and Kong (each with the Managed Prometheus sidecar), frontend |
| `observability.tf` | Grafana on Cloud Run (with Google's Prometheus query proxy as a sidecar), its generated admin password, the scrape config for Kong's metrics |
| `github.tf` | Workload Identity Federation for GitHub Actions and the deployer account |
| `monitoring.tf` | the budget alert (when `billing_account` is set) |

Terraform owns each service's configuration. Services are created on a placeholder image (Cloud Run's "Congratulations" page, which answers 200 on every path); the deploy workflow (`.github/workflows/deploy.yml`) rolls out the real images and Terraform ignores image changes.

Only the person running the cloud needs anything here. Teammates who run `uvicorn` + `npm run dev` need only `backend/.env`, as before. Deploying needs no local files either: the workflow runs on GitHub from the repository variables, so anyone with write access can run it.

## Status (2026-10-22)

Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`, deploying from the `microservices` branch.

Done:
- the state bucket and `terraform apply` (the existing `github-deployer` account and `app` repository were imported, not recreated);
- the secret values and the GitHub repository variables;
- the first successful end-to-end deploy (2026-10-22), after each problem in "Problems met" below was fixed in the workflow.

Whether the budget alert applies depends on your billing permissions (see below).

**Pending: the observability change** (Grafana, Kong metrics, traces on). It needs Terraform before the next deploy; see the next section.

## Rolling out Grafana and tracing (once)

The deploy workflow now builds and checks a `grafana` service, so Terraform must create it first, or the deploy fails at "Record the revisions now serving".

```powershell
cd deploy/terraform
# The proxy tag in grafana_proxy_image (variables.tf, v0.15.3-gke.0) existed on 2026-10-22. gke.gcr.io isn't Artifact
# Registry, so `gcloud artifacts docker tags list` refuses it; list its tags with:
#   curl.exe -s https://gke.gcr.io/v2/prometheus-engine/frontend/tags/list
terraform init -upgrade -backend-config="bucket=<project-id>-tfstate"   # adds the random provider (Grafana's password)
terraform apply
terraform output -raw grafana_admin_password                         # log in as admin with this
```

Then commit, push and run the deploy. The run summary shows the Grafana URL.

What the apply changes:
- **New:** the `grafana` service, its account, the password secret, `kong-gmp-config`, and metric-writer and viewer roles.
- **Kong:** its container gets a name and a collector sidecar.
- **Every backend service:** gets `OTEL_ENABLED=true`.

Expect a new revision of each service. The images stay as deployed: Terraform ignores image changes.

## Rolling out stuck-work recovery (once, can go with the apply above)

The same `terraform apply` also:
- **Replaces the three worker subscriptions.** Their filters gained `attributes.owner`, and a Pub/Sub filter can't be edited, so Terraform destroys and recreates them. Messages waiting in them at that moment are dropped, so apply when nothing is processing. That's expected in the plan: 3 to destroy, 3 to add. Nothing else should be destroyed.
- **Creates the Cloud Scheduler job `sweep`** (and enables its API).
- **Sets `SWEEPER_ENABLED=true` on audit-metrics and lets it publish.**
- **Raises api's maximum to 3 instances.** WebSocket tickets are now signed, so no instance needs another's memory.

Then deploy as usual: the new code must be running before the scheduler's ticks mean anything. Ticks that arrive at the old code are stored as plain events, which is harmless.

**To see it work:** Pipeline dashboard → "Stuck work and recovery", or `GET <Kong URL>/audit/events?case_id=<id>` for `recovery.requested`, `document.recovered` and `recovery.exhausted`. To test it, stop a detection mid-run (for example, deploy while one runs): within about 17 minutes the document is detected again without anyone acting.

## One-time setup

PowerShell or Git Bash, from `deploy/terraform`:

```bash
gcloud auth login
gcloud auth application-default login      # tick "Select all" on the consent page, or it fails
gcloud config set project <project-id>
# Terraform needs these to read the project before it can enable anything else.
gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com --project <project-id>

# 1. A bucket for the state
gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access

# 2. Variables (git-ignored): project_id, github_repo, supabase_url; optional billing_account, min_instances
cp terraform.tfvars.example terraform.tfvars

# 3. Create everything (check the newest sidecar tag first and set gmp_sidecar_image if it isn't 1.2.0:
#    gcloud artifacts docker tags list us-docker.pkg.dev/cloud-ops-agents-artifacts/cloud-run-gmp-sidecar/cloud-run-gmp-sidecar)
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply
```

If an account or repository already exists from an earlier deploy, import it instead of recreating it, e.g.:

```bash
terraform import google_service_account.deployer projects/<project-id>/serviceAccounts/github-deployer@<project-id>.iam.gserviceaccount.com
terraform import google_artifact_registry_repository.app projects/<project-id>/locations/asia-southeast1/repositories/app
```

The first apply's Cloud Run services fail until the secrets have values (step 4); apply again after it.

**4. Secret values.** Every secret needs at least one version or Cloud Run won't start the service: `supabase-publishable-key`, `supabase-secret-key`, `anthropic-api-key`, `auth-users`, `auth-secret-key`, `data-gov-sg-api-key`, `ura-api-key` (`terraform output secrets_to_fill`). For a key you don't have, store a single space: the app strips it to empty and skips that lookup. In PowerShell, write the value to a file first, because piping a string to gcloud adds a newline:

```powershell
$f = New-TemporaryFile; [IO.File]::WriteAllText($f, 'the-value'); gcloud secrets versions add anthropic-api-key --data-file=$f; Remove-Item $f
```

(Or Git Bash: `printf '%s' 'the-value' | gcloud secrets versions add anthropic-api-key --data-file=-`.) A local `set-secrets.ps1` doing this for all seven is git-ignored; delete it once the values are in, since it holds them in plain text. Check every secret has a version:

```powershell
foreach ($s in "supabase-publishable-key","supabase-secret-key","anthropic-api-key","auth-users","auth-secret-key","data-gov-sg-api-key","ura-api-key") { "$s: " + (gcloud secrets versions list $s --format="value(name)" --limit 1) }
```

`auth-secret-key` only signs the app's login tokens; any long random value works, and changing it just makes everyone log in again. `ONEMAP_API_TOKEN` is not used in the cloud (OneMap works without one).

**5. GitHub.** `terraform output github_variables`, then set `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER` and `GCP_DEPLOYER_SA` under Settings → Secrets and variables → Actions → **Variables** (not Secrets: the workflow reads `vars.*`). They are identifiers, not credentials: only a workflow in this repository can use them (the identity provider checks the repository). Keep `DATA_GOV_SG_API_KEY` as a repository **secret** (a build secret for the zoning index).

## Deploying and checking a deploy

Actions → **Deploy to Cloud Run** → Run workflow, choosing the branch to deploy (`microservices` until it is merged; the `deploy.yml` on `main` is the old single-service one).

- Each job mints one Google access token at its start (20 minutes for build, 10 for deploy) and gives it to gcloud and Docker, so nothing returns to GitHub's token service mid-run.
- An image the commit already has is not rebuilt; otherwise unchanged layers come from the build cache in Artifact Registry (`buildcache:<image>`). A change under `backend/app` still rebuilds the zoning index (about a minute).
- When only the deploy job failed and no code changed, use **"Re-run failed jobs"** on the run: it reuses the built images.
- If the gateway check fails, every service goes back to the revision that was serving before, and the failing `expect` line in the log says which check failed.

When it succeeds, the run summary shows the frontend and API (Kong) URLs. Then:

1. Open the frontend URL and log in with an `AUTH_USERS` account.
2. Create a case, upload a document, approve it, and run the case analysis.
3. `GET <Kong URL>/audit/events?case_id=<id>` with your token lists the events, each officer action with Kong's request id.
4. **Grafana:** open the Grafana URL and log in as `admin` with `terraform output -raw grafana_admin_password`. The "Six Sevens" folder holds four dashboards: Services, Pipeline, Agents (calls, cache hit ratio, latency, tool outcomes) and Cost.
   - Data appears a few minutes after the services get traffic.
   - The first page load after an idle period waits for Grafana's cold start.
   - The same PromQL also works in Cloud Console → Monitoring → Metrics Explorer.
5. **Traces:** Cloud Console → Trace → Trace explorer. One upload shows api, doc-worker and detection-worker spans in one trace. Claude spans carry the model, tokens and `llm.cost_usd`.

A secret changed later reaches a service on its next revision (the next deploy, or `gcloud run services update <service> --region asia-southeast1`). Configuration (sizes, scaling, environment, secrets) changes here, with `terraform apply`, never in the workflow.

## Clean-up after the first successful deploy

- Delete the old single-service deploy (`fyp-backend`, `fyp-frontend`). It's kept running for now; the full step-by-step teardown, including revoking the old key, is in `PENDING_WORK.md` under "Tear down the old single-service deploy".
- Delete the GitHub secrets the new workflow no longer reads: `GCP_SA_KEY`, `SUPABASE_*`, `ANTHROPIC_API_KEY`, `AUTH_*`, `ONEMAP_API_TOKEN`, `URA_API_KEY`, and any `GCP_PROJECT_ID` or `GCP_REGION` *secrets* (the variables of the same names stay). Keep `DATA_GOV_SG_API_KEY`.
- Delete `set-secrets.ps1` if it still exists.
- Optional: `docker image rm six-sevens/api:check six-sevens/kong:check six-sevens/grafana:check prom/prometheus:v3.1.0 hashicorp/terraform:1.9.8` (local images used to check the Dockerfiles, dashboards and Terraform).

## Optional, any time

- A `SNYK_TOKEN` repository secret makes CI use Snyk instead of `pip-audit` and `npm audit`.
- **Exact billed cost.** The Cost dashboard estimates Cloud Run and Pub/Sub from usage. For the invoice figures, a billing admin can turn on Billing → Billing export → BigQuery (a Grafana BigQuery data source can read it later). Until then, use Billing → Reports.
- **Cloud Run prices.** If they change, edit the price boxes at the top of the Cost dashboard, or their defaults in the dashboard JSON.
- For a demo, `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }` in `terraform.tfvars`, then `terraform apply`, avoids cold starts (below).
- To let only `main` deploy, add `assertion.ref == "refs/heads/main"` to the `attribute_condition` in `github.tf` (after merging).

## Notes

- **Scale to zero.** Every service defaults to 0 minimum instances. Cloud Run keeps an idle instance for up to about 15 minutes after its last request (for a worker, its last push; Google publishes no exact figure), then stops it; the next event waits for a cold start, roughly 10-30 s here because of the LangGraph and OCR imports. Idle instances cost nothing with CPU idle.
- **api runs up to 3 instances.** WebSocket tickets are signed, and each instance has its own Realtime listener.
- **The sweeper** runs in audit-metrics on Cloud Scheduler's `sweep.requested` (every 2 minutes). Pause it with `gcloud scheduler jobs pause sweep --location asia-southeast1`, and `resume` it the same way.
- **Public services** are api, audit-metrics, Kong and the frontend (each still checks the app's token), and Grafana (its own login, anonymous access off). Workers can be invoked only by the `pubsub-push` account; audit-metrics' `/push` checks that account's OIDC token itself.
- **The sidecar** (`gmp_sidecar_image`): with CPU idle between requests, scrapes can be delayed and short gaps appear.
- **Provider billing project.** `versions.tf` sets `billing_project` and `user_project_override`, so API calls bill to this project rather than gcloud's own client project (the budget API refuses local user credentials otherwise).

## Problems met, and their fixes

| Symptom | Cause | Fix (already in the code unless it says otherwise) |
|---|---|---|
| `application-default login`: "cloud-platform scope is required but not consented" | The Google Cloud box on the consent page was not ticked | Log in again and tick "Select all" (`--no-browser` if the browser flow fails) |
| Budget: "requires a quota project" | Local credentials billed to gcloud's own project | `billing_project` + `user_project_override` in `versions.tf`; if it then says permission denied, remove `billing_account` |
| "Cloud Resource Manager API has not been used in project" | Needed before Terraform can enable anything | `gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com` (by hand, once) |
| 409 "already exists" for `github-deployer` or repository `app` | Created by the earlier single-service deploy | `terraform import` (above) |
| Cloud Run: "Secret …/versions/latest was not found" | A secret with no value yet | Add the values (step 4), apply again |
| `auth`: "must specify exactly one of workload_identity_provider or credentials_json" | The run used `main`'s old workflow, or the values are secrets/environment variables instead of repository variables | Run from `microservices`; set repository **variables** |
| `gcloud run services update`: "unrecognized arguments: --quiet (did you mean '--quiet'?)" | Every flag after `--container` belongs to that container | Service-wide flags go before `--container app` |
| Google's HTML 404 for `/healthz` on any Cloud Run URL | Cloud Run reserves paths ending in `z` | The check uses `/health`; `/healthz` stays for Kubernetes later |
| "Unable to retrieve Identity Pool subject token… upstream request timeout" | gcloud fetched a new GitHub token mid-run and GitHub's token service timed out | One access token per job (`token_format: access_token`, `create_credentials_file: false`) |
| `/cases` answers 200 without a login; the "Congratulations" page | A rollback pinned traffic to the placeholder revision, and `services update` doesn't move traffic | The rollout runs `update-traffic --to-latest`; the check also requires `{"status":"ok"}` from `/health`; the rollback records the revision serving traffic |
| A push fails "unauthorized"/"denied" late in a long build | The build job's token expired | Raise `access_token_lifetime` in the build job (now 1200s) |
| Deploy fails at "Record the revisions" with `grafana` not found | The workflow expects the Grafana service, which Terraform creates | `terraform apply` first ("Rolling out Grafana and tracing") |
| `terraform apply` wants to destroy and recreate a subscription | Its filter changed (filters are immutable) | Expected after an event-type change; apply when nothing is processing |
| Grafana panels say "No data" for Cloud Run or Pub/Sub only | The query proxy can't read Cloud Monitoring | Check the `grafana` service's `prometheus` container logs; its account needs `roles/monitoring.viewer` (`observability.tf`) |
