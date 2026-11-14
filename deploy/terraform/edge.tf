# The front door on GKE (CLOUD_ARCHITECTURE_PLAN.md section 15): sixsevens.me in Cloud DNS, one Google-managed
# certificate (checked through DNS), the load balancer's static address, and Cloud Armor. The chart's Gateway
# (deploy/helm/six-sevens/templates/gateway.yaml) uses the address, the certificate map and the policy by name.

resource "google_compute_global_address" "lb" {
  count      = var.gke_enabled ? 1 : 0
  name       = "sixsevens-lb"
  depends_on = [google_project_service.apis]
}

# The domain's DNS. After the first apply, set the registrar's name servers to `terraform output dns_name_servers`.
resource "google_dns_managed_zone" "public" {
  count       = var.gke_enabled ? 1 : 0
  name        = replace(var.domain, ".", "-")
  dns_name    = "${var.domain}."
  description = "Six Sevens front door"

  dnssec_config {
    state = "on"
  }

  depends_on = [google_project_service.apis]
}

resource "google_dns_record_set" "front_door" {
  for_each     = var.gke_enabled ? toset(["", "api.", "grafana."]) : toset([])
  managed_zone = google_dns_managed_zone.public[0].name
  name         = "${each.value}${var.domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_global_address.lb[0].address]
}

# The certificate covers the domain and every name under it, proved through a DNS record in the zone above.
resource "google_certificate_manager_dns_authorization" "domain" {
  count      = var.gke_enabled ? 1 : 0
  name       = replace(var.domain, ".", "-")
  domain     = var.domain
  depends_on = [google_project_service.apis]
}

resource "google_dns_record_set" "certificate_check" {
  count        = var.gke_enabled ? 1 : 0
  managed_zone = google_dns_managed_zone.public[0].name
  name         = google_certificate_manager_dns_authorization.domain[0].dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.domain[0].dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.domain[0].dns_resource_record[0].data]
}

resource "google_certificate_manager_certificate" "front_door" {
  count = var.gke_enabled ? 1 : 0
  name  = replace(var.domain, ".", "-")
  managed {
    domains            = [var.domain, "*.${var.domain}"]
    dns_authorizations = [google_certificate_manager_dns_authorization.domain[0].id]
  }
}

resource "google_certificate_manager_certificate_map" "front_door" {
  count      = var.gke_enabled ? 1 : 0
  name       = "sixsevens"
  depends_on = [google_project_service.apis]
}

resource "google_certificate_manager_certificate_map_entry" "front_door" {
  count        = var.gke_enabled ? 1 : 0
  name         = "sixsevens-primary"
  map          = google_certificate_manager_certificate_map.front_door[0].name
  certificates = [google_certificate_manager_certificate.front_door[0].id]
  matcher      = "PRIMARY"
}

# Cloud Armor in front of Kong, the frontend and Grafana: OWASP rules and a per-address limit before anything
# reaches Kong (which keeps its per-officer limits). Rules start in preview (logged, not enforced): set
# cloud_armor_preview = false once the logs show no false positives on normal use. Uploads are left out of the
# body rules, since a PDF's bytes can look like an attack.
locals {
  waf_rules = {
    1000 = "sqli-v33-stable"
    1010 = "xss-v33-stable"
    1020 = "lfi-v33-stable"
    1030 = "rfi-v33-stable"
    1040 = "rce-v33-stable"
    1050 = "scannerdetection-v33-stable"
    1060 = "protocolattack-v33-stable"
    1070 = "sessionfixation-v33-stable"
  }
}

resource "google_compute_security_policy" "edge" {
  count       = var.gke_enabled ? 1 : 0
  name        = "sixsevens-edge"
  description = "Edge rules for sixsevens.me"

  dynamic "rule" {
    for_each = local.waf_rules
    content {
      priority = rule.key
      action   = "deny(403)"
      preview  = var.cloud_armor_preview
      match {
        expr {
          expression = "evaluatePreconfiguredWaf('${rule.value}', {'sensitivity': 1}) && !request.path.startsWith('/documents/upload')"
        }
      }
      description = rule.value
    }
  }

  # Log4j and similar known exploits (CKV_GCP_73).
  rule {
    priority = 900
    action   = "deny(403)"
    preview  = var.cloud_armor_preview
    match {
      expr {
        expression = "evaluatePreconfiguredExpr('cve-canary')"
      }
    }
    description = "Known CVEs, including Log4j"
  }

  # Per client address: well above an officer's normal use, low enough to stop a flood.
  rule {
    priority = 2000
    action   = "throttle"
    preview  = var.cloud_armor_preview
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 600
        interval_sec = 60
      }
    }
    description = "600 requests a minute per address"
  }

  rule {
    priority = 2147483647
    action   = "allow"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default: allow"
  }

  depends_on = [google_project_service.apis]
}
