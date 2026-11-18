output "kong_url" {
  description = "The API entry point (Kong on GKE); the frontend's VITE_API_URL."
  value       = "https://api.${var.domain}"
}

output "frontend_url" {
  value = "https://${var.domain}"
}

output "grafana_url" {
  description = "Log in as admin with: terraform output -raw grafana_admin_password"
  value       = "https://grafana.${var.domain}"
}

output "grafana_admin_password" {
  value     = random_password.grafana_admin.result
  sensitive = true
}

output "registry" {
  value = local.registry
}

# For the repository variables the deploy workflow reads.
output "github_variables" {
  value = {
    GCP_PROJECT_ID   = var.project_id
    GCP_REGION       = var.region
    GCP_WIF_PROVIDER = google_iam_workload_identity_pool_provider.github.name
    GCP_DEPLOYER_SA  = google_service_account.deployer.email
  }
}

output "secrets_to_fill" {
  description = "Secret Manager secrets whose values must be added by hand."
  value       = sort([for s in google_secret_manager_secret.secret : s.secret_id])
}

# --- GKE ---

output "dns_name_servers" {
  description = "Set these as sixsevens.me's name servers at the registrar."
  value       = var.gke_enabled ? google_dns_managed_zone.public[0].name_servers : []
}

output "gke" {
  description = "The cluster, its addresses, and the KEDA account for its one-time install (README.md)."
  value = var.gke_enabled ? {
    cluster      = google_container_cluster.prod[0].name
    lb_address   = google_compute_global_address.lb[0].address
    nat_address  = google_compute_address.nat[0].address
    keda_account = google_service_account.keda[0].email
  } : null
}
