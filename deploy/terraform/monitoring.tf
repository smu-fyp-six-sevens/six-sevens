# Budget alert (CLOUD_ARCHITECTURE_PLAN.md section 6) and alert policies emailed to var.alert_email.

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

# Alert policies, in PromQL over the same metrics the Grafana dashboards read. Off until alert_email is set.
locals {
  alerts = {
    dead-letter = {
      name = "Events dead-lettered"
      doc  = "An event failed every delivery attempt. Read it from the domain-events-dead-letter-pull subscription; the worker's logs say why."
      # Any message moved to the dead-letter topic in the last 10 minutes.
      query    = "sum(increase(pubsub_googleapis_com:subscription_dead_letter_message_count[10m])) > 0"
      duration = "0s"
    }
    errors = {
      name = "API 5xx above 5%"
      doc  = "More than 5% of API requests failed for 10 minutes. Services dashboard: 5xx by route."
      # Only with some traffic, so one failed request at 3 am doesn't page anyone.
      query    = "(sum(rate(http_requests_total{status=~\"5..\",route!~\"/push.*\"}[5m])) / sum(rate(http_requests_total{route!~\"/push.*\"}[5m])) > 0.05) and sum(rate(http_requests_total{route!~\"/push.*\"}[5m])) > 0.05"
      duration = "600s"
    }
    claude-spend = {
      name     = "Claude spend over ${var.alert_daily_claude_usd} USD a day"
      doc      = "Claude spend in the last 24 hours passed the daily limit. Cost dashboard: spend by agent and model."
      query    = "sum(increase(llm_cost_usd_total[24h])) > ${var.alert_daily_claude_usd}"
      duration = "0s"
    }
    recovery-exhausted = {
      name     = "Stuck work could not be recovered"
      doc      = "The sweeper gave up on a document or case analysis. Pipeline dashboard: stuck work; the audit trail shows recovery.exhausted with the document."
      query    = "sum(increase(recoveries_total{result=\"exhausted\"}[30m])) > 0"
      duration = "0s"
    }
  }
}

resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  display_name = "six-sevens alerts"
  type         = "email"
  labels       = { email_address = var.alert_email }
  depends_on   = [google_project_service.apis]
}

resource "google_monitoring_alert_policy" "alert" {
  for_each     = var.alert_email == "" ? {} : local.alerts
  display_name = each.value.name
  combiner     = "OR"

  conditions {
    display_name = each.value.name
    condition_prometheus_query_language {
      query               = each.value.query
      duration            = each.value.duration
      evaluation_interval = "60s"
      # Our metrics only exist once a service has had traffic; don't refuse the policy before that.
      disable_metric_validation = true
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
