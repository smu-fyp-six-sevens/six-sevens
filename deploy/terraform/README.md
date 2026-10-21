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

Terraform owns each service's configuration. Services are created on a placeholder image; the deploy workflow (`.github/workflows/deploy.yml`) rolls out the real images and Terraform ignores image changes.

## One-time setup

```bash
gcloud auth application-default login
gcloud config set project <project-id>

# 1. A bucket for the state
gcloud storage buckets create gs://<project-id>-tfstate --location asia-southeast1 --uniform-bucket-level-access

# 2. Variables
cp terraform.tfvars.example terraform.tfvars   # fill in project_id, github_repo, supabase_url

# 3. Create everything
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform apply

# 4. Secret values (names in the secrets_to_fill output)
printf '%s' "$VALUE" | gcloud secrets versions add anthropic-api-key --data-file=-
#    ...and supabase-publishable-key, supabase-secret-key, auth-users, auth-secret-key,
#    data-gov-sg-api-key, onemap-api-token, ura-api-key

# 5. GitHub: repository variables from the github_variables output
terraform output github_variables
```

Then set `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER` and `GCP_DEPLOYER_SA` as repository **variables** (Settings → Secrets and variables → Actions → Variables), keep the `DATA_GOV_SG_API_KEY` repository secret (a build secret for the zoning index), and run "Deploy to Cloud Run" from the Actions tab.

A secret changed later reaches a service on its next revision (the next deploy, or `gcloud run services update <service> --region asia-southeast1`).

## Notes

- **Scale to zero.** Every service defaults to 0 minimum instances. Cloud Run keeps an idle instance for up to about 15 minutes after its last request (for a worker, its last push), then stops it; the next event waits for a cold start, roughly 10-30 s here because of the LangGraph and OCR imports. For a demo set `min_instances = { api = 1, kong = 1, "doc-worker" = 1, "detection-worker" = 1 }`.
- **api is capped at one instance** while WebSocket tickets are held in its memory.
- **Public services** are api, audit-metrics, Kong and the frontend (each still checks the app's token). Workers can be invoked only by the `pubsub-push` account; audit-metrics' `/push` checks that account's OIDC token itself.
- **The sidecar** (`gmp_sidecar_image`): check the newest tag before applying. With CPU idle between requests, scrapes can be delayed and short gaps appear.
- **Old services** `fyp-backend` and `fyp-frontend` from the single-service deploy can be deleted once these work.
