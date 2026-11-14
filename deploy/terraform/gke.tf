# The GKE Autopilot cluster (CLOUD_ARCHITECTURE_PLAN.md sections 13-24). Regional (survives a zone loss), private
# nodes, Workload Identity (always on in Autopilot), the Secret Manager add-on, Binary Authorization, Gateway API.
# Terraform owns the cluster; the Helm chart (deploy/helm), released by Cloud Deploy, owns what runs in it.
# Billing starts with the first apply that sets gke_enabled = true (lean profile: roughly $130-150 a month, idle included).

# The nodes' own account, with only what nodes need (not the default compute account).
resource "google_service_account" "gke_nodes" {
  count        = var.gke_enabled ? 1 : 0
  account_id   = "gke-nodes"
  display_name = "GKE Autopilot nodes"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = var.gke_enabled ? toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/stackdriver.resourceMetadata.writer",
    "roles/autoscaling.metricsWriter",
  ]) : toset([])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes[0].email}"
}

resource "google_artifact_registry_repository_iam_member" "gke_nodes_pull" {
  count      = var.gke_enabled ? 1 : 0
  location   = var.region
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.gke_nodes[0].email}"
}

resource "google_container_cluster" "prod" {
  #checkov:skip=CKV_GCP_20:The control plane takes Cloud Deploy's jobs from Google's build workers, so no fixed source range; every call is IAM-authorised.
  #checkov:skip=CKV_GCP_21:Labels are set on the chart's objects, not the cluster.
  #checkov:skip=CKV_GCP_65:Google Groups for RBAC is not set up; access is by IAM.
  #checkov:skip=CKV_GCP_66:Binary Authorization is on (project policy, supply_chain.tf).
  count    = var.gke_enabled ? 1 : 0
  name     = "six-sevens"
  location = var.region

  enable_autopilot    = true
  deletion_protection = false

  network    = google_compute_network.gke[0].id
  subnetwork = google_compute_subnetwork.gke[0].id
  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  release_channel {
    channel = "REGULAR"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.gke_nodes[0].email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  secret_manager_config {
    enabled = true
  }

  binary_authorization {
    evaluation_mode = "PROJECT_SINGLETON_POLICY_ENFORCE"
  }

  gateway_api_config {
    channel = "CHANNEL_STANDARD"
  }

  security_posture_config {
    mode               = "BASIC"
    vulnerability_mode = "VULNERABILITY_BASIC"
  }

  depends_on = [google_project_service.apis, google_project_iam_member.gke_nodes]
}
