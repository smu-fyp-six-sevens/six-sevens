FROM kong:3.9.1

# The base image's OS packages lag their fixes (Trivy in deploy.yml reports them).
USER root
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*

COPY deploy/kong/kong.yml.tmpl /kong/kong.yml.tmpl
COPY --chmod=0755 deploy/kong/render.sh /kong/render.sh

ENV PORT=8000
EXPOSE 8000
# Cloud Run ignores this and uses its own probes; compose and docker ps show it.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s CMD ["kong", "health"]
# The base image's user, stated explicitly; render.sh writes only to /tmp.
USER kong
ENTRYPOINT ["/kong/render.sh"]
