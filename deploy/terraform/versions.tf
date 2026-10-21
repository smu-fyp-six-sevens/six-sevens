terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # State lives in a GCS bucket created once by hand (see README.md here):
  #   terraform init -backend-config="bucket=<project-id>-tfstate"
  backend "gcs" {
    prefix = "six-sevens"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # Bill API calls to this project, not gcloud's own client project: the budget API refuses
  # local user credentials without a quota project.
  billing_project       = var.project_id
  user_project_override = true
}

data "google_project" "this" {}
