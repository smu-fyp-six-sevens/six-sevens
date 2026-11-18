locals {
  registry = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"

  # The role table (roles.json), shared with the Helm chart (deploy/helm/gen_values.py). Normalised so every role
  # has the same type.
  role_table = jsondecode(file("${path.module}/roles.json"))
  roles = { for name, role in local.role_table.roles : name => {
    cpu         = role.cpu
    memory      = role.memory
    concurrency = role.concurrency
    timeout     = role.timeout
    max         = role.max
    public      = role.public
    publishes   = role.publishes
    secrets     = tolist(role.secrets)
    env         = tomap(role.env)
  } }

  common_env = merge(local.role_table.common_env, {
    GOOGLE_CLOUD_PROJECT    = var.project_id
    SUPABASE_URL            = var.supabase_url
    SUPABASE_STORAGE_BUCKET = var.supabase_storage_bucket
  })

  all_secrets = toset(flatten([for role in values(local.roles) : role.secrets]))
}

resource "google_project_service" "apis" {
  for_each = toset([
    # These two must already be on before the first plan (README.md, one-time setup); listed so they stay on.
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
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
    # GKE (gke.tf, network.tf, edge.tf, supply_chain.tf): enabling an API costs nothing until something uses it.
    "container.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "certificatemanager.googleapis.com",
    "binaryauthorization.googleapis.com",
    "containeranalysis.googleapis.com",
    "clouddeploy.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudkms.googleapis.com",
    "storage.googleapis.com",
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

  # Checked 2026-11-01 by applying the policies to the image list: no serving image would be deleted.
  cleanup_policy_dry_run = false

  # KEEP beats DELETE: the 5 newest versions of each image (the serving one and four rollback targets) are never deleted.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "604800s" # 7 days
    }
  }

  # Every build moves the buildcache tags, leaving the previous cache untagged.
  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "86400s" # 1 day
    }
  }

  depends_on = [google_project_service.apis]
}
