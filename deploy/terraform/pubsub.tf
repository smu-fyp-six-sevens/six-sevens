# One topic, a dead-letter topic, and one filtered push subscription per role (CLOUD_ARCHITECTURE_PLAN.md
# section 3). The filters come from the code: backend/app/events/types.py exports subscriptions.json.

locals {
  subscriptions = jsondecode(file("${path.module}/subscriptions.json"))
}

resource "google_pubsub_topic" "events" {
  #checkov:skip=CKV_GCP_83:Google-managed encryption; events carry ids and statuses, not document contents.
  name                       = "domain-events"
  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_topic" "dead_letter" {
  #checkov:skip=CKV_GCP_83:Google-managed encryption; same events as domain-events.
  name                       = "domain-events-dead-letter"
  message_retention_duration = "604800s"
  depends_on                 = [google_project_service.apis]
}

resource "google_pubsub_subscription" "push" {
  for_each = local.subscriptions
  name     = "${each.key}-push"
  topic    = google_pubsub_topic.events.id
  filter   = each.value.filter

  # The maximum; a run must finish inside it (detection takes 1-2 minutes), or it is redelivered.
  ack_deadline_seconds = 600

  push_config {
    push_endpoint = "${local.url[each.key]}/push/${each.key}"
    oidc_token {
      service_account_email = google_service_account.pubsub_push.email
      audience              = local.url[each.key]
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  expiration_policy {
    ttl = ""
  }
}

# The sweeper's clock (CLOUD_ARCHITECTURE_PLAN.md section 5): a sweep.requested event every 5 minutes (the shortest stage limit), which
# audit-metrics handles. Fields the envelope leaves out (event_id, occurred_at) are filled in when it is read.
resource "google_cloud_scheduler_job" "sweep" {
  name      = "sweep"
  region    = var.region
  schedule  = "*/5 * * * *"
  time_zone = "Etc/UTC"

  pubsub_target {
    topic_name = google_pubsub_topic.events.id
    data       = base64encode(jsonencode({ type = "sweep.requested", actor_type = "system", actor = "cloud-scheduler" }))
    attributes = { type = "sweep.requested" }
  }

  depends_on = [google_project_service.apis]
}

# Where dead-lettered messages can be read and replayed from.
resource "google_pubsub_subscription" "dead_letter" {
  name                 = "domain-events-dead-letter-pull"
  topic                = google_pubsub_topic.dead_letter.id
  ack_deadline_seconds = 60

  expiration_policy {
    ttl = ""
  }
}
