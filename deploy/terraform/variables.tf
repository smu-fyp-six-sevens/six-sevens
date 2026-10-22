variable "project_id" {
  description = "The GCP project everything runs in."
  type        = string
}

variable "region" {
  description = "Cloud Run, Pub/Sub and Artifact Registry region."
  type        = string
  default     = "asia-southeast1"
}

variable "github_repo" {
  description = "owner/name of the GitHub repository allowed to deploy through Workload Identity Federation."
  type        = string
}

variable "supabase_url" {
  description = "The Supabase project URL (not a secret)."
  type        = string
}

variable "supabase_storage_bucket" {
  type    = string
  default = "documents"
}

variable "min_instances" {
  description = "Minimum instances per service. 0 scales to zero; raise api, kong and the workers for a demo."
  type        = map(number)
  default     = {}
}

variable "extra_frontend_origins" {
  description = "Origins besides the frontend service's own URLs that may call the API (e.g. a custom domain)."
  type        = list(string)
  default     = []
}

variable "metrics_sidecar" {
  description = "Run Google's Managed Prometheus sidecar next to each backend service."
  type        = bool
  default     = true
}

variable "gmp_sidecar_image" {
  description = "Managed Prometheus sidecar for Cloud Run; check the newest tag before applying."
  type        = string
  default     = "us-docker.pkg.dev/cloud-ops-agents-artifacts/cloud-run-gmp-sidecar/cloud-run-gmp-sidecar:1.2.0"
}

variable "grafana_proxy_image" {
  description = "Google's Prometheus query proxy, beside Grafana; check the newest tag before applying."
  type        = string
  default     = "gke.gcr.io/prometheus-engine/frontend:v0.15.3-gke.0"
}

variable "billing_account" {
  description = "Billing account id for the budget alert; empty skips the budget."
  type        = string
  default     = ""
}

variable "alert_email" {
  description = "Where alert policies send email (monitoring.tf); empty creates none."
  type        = string
  default     = ""
}

variable "alert_daily_claude_usd" {
  description = "Alert when Claude spend over the last 24 hours passes this many USD."
  type        = number
  default     = 10
}

variable "billing_export_table" {
  description = "Once the Cloud Billing export to BigQuery is on: the table it created in billing_export, e.g. gcp_billing_export_v1_0123AB_45CD67_89EF01. Empty skips the costs view Grafana reads."
  type        = string
  default     = ""
}

variable "billing_export_location" {
  description = "Location of the billing_export dataset; the export accepts the US and EU multi-regions everywhere."
  type        = string
  default     = "US"
}

variable "budget_amount_sgd" {
  description = "Monthly budget; alerts at 50% and 90%."
  type        = number
  default     = 50
}
