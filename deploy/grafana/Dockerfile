# Grafana with its data sources and dashboards baked in, so an instance needs no state: Cloud Run may stop it
# at any time (CLOUD_ARCHITECTURE_PLAN.md section 8). Built from the repository root.
FROM grafana/grafana:11.4.0

# Reads the Cloud Billing export (the Cost dashboard's "Billed" row). Installed at build, not at start, so a cold
# start doesn't download it; the CLI picks the newest version compatible with this Grafana.
RUN grafana cli plugins install grafana-bigquery-datasource

# Settings in a file rather than ENV; secrets (the admin password) only ever arrive at run time.
COPY deploy/grafana/grafana.ini /etc/grafana/grafana.ini
COPY deploy/grafana/provisioning /etc/grafana/provisioning
COPY deploy/grafana/dashboards /var/lib/grafana/dashboards

# Where the data sources point (deploy/grafana/provisioning/datasources): Google's query proxy beside Grafana on
# Cloud Run, the local Prometheus in compose. Not secrets; Terraform and compose override them.
ENV PROMETHEUS_URL=http://localhost:9090 \
    GCP_PROJECT="" \
    BIGQUERY_LOCATION=US

EXPOSE 3000
