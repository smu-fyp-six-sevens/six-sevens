# Releases on GKE (HOW_IT_WORKS.md §9, "Releases on GKE"): the deploy signs each image with one Cloud KMS key (cosign
# signature and SBOM, plus a Binary Authorization attestation), Binary Authorization admits only attested images,
# and Cloud Deploy rolls out the rendered chart, records it, runs the gateway checks, and rolls back.

# --- The signing key. Key rings can't be deleted: destroying this leaves the ring and schedules the key's versions
# for destruction.
resource "google_kms_key_ring" "signing" {
  count      = var.gke_enabled ? 1 : 0
  name       = "six-sevens"
  location   = var.region
  depends_on = [google_project_service.apis]
}

resource "google_kms_crypto_key" "image_signing" {
  #checkov:skip=CKV_GCP_43:Asymmetric signing keys can't rotate automatically; a new version is added by hand.
  count    = var.gke_enabled ? 1 : 0
  name     = "image-signing"
  key_ring = google_kms_key_ring.signing[0].id
  purpose  = "ASYMMETRIC_SIGN"
  version_template {
    algorithm        = "EC_SIGN_P256_SHA256"
    protection_level = "SOFTWARE"
  }
}

data "google_kms_crypto_key_version" "image_signing" {
  count      = var.gke_enabled ? 1 : 0
  crypto_key = google_kms_crypto_key.image_signing[0].id
}

# --- Binary Authorization: only images the deploy attested run on the cluster (GKE's own system images are exempt).
resource "google_container_analysis_note" "built_by_ci" {
  count = var.gke_enabled ? 1 : 0
  name  = "built-by-ci"
  attestation_authority {
    hint {
      human_readable_name = "Built, scanned and signed by the GitHub deploy"
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_binary_authorization_attestor" "built_by_ci" {
  count = var.gke_enabled ? 1 : 0
  name  = "built-by-ci"
  attestation_authority_note {
    note_reference = google_container_analysis_note.built_by_ci[0].name
    public_keys {
      id = data.google_kms_crypto_key_version.image_signing[0].id
      pkix_public_key {
        public_key_pem      = data.google_kms_crypto_key_version.image_signing[0].public_key[0].pem
        signature_algorithm = data.google_kms_crypto_key_version.image_signing[0].public_key[0].algorithm
      }
    }
  }
}

resource "google_binary_authorization_policy" "project" {
  count                         = var.gke_enabled ? 1 : 0
  global_policy_evaluation_mode = "ENABLE"

  # Installed once by hand (README.md), not built by us.
  admission_whitelist_patterns {
    name_pattern = "ghcr.io/kedacore/**"
  }
  # Google's Managed Prometheus query proxy beside Grafana.
  admission_whitelist_patterns {
    name_pattern = "gke.gcr.io/prometheus-engine/**"
  }

  default_admission_rule {
    evaluation_mode         = "REQUIRE_ATTESTATION"
    enforcement_mode        = var.binauthz_enforce ? "ENFORCED_BLOCK_AND_AUDIT_LOG" : "DRYRUN_AUDIT_LOG_ONLY"
    require_attestations_by = [google_binary_authorization_attestor.built_by_ci[0].name]
  }
}

# --- Cloud Deploy: one pipeline, one target (prod), no approval (decided), the gateway checks as its verify step.
resource "google_service_account" "clouddeploy" {
  count        = var.gke_enabled ? 1 : 0
  account_id   = "clouddeploy-runner"
  display_name = "Cloud Deploy: renders, applies and verifies on GKE"
}

resource "google_project_iam_member" "clouddeploy" {
  for_each = var.gke_enabled ? toset([
    "roles/clouddeploy.jobRunner",
    "roles/container.developer",
    "roles/logging.logWriter",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.clouddeploy[0].email}"
}

resource "google_clouddeploy_target" "prod" {
  count            = var.gke_enabled ? 1 : 0
  name             = "prod"
  location         = var.region
  require_approval = false
  gke {
    cluster = google_container_cluster.prod[0].id
  }
  execution_configs {
    usages           = ["RENDER", "DEPLOY", "VERIFY"]
    service_account  = google_service_account.clouddeploy[0].email
    artifact_storage = "gs://${google_storage_bucket.clouddeploy[0].name}/artifacts"
  }
  depends_on = [google_project_iam_member.clouddeploy, google_storage_bucket_iam_member.clouddeploy]
}

resource "google_clouddeploy_delivery_pipeline" "app" {
  count    = var.gke_enabled ? 1 : 0
  name     = "six-sevens"
  location = var.region
  serial_pipeline {
    stages {
      target_id = google_clouddeploy_target.prod[0].name
      strategy {
        standard {
          verify = true
        }
      }
    }
  }
}

# --- What the GitHub deploy needs on top of pushing images (github.tf).
resource "google_project_iam_member" "deployer_gke" {
  for_each = var.gke_enabled ? toset([
    "roles/clouddeploy.releaser",
    "roles/containeranalysis.occurrences.editor",
    # Reads the load balancer's address for Kong's trusted IPs.
    "roles/compute.viewer",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_clouddeploy" {
  count              = var.gke_enabled ? 1 : 0
  service_account_id = google_service_account.clouddeploy[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_kms_crypto_key_iam_member" "deployer_signs" {
  count         = var.gke_enabled ? 1 : 0
  crypto_key_id = google_kms_crypto_key.image_signing[0].id
  role          = "roles/cloudkms.signerVerifier"
  member        = "serviceAccount:${google_service_account.deployer.email}"
}

# cosign reads the key itself (cloudkms.cryptoKeys.get) before it signs.
resource "google_kms_crypto_key_iam_member" "deployer_reads_key" {
  count         = var.gke_enabled ? 1 : 0
  crypto_key_id = google_kms_crypto_key.image_signing[0].id
  role          = "roles/cloudkms.viewer"
  member        = "serviceAccount:${google_service_account.deployer.email}"
}

# --- Cloud Deploy's own files (each release's source and rendered manifests). The app keeps using Supabase Storage;
# this bucket only holds what the pipeline records.
resource "google_storage_bucket" "clouddeploy" {
  #checkov:skip=CKV_GCP_62:Access logs aren't needed for the pipeline's own release files; Cloud Deploy records each release.
  count                       = var.gke_enabled ? 1 : 0
  name                        = "${var.project_id}-clouddeploy"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "clouddeploy" {
  for_each = var.gke_enabled ? {
    "deployer-objects"  = { role = "roles/storage.objectAdmin", member = "serviceAccount:${google_service_account.deployer.email}" }
    "deployer-bucket"   = { role = "roles/storage.legacyBucketReader", member = "serviceAccount:${google_service_account.deployer.email}" }
    "execution-objects" = { role = "roles/storage.objectAdmin", member = "serviceAccount:${google_service_account.clouddeploy[0].email}" }
    "execution-bucket"  = { role = "roles/storage.legacyBucketReader", member = "serviceAccount:${google_service_account.clouddeploy[0].email}" }
  } : {}
  bucket = google_storage_bucket.clouddeploy[0].name
  role   = each.value.role
  member = each.value.member
}

resource "google_container_analysis_note_iam_member" "deployer_attests" {
  count  = var.gke_enabled ? 1 : 0
  note   = google_container_analysis_note.built_by_ci[0].name
  role   = "roles/containeranalysis.notes.attacher"
  member = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_binary_authorization_attestor_iam_member" "deployer_reads_attestor" {
  count    = var.gke_enabled ? 1 : 0
  attestor = google_binary_authorization_attestor.built_by_ci[0].name
  role     = "roles/binaryauthorization.attestorsViewer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}
