# Cloud Run services. Terraform owns their configuration; the deploy workflow only changes the image.

resource "google_cloud_run_v2_service" "role" {
  for_each            = local.roles
  name                = each.key
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.role[each.key].email
    timeout                          = "${each.value.timeout}s"
    max_instance_request_concurrency = each.value.concurrency

    scaling {
      min_instance_count = lookup(var.min_instances, each.key, 0)
      max_instance_count = each.value.max
    }

    # The deploy workflow updates this one by name (`--container app`).
    containers {
      name  = "app"
      image = local.placeholder_image

      ports {
        container_port = 8080
      }

      resources {
        limits            = { cpu = each.value.cpu, memory = each.value.memory }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.common_env, each.value.env)
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = toset(each.value.secrets)
        content {
          name = env.value
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = "latest"
            }
          }
        }
      }
    }

    # Managed Prometheus: with no config it scrapes :8080/metrics every 60 s. While CPU is idle between
    # requests (cpu_idle) a scrape can be delayed, so short gaps appear; counters stay correct.
    dynamic "containers" {
      for_each = var.metrics_sidecar ? [1] : []
      content {
        name  = "collector"
        image = var.gmp_sidecar_image
        # Cloud Run turns the boost on for the roles' sidecar; set here so plans stop reporting it.
        resources {
          limits            = { cpu = "500m", memory = "256Mi" }
          cpu_idle          = true
          startup_cpu_boost = true
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }

  depends_on = [google_secret_manager_secret_iam_member.access]
}

resource "google_cloud_run_v2_service" "kong" {
  name                = "kong"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.role["kong"].email
    # The case WebSocket runs through Kong; Cloud Run caps a connection at 60 minutes, the client reconnects.
    timeout                          = "3600s"
    max_instance_request_concurrency = 200

    scaling {
      min_instance_count = lookup(var.min_instances, "kong", 0)
      max_instance_count = 2
    }

    # The deploy workflow updates this one by name (`--container kong`).
    containers {
      name  = "kong"
      image = local.placeholder_image

      ports {
        container_port = 8080
      }

      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }

      env {
        name  = "API_URL"
        value = local.url["api"]
      }
      env {
        name  = "AUDIT_URL"
        value = local.url["audit-metrics"]
      }
      # Cloud Run routes by Host, so the upstream must see its own.
      env {
        name  = "PRESERVE_HOST"
        value = "false"
      }
      env {
        name  = "CORS_ORIGINS"
        value = join(",", concat([local.url["frontend"], google_cloud_run_v2_service.frontend.uri], var.extra_frontend_origins))
      }
    }

    # Managed Prometheus for Kong's own metrics, with the scrape config from observability.tf (port 8100).
    dynamic "containers" {
      for_each = var.metrics_sidecar ? [1] : []
      content {
        name  = "collector"
        image = var.gmp_sidecar_image
        resources {
          limits   = { cpu = "500m", memory = "256Mi" }
          cpu_idle = true
        }
        volume_mounts {
          name       = "gmp-config"
          mount_path = "/etc/rungmp"
        }
      }
    }

    dynamic "volumes" {
      for_each = var.metrics_sidecar ? [1] : []
      content {
        name = "gmp-config"
        secret {
          secret = google_secret_manager_secret.kong_gmp_config.secret_id
          items {
            version = "latest"
            path    = "config.yaml"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }

  depends_on = [google_secret_manager_secret_version.kong_gmp_config, google_secret_manager_secret_iam_member.kong_gmp_config]
}

resource "google_cloud_run_v2_service" "frontend" {
  name                = "frontend"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = google_service_account.role["frontend"].email

    scaling {
      min_instance_count = lookup(var.min_instances, "frontend", 0)
      max_instance_count = 2
    }

    containers {
      image = local.placeholder_image

      ports {
        # nginx in the real image listens on 80; the placeholder ignores it.
        container_port = 80
      }

      resources {
        limits   = { cpu = "1", memory = "256Mi" }
        cpu_idle = true
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }
}

# Public: the officer-facing entry points. Checkov flags these; the reason is recorded in its skip.
resource "google_cloud_run_v2_service_iam_member" "public" {
  for_each = merge(
    { for role, cfg in local.roles : role => google_cloud_run_v2_service.role[role].name if cfg.public },
    {
      kong     = google_cloud_run_v2_service.kong.name
      frontend = google_cloud_run_v2_service.frontend.name
      grafana  = google_cloud_run_v2_service.grafana.name # behind Grafana's own login
    },
  )
  location = var.region
  name     = each.value
  role     = "roles/run.invoker"
  member   = "allUsers" #checkov:skip=CKV_GCP_102:Officer-facing entry points; the app checks its own token.
}

# Only Pub/Sub's push identity may invoke the workers (and audit-metrics' /push, checked in the app).
resource "google_cloud_run_v2_service_iam_member" "push_invoker" {
  for_each = local.subscriptions
  location = var.region
  name     = google_cloud_run_v2_service.role[each.key].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.pubsub_push.email}"
}
