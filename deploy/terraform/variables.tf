variable "project_id" {
  description = "The GCP project everything runs in."
  type        = string
}

variable "region" {
  description = "GKE, Pub/Sub and Artifact Registry region."
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

# --- GKE (HOW_IT_WORKS.md §9) ---

variable "gke_enabled" {
  description = "Create the GKE cluster and everything around it (network, NAT, DNS, certificate, Cloud Armor, signing, Cloud Deploy). Starts billing, idle included."
  type        = bool
  default     = false
}

variable "domain" {
  description = "The front door: the frontend at the apex, api. and grafana. under it."
  type        = string
  default     = "sixsevens.me"
}

variable "cloud_armor_preview" {
  description = "Cloud Armor rules only log (true) or also block (false). Start in preview; switch once the logs show no false positives."
  type        = bool
  default     = true
}

variable "binauthz_enforce" {
  description = "Binary Authorization blocks unattested images (true) or only logs them (false, dry run). Enforce once a release passes in dry run."
  type        = bool
  default     = false
}
