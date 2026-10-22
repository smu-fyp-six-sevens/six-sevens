# Grafana with its data source and dashboards baked in, so an instance needs no state: Cloud Run may stop it
# at any time (CLOUD_ARCHITECTURE_PLAN.md section 8). Built from the repository root.
FROM grafana/grafana:11.4.0

COPY deploy/grafana/provisioning /etc/grafana/provisioning
COPY deploy/grafana/dashboards /var/lib/grafana/dashboards

# PROMETHEUS_URL: Google's query proxy beside Grafana on Cloud Run; the local Prometheus in compose.
ENV PROMETHEUS_URL=http://localhost:9090 \
    GF_SERVER_HTTP_PORT=3000 \
    GF_AUTH_ANONYMOUS_ENABLED=false \
    GF_USERS_ALLOW_SIGN_UP=false \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false \
    GF_NEWS_NEWS_FEED_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/services.json

EXPOSE 3000
