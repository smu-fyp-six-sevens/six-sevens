# Grafana's admin password and read access. Grafana itself runs on GKE (deploy/helm, the observability namespace)
# and reads Managed Prometheus through Google's query proxy beside it, signing each query as its service account.

resource "random_password" "grafana_admin" {
  length  = 24
  special = false
}

resource "google_secret_manager_secret" "grafana_admin" {
  secret_id = "grafana-admin-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "grafana_admin" {
  secret      = google_secret_manager_secret.grafana_admin.id
  secret_data = random_password.grafana_admin.result
}

resource "google_secret_manager_secret_iam_member" "grafana_admin" {
  secret_id = google_secret_manager_secret.grafana_admin.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role["grafana"].email}"
}

# Read metrics (Managed Prometheus and Cloud Monitoring's GKE and Pub/Sub metrics).
resource "google_project_iam_member" "grafana_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.role["grafana"].email}"
}
