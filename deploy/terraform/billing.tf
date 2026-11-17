# Billed cost in Grafana, from Cloud Billing's export to BigQuery (README.md: "Billed cost in Grafana").
#
# Terraform makes the dataset; a billing account admin turns the export on in the console (there is no API for
# it), choosing this project and dataset. Once its table appears, set billing_export_table and apply again: that
# creates billing_export.costs, a view with stable column names, which the Cost dashboard's "Billed" row reads.

resource "google_bigquery_dataset" "billing" {
  #checkov:skip=CKV_GCP_81:Google-managed encryption; billing lines are not sensitive enough to justify a KMS key.
  dataset_id    = "billing_export"
  friendly_name = "Cloud Billing export"
  description   = "Standard usage cost export, written by Cloud Billing; read by Grafana through the costs view."
  location      = var.billing_export_location

  # No access blocks: they'd make Terraform own the dataset's permissions and remove the writer entry
  # Cloud Billing adds when the export is turned on.
  depends_on = [google_project_service.apis]
}

# Grafana's account reads the view, which reads the export table in the same dataset.
resource "google_bigquery_dataset_iam_member" "grafana_reader" {
  dataset_id = google_bigquery_dataset.billing.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.role["grafana"].email}"
}

resource "google_bigquery_table" "costs" {
  #checkov:skip=CKV_GCP_80:A view holds no data of its own.
  #checkov:skip=CKV_GCP_121:A view, recreated from its query; protection would block changing billing_export_table.
  count               = var.billing_export_table == "" ? 0 : 1
  dataset_id          = google_bigquery_dataset.billing.dataset_id
  table_id            = "costs"
  deletion_protection = false

  view {
    use_legacy_sql = false
    # One row per usage line of this project. Credits (the free tier, promotions) are negative amounts.
    query = <<-SQL
      SELECT
        usage_start_time AS time,
        invoice.month AS invoice_month,
        service.description AS service,
        sku.description AS sku,
        cost,
        IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0) AS credits,
        cost + IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0) AS net_cost,
        currency,
        -- GKE cost allocation (gke.tf): the namespace a cluster line was for; null for everything else.
        (SELECT l.value FROM UNNEST(labels) AS l WHERE l.key = 'k8s-namespace' LIMIT 1) AS namespace
      FROM `${var.project_id}.${google_bigquery_dataset.billing.dataset_id}.${var.billing_export_table}`
      WHERE project.id = '${var.project_id}'
    SQL
  }
}

# Running a query is a project-level permission; reading data comes from the dataset access above.
resource "google_project_iam_member" "grafana_bigquery_jobs" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.role["grafana"].email}"
}
