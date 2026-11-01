# Grafana on Cloud Run (CLOUD_ARCHITECTURE_PLAN.md section 8), and the scrape config for Kong's metrics.
#
# Grafana reads Managed Prometheus through Google's query proxy, a sidecar that signs each query as Grafana's
# service account, so no key is stored anywhere. Its dashboards and data source are baked into the image
# (deploy/grafana); the instance keeps no state and scales to zero like everything else.

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

# Read metrics (Managed Prometheus and Cloud Monitoring's Cloud Run and Pub/Sub metrics).
resource "google_project_iam_member" "grafana_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.role["grafana"].email}"
}

resource "google_cloud_run_v2_service" "grafana" {
  name                = "grafana"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.role["grafana"].email

    scaling {
      min_instance_count = lookup(var.min_instances, "grafana", 0)
      # Logins live in the instance's own SQLite; one instance keeps them consistent.
      max_instance_count = 1
    }

    # The deploy workflow updates this one by name (`--container grafana`).
    containers {
      name  = "grafana"
      image = local.placeholder_image

      ports {
        container_port = 3000
      }

      resources {
        limits            = { cpu = "1", memory = "512Mi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      env {
        name  = "GF_SERVER_ROOT_URL"
        value = local.url["grafana"]
      }
      env {
        name  = "PROMETHEUS_URL"
        value = "http://localhost:9090"
      }
      # The BigQuery data source (billing export) runs its queries in this project.
      env {
        name  = "GCP_PROJECT"
        value = var.project_id
      }
      env {
        name  = "BIGQUERY_LOCATION"
        value = var.billing_export_location
      }
      env {
        name = "GF_SECURITY_ADMIN_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.grafana_admin.secret_id
            version = "latest"
          }
        }
      }
    }

    containers {
      name  = "prometheus"
      image = var.grafana_proxy_image
      args  = ["--web.listen-address=:9090", "--query.project-id=${var.project_id}"]
      resources {
        limits            = { cpu = "500m", memory = "256Mi" }
        cpu_idle          = true
        startup_cpu_boost = true
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }

  depends_on = [google_secret_manager_secret_version.grafana_admin, google_secret_manager_secret_iam_member.grafana_admin]
}

# Kong's prometheus plugin serves /metrics on its status port, not the default :8080 the sidecar scrapes.
resource "google_secret_manager_secret" "kong_gmp_config" {
  secret_id = "kong-gmp-config"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "kong_gmp_config" {
  secret      = google_secret_manager_secret.kong_gmp_config.id
  secret_data = <<-YAML
    apiVersion: monitoring.googleapis.com/v1beta
    kind: RunMonitoring
    metadata:
      name: kong
    spec:
      endpoints:
      - port: 8100
        path: /metrics
        interval: 30s
  YAML
}

resource "google_secret_manager_secret_iam_member" "kong_gmp_config" {
  secret_id = google_secret_manager_secret.kong_gmp_config.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role["kong"].email}"
}
