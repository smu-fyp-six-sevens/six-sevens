# Terraform: the Cloud Run deployment

Creates everything the services need on GCP (CLOUD_ARCHITECTURE_PLAN.md sections 6-8):

| File | What |
|---|---|
| `main.tf` | APIs, Artifact Registry, the role table (size, scaling, secrets, environment per service) |
| `iam.tf` | a service account per service, Secret Manager secrets and who may read each, publish rights, metric writing, Pub/Sub's service agent |
| `pubsub.tf` | `domain-events`, its dead-letter topic, one filtered push subscription per role (filters from `subscriptions.json`, exported by `python -m app.events.types`) |
| `run.tf` | Cloud Run: api, doc-worker, detection-worker, case-worker, audit-metrics (each with the Managed Prometheus sidecar), Kong, frontend |
| `github.tf` | Workload Identity Federation for GitHub Actions and the deployer account |
| `monitoring.tf` | the budget alert (when `billing_account` is set) |

Terraform owns each service's configuration. Services are created on a placeholder image (Cloud Run's "Congratulations" page, which answers 200 on every path); the deploy workflow (`.github/workflows/deploy.yml`) rolls out the real images and Terraform ignores image changes.

Only the person running the cloud needs anything here. Teammates who run `uvicorn` + `npm run dev` need only `backend/.env`, as before. Deploying needs no local files either: the workflow runs on GitHub from the repository variables, so anyone with write access can run it.

## Status (2026-10-22)

Project `fyp-six-sevens` (number `1076848636975`), region `asia-southeast1`, repo `smu-fyp-six-sevens/six-sevens`, deploying from the `microservices` branch.

Done: the state bucket, `terraform apply` (the existing `github-deployer` account and `app` repository were imported, not recreated), the secret values, the GitHub repository variables. Deploy runs so far have each failed on one of the problems in "Problems met" below, all fixed in the workflow; the run after the traffic fix had not happened yet. Whether the budget alert applies depends on your billing permissions (see below).

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
4. Metrics: Cloud Console → Monitoring → Metrics Explorer → PromQL, e.g. `sum by (route) (rate(http_requests_total[5m]))`. Data appears a few minutes after the services get traffic.

A secret changed later reaches a service on its next revision (the next deploy, or `gcloud run services update <service> --region asia-southeast1`). Configuration (sizes, scaling, environment, secrets) changes here, with `terraform apply`, never in the workflow.

## Clean-up after the first successful deploy

- Delete the old single-service deploy: `gcloud run services delete fyp-backend --region asia-southeast1`, and the same for `fyp-frontend`.
- Delete the GitHub secrets the new workflow no longer reads: `GCP_SA_KEY`, `SUPABASE_*`, `ANTHROPIC_API_KEY`, `AUTH_*`, `ONEMAP_API_TOKEN`, `URA_API_KEY`, and any `GCP_PROJECT_ID` or `GCP_REGION` *secrets* (the variables of the same names stay). Keep `DATA_GOV_SG_API_KEY`.
- Delete `set-secrets.ps1` if it still exists.
- Optional: `docker image rm six-sevens/api:check six-sevens/kong:check` (local images built to check the Dockerfiles).

## Optional, any time

- A `SNYK_TOKEN` repository secret makes CI use Snyk instead of `pip-audit` and `npm audit`.
- Decide where Grafana runs (Grafana Cloud free tier or one small instance; plan decision 5).
- For a demo, `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }` in `terraform.tfvars`, then `terraform apply`, avoids cold starts (below).
- To let only `main` deploy, add `assertion.ref == "refs/heads/main"` to the `attribute_condition` in `github.tf` (after merging).

## Notes

- **Scale to zero.** Every service defaults to 0 minimum instances. Cloud Run keeps an idle instance for up to about 15 minutes after its last request (for a worker, its last push; Google publishes no exact figure), then stops it; the next event waits for a cold start, roughly 10-30 s here because of the LangGraph and OCR imports. Idle instances cost nothing with CPU idle.
- **api is capped at one instance** while WebSocket tickets are held in its memory.
- **Public services** are api, audit-metrics, Kong and the frontend (each still checks the app's token). Workers can be invoked only by the `pubsub-push` account; audit-metrics' `/push` checks that account's OIDC token itself.
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
