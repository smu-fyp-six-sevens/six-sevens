# Budget alert (CLOUD_ARCHITECTURE_PLAN.md section 6). Grafana and Managed Prometheus come in step 7.

resource "google_billing_budget" "monthly" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "six-sevens monthly"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  amount {
    specified_amount {
      currency_code = "SGD"
      units         = tostring(var.budget_amount_sgd)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }

  depends_on = [google_project_service.apis]
}
