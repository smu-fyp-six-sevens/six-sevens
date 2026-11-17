# One image per role (HOW_IT_WORKS.md §9): build with --target <role>.
# api, doc-worker, detection-worker, case-worker, audit-metrics, or all (every role in one process).

FROM python:3.13-slim AS deps

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# upgrade: the base image's own packages get their security fixes before Debian rebuilds the image (Trivy).
RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libxcb1 \
        libgomp1 \
        fonts-liberation \
        fonts-dejavu-core \
        fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so a code change doesn't reinstall them. The lock pins every version (requirements.txt holds the ranges).
COPY backend/requirements.txt backend/requirements.lock ./
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.lock

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/app ./app
RUN mkdir -p ./document_templates ./documents_generated
RUN pip install --no-cache-dir --no-deps -e .

# --- test stage, not shipped ---
FROM deps AS test
COPY backend/requirements-dev.lock ./
RUN pip install --no-cache-dir -r requirements-dev.lock
COPY backend/tests ./tests

# --- what every role ships: no dev injection tool ---
FROM deps AS runtime
RUN rm -rf ./app/injection ./app/routers/injection.py ./document_templates ./documents_generated
# pip is only for building: it carries its own old copies of urllib3, msgpack and setuptools (Trivy findings).
RUN python -m pip uninstall -y pip
# Runs as a non-root user. It may write only ./data (the land-use index, built below) and its home (font caches).
RUN useradd --create-home --uid 10001 app && mkdir -p ./data && chown app ./data
ENV LAND_USE_RUNTIME_REBUILD=false
ENV PORT=8000
EXPOSE 8000
# Cloud Run ignores this and uses its own probes; compose and docker ps show it.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=4)"
USER app
# Cloud Run sends SIGTERM and allows 10 s; in-flight requests (a push's work) get most of it.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --timeout-graceful-shutdown 8"]

# The URA zoning index, for the roles that run detection.
FROM runtime AS with-land-use
ARG ALLOW_MISSING_LAND_USE=false
# uid: secrets are mounted readable by root only, and this stage runs as app.
RUN --mount=type=secret,id=data_gov_sg_api_key,uid=10001 \
    if [ -r /run/secrets/data_gov_sg_api_key ]; then export DATA_GOV_SG_API_KEY="$(cat /run/secrets/data_gov_sg_api_key)"; fi; \
    if python -c "from app.fraud_detection.external_validation.registry_lookups.land_use import cli; cli([])"; then :; \
    elif [ "$ALLOW_MISSING_LAND_USE" = "true" ]; then \
        echo "WARNING: URA land-use index not built (ALLOW_MISSING_LAND_USE=true); zoning checks will be skipped"; \
    else \
        echo "ERROR: could not build the URA land-use index; fix the cause or build with --build-arg ALLOW_MISSING_LAND_USE=true" >&2; \
        exit 1; \
    fi

# GIT_COMMIT (the deploy passes the commit) goes last in each stage, so a new commit rebuilds no cached layer.
# agent_versions.py records it beside each new agent version.
FROM runtime AS api
ENV ROLE=api
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT

FROM runtime AS doc-worker
ENV ROLE=doc-worker
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT

FROM with-land-use AS detection-worker
ENV ROLE=detection-worker
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT

FROM runtime AS case-worker
ENV ROLE=case-worker
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT

FROM runtime AS audit-metrics
ENV ROLE=audit-metrics
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT

# Every role in one process, as the single Cloud Run service ran before the split.
FROM with-land-use AS all
ENV ROLE=all
ARG GIT_COMMIT=local
ENV GIT_COMMIT=$GIT_COMMIT
