# One service account per service, with only what it needs (HOW_IT_WORKS.md §9).

resource "google_service_account" "role" {
  for_each     = toset(concat(keys(local.roles), ["kong", "frontend", "grafana"]))
  account_id   = "run-${each.key}"
  display_name = "Cloud Run: ${each.key}"
}

# The identity Pub/Sub signs pushes with; the only principal allowed to invoke the workers.
resource "google_service_account" "pubsub_push" {
  account_id   = "pubsub-push"
  display_name = "Pub/Sub push to workers"
}

# Secrets: Terraform creates the containers; their values are added by hand (README.md here).
resource "google_secret_manager_secret" "secret" {
  for_each  = local.all_secrets
  secret_id = lower(replace(each.value, "_", "-"))

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "access" {
  for_each = { for pair in flatten([
    for role, cfg in local.roles : [for secret in cfg.secrets : { role = role, secret = secret }]
  ]) : "${pair.role}/${pair.secret}" => pair }

  secret_id = google_secret_manager_secret.secret[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role[each.value.role].email}"
}

# Kong isn't a backend role, so its one secret is granted here: the key it sends api and audit-metrics.
resource "google_secret_manager_secret_iam_member" "kong_gateway_secret" {
  secret_id = google_secret_manager_secret.secret["GATEWAY_SECRET"].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.role["kong"].email}"
}

resource "google_pubsub_topic_iam_member" "publisher" {
  for_each = { for role, cfg in local.roles : role => cfg if cfg.publishes }
  topic    = google_pubsub_topic.events.id
  role     = "roles/pubsub.publisher"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

# Traces from every backend service.
resource "google_project_iam_member" "trace" {
  for_each = toset(keys(local.roles))
  project  = var.project_id
  role     = "roles/cloudtrace.agent"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

# The Managed Prometheus sidecar writes metrics as the service's account (Kong's too).
resource "google_project_iam_member" "metric_writer" {
  for_each = toset(concat(keys(local.roles), ["kong"]))
  project  = var.project_id
  role     = "roles/monitoring.metricWriter"
  member   = "serviceAccount:${google_service_account.role[each.key].email}"
}

# Pub/Sub's own service agent: mints push tokens, and moves failed messages to the dead-letter topic.
locals {
  pubsub_agent = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_service_account_iam_member" "pubsub_token_creator" {
  service_account_id = google_service_account.pubsub_push.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.pubsub_agent
}

resource "google_pubsub_topic_iam_member" "dead_letter_publisher" {
  topic  = google_pubsub_topic.dead_letter.id
  role   = "roles/pubsub.publisher"
  member = local.pubsub_agent
}

resource "google_pubsub_subscription_iam_member" "dead_letter_subscriber" {
  for_each     = google_pubsub_subscription.push
  subscription = each.value.id
  role         = "roles/pubsub.subscriber"
  member       = local.pubsub_agent
}
