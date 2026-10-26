output "kong_url" {
  description = "The API entry point; the frontend's VITE_API_URL."
  value       = google_cloud_run_v2_service.kong.uri
}

output "frontend_url" {
  value = google_cloud_run_v2_service.frontend.uri
}

output "grafana_url" {
  description = "Log in as admin with: terraform output -raw grafana_admin_password"
  value       = google_cloud_run_v2_service.grafana.uri
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
