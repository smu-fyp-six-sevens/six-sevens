# Workload Identity on GKE: each pod's Kubernetes account (deploy/helm, <namespace>/<name>) acts as the same Google
# account its Cloud Run service uses (run-<name>), so the role table's grants carry over unchanged.

locals {
  # Google account name -> its pods' namespace. Backend roles live in a namespace named after the role.
  gke_namespaces = merge(
    { for role in keys(local.roles) : role => role },
    { kong = "gateway", frontend = "frontend", grafana = "observability" },
  )
}

resource "google_service_account_iam_member" "gke_workload_identity" {
  for_each           = var.gke_enabled ? local.gke_namespaces : {}
  service_account_id = google_service_account.role[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[${each.value}/${each.key}]"
  # The identity pool exists only once the cluster does.
  depends_on = [google_container_cluster.prod]
}

# Each consumer pulls its own subscription (events/pull.py). Granted from phase 1; used once migration.json lists it.
resource "google_pubsub_subscription_iam_member" "pull" {
  for_each     = var.gke_enabled ? local.subscriptions : {}
  subscription = google_pubsub_subscription.push[each.key].id
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.role[each.key].email}"
}

# KEDA reads each subscription's backlog from Cloud Monitoring, as its own account (installed once: README.md).
resource "google_service_account" "keda" {
  count        = var.gke_enabled ? 1 : 0
  account_id   = "gke-keda"
  display_name = "KEDA operator on GKE"
}

resource "google_project_iam_member" "keda_monitoring" {
  count   = var.gke_enabled ? 1 : 0
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.keda[0].email}"
}

resource "google_service_account_iam_member" "keda_workload_identity" {
  count              = var.gke_enabled ? 1 : 0
  service_account_id = google_service_account.keda[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[keda/keda-operator]"
  depends_on         = [google_container_cluster.prod]
}
