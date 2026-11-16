# The cluster's network (HOW_IT_WORKS.md §9, GKE design): private nodes, and all egress through Cloud NAT on
# one static address. Created only with gke_enabled.

resource "google_compute_network" "gke" {
  count                   = var.gke_enabled ? 1 : 0
  name                    = "six-sevens"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

# Nothing comes in unless a rule allows it. GKE adds its own rules (nodes, control plane, the load balancer's health
# checks) at priority 1000, above this one.
resource "google_compute_firewall" "deny_ingress" {
  count     = var.gke_enabled ? 1 : 0
  name      = "six-sevens-deny-ingress"
  network   = google_compute_network.gke[0].id
  direction = "INGRESS"
  priority  = 65534

  deny {
    protocol = "all"
  }
  source_ranges = ["0.0.0.0/0"]

  log_config {
    metadata = "INCLUDE_ALL_METADATA"
  }
}

resource "google_compute_subnetwork" "gke" {
  count         = var.gke_enabled ? 1 : 0
  name          = "six-sevens-${var.region}"
  region        = var.region
  network       = google_compute_network.gke[0].id
  ip_cidr_range = "10.10.0.0/20"
  # Google APIs (Pub/Sub, Secret Manager, Artifact Registry) without leaving Google's network.
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.16.0.0/14"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.20.0.0/20"
  }

  log_config {
    aggregation_interval = "INTERVAL_5_MIN"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# The one address all egress leaves from (Supabase, Anthropic, the registries): what a bank's firewall would allow-list.
resource "google_compute_address" "nat" {
  count      = var.gke_enabled ? 1 : 0
  name       = "six-sevens-nat"
  region     = var.region
  depends_on = [google_project_service.apis]
}

resource "google_compute_router" "gke" {
  count   = var.gke_enabled ? 1 : 0
  name    = "six-sevens"
  region  = var.region
  network = google_compute_network.gke[0].id
}

resource "google_compute_router_nat" "gke" {
  count                              = var.gke_enabled ? 1 : 0
  name                               = "six-sevens"
  router                             = google_compute_router.gke[0].name
  region                             = var.region
  nat_ip_allocate_option             = "MANUAL_ONLY"
  nat_ips                            = [google_compute_address.nat[0].self_link]
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
