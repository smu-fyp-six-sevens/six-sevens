locals {
  # Cloud Run's deterministic URL: known before the service exists, so the push audience and Kong's
  # upstreams can be set without a dependency cycle.
  url = { for name in concat(keys(local.roles), ["kong", "frontend", "grafana"]) :
  name => "https://${name}-${data.google_project.this.number}.${var.region}.run.app" }

  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"

  # Terraform creates each service on this image; the deploy workflow then rolls out the real ones.
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"

  supabase = ["SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY"]
  # OneMap needs no token (ONEMAP_API_TOKEN is optional and not set in the cloud).
  registries = ["DATA_GOV_SG_API_KEY", "URA_API_KEY"]

  common_env = {
    EVENT_TRANSPORT         = "pubsub"
    GOOGLE_CLOUD_PROJECT    = var.project_id
    SUPABASE_URL            = var.supabase_url
    SUPABASE_STORAGE_BUCKET = var.supabase_storage_bucket
    # Traces to Cloud Trace, one service name each (K_SERVICE).
    OTEL_ENABLED = "true"
    # Cases created here need every required document approved before analysis (cases.checklist_enforced).
    REQUIRE_COMPLETE_CHECKLIST = "true"
  }

  # One entry per backend role (CLOUD_ARCHITECTURE_PLAN.md section 2).
  roles = {
    api = {
      cpu         = "1"
      memory      = "1Gi"
      concurrency = 40
      timeout     = 3600
      # WebSocket tickets are signed, and each instance has its own Realtime listener, so api scales out.
      max       = 3
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
      env       = tomap({ QUEUE_WORKER_CONCURRENCY = "2", CPU_WORKER_PROCESSES = "2" })
    }
    detection-worker = {
      cpu       = "2", memory = "4Gi", concurrency = 2, timeout = 600, max = 3
      public    = false
      publishes = true
      secrets   = concat(local.supabase, ["ANTHROPIC_API_KEY"], local.registries)
      env       = tomap({ DETECTION_CONCURRENCY = "2", CPU_WORKER_PROCESSES = "2" })
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
      public = true
      # It runs the sweeper (Cloud Scheduler's sweep.requested), which publishes recovery requests.
      publishes = true
      secrets   = concat(local.supabase, ["AUTH_SECRET_KEY"])
      env = tomap({
        SWEEPER_ENABLED      = "true"
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
    "cloudscheduler.googleapis.com",
    "bigquery.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "app" {
  #checkov:skip=CKV_GCP_84:Google-managed encryption; images hold code, no secrets or documents.
  location      = var.region
  repository_id = "app"
  format        = "DOCKER"
  description   = "One image per role"

  # Logs what the policies would delete without deleting; set false once the dry run looks right (PENDING_WORK.md).
  cleanup_policy_dry_run = true

  # KEEP beats DELETE: the 10 newest versions of each image (rollback targets) are never deleted.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "1209600s" # 14 days
    }
  }

  # Every build moves the buildcache tags, leaving the previous cache untagged.
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }

  depends_on = [google_project_service.apis]
}
