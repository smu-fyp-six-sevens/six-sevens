# Grafana with its data sources and dashboards baked in, so an instance needs no state: Cloud Run may stop it
# at any time (CLOUD_ARCHITECTURE_PLAN.md section 8). Built from the repository root.
FROM grafana/grafana:12.4.10

# Reads the Cloud Billing export (the Cost dashboard's "Billed" row). Installed at build, not at start, so a cold
# start doesn't download it. Pinned: the CLI doesn't enforce a plugin's Grafana requirement, so an unpinned
# install can pick a version this Grafana can't load (3.4.6 needs Grafana >= 12.2.5 or >= 11.6.11; bump them together).
RUN grafana cli plugins install grafana-bigquery-datasource 3.4.6

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
# Cloud Run ignores this and uses its own probes; compose and docker ps show it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s CMD ["curl", "-fsS", "-o", "/dev/null", "http://127.0.0.1:3000/api/health"]
# The base image's user, stated explicitly.
USER grafana
