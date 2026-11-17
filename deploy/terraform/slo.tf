# Uptime checks and service-level objectives (HOW_IT_WORKS.md §9, "Uptime and SLOs"). GKE only: they watch
# sixsevens.me. Free or near it: three regions every 5 minutes stay inside the uptime checks' free allowance, and the
# SLOs read metrics already collected. The Grafana "Status" dashboard shows the same numbers.

locals {
  uptime_checks = var.gke_enabled ? {
    site = { name = "Site (${var.domain})", host = var.domain, path = "/", content = "" }
    # The API's own health reply through Kong, not just any 200.
    api = { name = "API (api.${var.domain})", host = "api.${var.domain}", path = "/health", content = "\"status\":\"ok\"" }
  } : {}

  # Prometheus metrics from the app (Managed Service for Prometheus), in Cloud Monitoring's filter language.
  api_requests = join(" AND ", [
    "metric.type=\"prometheus.googleapis.com/http_requests_total/counter\"",
    "resource.type=\"prometheus_target\"",
    "resource.labels.namespace=\"api\"",
    # Probes and uptime checks, not officers.
    "NOT metric.labels.route=\"/health\"",
  ])

  slos = var.gke_enabled ? {
    api-availability = {
      name = "99% of API requests succeed"
      goal = 0.99
      doc  = "The API is failing more requests than its 1% error budget allows. Services dashboard: 5xx by route."
    }
    detection-latency = {
      name = "95% of detection runs finish within 3 minutes"
      goal = 0.95
      doc  = "Detection runs are slower than their objective. Pipeline dashboard: stage timings; Agents dashboard: Claude latency."
    }
  } : {}
}

resource "google_monitoring_uptime_check_config" "check" {
  for_each     = local.uptime_checks
  display_name = each.value.name
  timeout      = "10s"
  period       = "300s"
  # The minimum is three regions.
  selected_regions = ["ASIA_PACIFIC", "EUROPE", "USA_OREGON"]

  http_check {
    path           = each.value.path
    port           = 443
    use_ssl        = true
    validate_ssl   = true
    request_method = "GET"
    accepted_response_status_codes {
      status_class = "STATUS_CLASS_2XX"
    }
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = each.value.host
    }
  }

  dynamic "content_matchers" {
    for_each = each.value.content == "" ? [] : [each.value.content]
    content {
      content = content_matchers.value
      matcher = "CONTAINS_STRING"
    }
  }

  depends_on = [google_project_service.apis]
}

# Down from more than one region for 10 minutes: one region's blip doesn't email anyone.
resource "google_monitoring_alert_policy" "uptime" {
  for_each     = var.alert_email == "" ? {} : local.uptime_checks
  display_name = "${each.value.name} is down"
  combiner     = "OR"

  conditions {
    display_name = "${each.value.name} failing from more than one region"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.labels.check_id=\"${google_monitoring_uptime_check_config.check[each.key].uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "600s"
      aggregations {
        alignment_period     = "1200s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
      trigger {
        count = 1
      }
    }
  }

  documentation {
    content   = "Uptime checks fail from several regions. Check the GKE workloads and the load balancer; the Status dashboard shows when it started."
    mime_type = "text/markdown"
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]
}

resource "google_monitoring_custom_service" "app" {
  count        = var.gke_enabled ? 1 : 0
  service_id   = "six-sevens"
  display_name = "Six Sevens"
  depends_on   = [google_project_service.apis]
}

resource "google_monitoring_slo" "api_availability" {
  count               = var.gke_enabled ? 1 : 0
  service             = google_monitoring_custom_service.app[0].service_id
  slo_id              = "api-availability"
  display_name        = local.slos["api-availability"].name
  goal                = local.slos["api-availability"].goal
  rolling_period_days = 28

  request_based_sli {
    good_total_ratio {
      bad_service_filter   = "${local.api_requests} AND metric.labels.status=monitoring.regex.full_match(\"5..\")"
      total_service_filter = local.api_requests
    }
  }
}

resource "google_monitoring_slo" "detection_latency" {
  count               = var.gke_enabled ? 1 : 0
  service             = google_monitoring_custom_service.app[0].service_id
  slo_id              = "detection-latency"
  display_name        = local.slos["detection-latency"].name
  goal                = local.slos["detection-latency"].goal
  rolling_period_days = 28

  request_based_sli {
    # Each finished run's duration, from its document.detected event (metrics.stage_duration).
    distribution_cut {
      distribution_filter = join(" AND ", [
        "metric.type=\"prometheus.googleapis.com/stage_duration_seconds/histogram\"",
        "resource.type=\"prometheus_target\"",
        "metric.labels.stage=\"document.detected\"",
      ])
      range {
        min = 0
        max = 180
      }
    }
  }
}

# Burn-rate alerts (the usual pair): fast, the last hour spending budget 14.4 times too quickly (2% of 28 days' budget
# in an hour); slow, the last 6 hours at 6 times.
resource "google_monitoring_alert_policy" "slo_burn" {
  for_each     = var.alert_email == "" ? {} : local.slos
  display_name = "SLO burning: ${each.value.name}"
  combiner     = "OR"

  conditions {
    display_name = "Fast burn (1 hour)"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${each.key == "api-availability" ? google_monitoring_slo.api_availability[0].name : google_monitoring_slo.detection_latency[0].name}\", \"3600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 14.4
      duration        = "0s"
    }
  }

  conditions {
    display_name = "Slow burn (6 hours)"
    condition_threshold {
      filter          = "select_slo_burn_rate(\"${each.key == "api-availability" ? google_monitoring_slo.api_availability[0].name : google_monitoring_slo.detection_latency[0].name}\", \"21600s\")"
      comparison      = "COMPARISON_GT"
      threshold_value = 6
      duration        = "0s"
    }
  }

  documentation {
    content   = each.value.doc
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email[0].id]
}
