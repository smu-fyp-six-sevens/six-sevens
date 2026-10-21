locals {
  # Cloud Run's deterministic URL: known before the service exists, so the push audience and Kong's
  # upstreams can be set without a dependency cycle.
  url = { for name in concat(keys(local.roles), ["kong", "frontend"]) :
  name => "https://${name}-${data.google_project.this.number}.${var.region}.run.app" }

  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"

  # Terraform creates each service on this image; the deploy workflow then rolls out the real ones.
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"

  supabase   = ["SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"]
  # OneMap needs no token (ONEMAP_API_TOKEN is optional and not set in the cloud).
  registries = ["DATA_GOV_SG_API_KEY", "URA_API_KEY"]

  common_env = {
    EVENT_TRANSPORT         = "pubsub"
    GOOGLE_CLOUD_PROJECT    = var.project_id
    SUPABASE_URL            = var.supabase_url
    SUPABASE_STORAGE_BUCKET = var.supabase_storage_bucket
  }

  # One entry per backend role (CLOUD_ARCHITECTURE_PLAN.md section 2).
  roles = {
    api = {
      cpu         = "1"
      memory      = "1Gi"
      concurrency = 40
      timeout     = 3600
      # WebSocket tickets are held in memory, so one instance until they are signed instead.
      max       = 1
      public    = true
      publishes = true
      secrets   = concat(local.supabase, ["AUTH_USERS", "AUTH_SECRET_KEY"])
      env       = tomap({ CORS_IN_APP = "false" })
    }
    doc-worker = {
      cpu       = "2", memory = "4Gi", concurrency = 2, timeout = 600, max = 3
      public    = false
      publishes = true
      secrets   = concat(local.supabase, ["ANTHROPIC_API_KEY"])
      env       = tomap({ QUEUE_WORKER_CONCURRENCY = "2" })
    }
    detection-worker = {
      cpu       = "2", memory = "4Gi", concurrency = 2, timeout = 600, max = 3
      public    = false
      publishes = true
      secrets   = concat(local.supabase, ["ANTHROPIC_API_KEY"], local.registries)
      env       = tomap({ DETECTION_CONCURRENCY = "2" })
    }
    case-worker = {
      cpu       = "1", memory = "2Gi", concurrency = 4, timeout = 600, max = 2
      public    = false
      publishes = true
      secrets   = concat(local.supabase, ["ANTHROPIC_API_KEY"])
      env       = tomap({ CASE_ANALYSIS_TIMEOUT_SECONDS = "900" })
    }
    audit-metrics = {
      cpu         = "1"
      memory      = "1Gi"
      concurrency = 40
      timeout     = 60
      max         = 2
      # Public so Kong can reach it; its /push checks Pub/Sub's OIDC token itself.
      public    = true
      publishes = false
      secrets   = concat(local.supabase, ["AUTH_SECRET_KEY"])
      env = tomap({
        CORS_IN_APP          = "false"
        PUSH_AUDIENCE        = "https://audit-metrics-${data.google_project.this.number}.${var.region}.run.app"
        PUSH_SERVICE_ACCOUNT = "pubsub-push@${var.project_id}.iam.gserviceaccount.com"
      })
    }
  }

  all_secrets = toset(flatten([for role in values(local.roles) : role.secrets]))
}

resource "google_project_service" "apis" {
  for_each = toset([
    # These two must already be on before the first plan (README.md, one-time setup); listed so they stay on.
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "run.googleapis.com",
    "pubsub.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "billingbudgets.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "app"
  format        = "DOCKER"
  description   = "One image per role"

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  depends_on = [google_project_service.apis]
}
