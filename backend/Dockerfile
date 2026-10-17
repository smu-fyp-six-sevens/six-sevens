FROM python:3.13-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
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

COPY backend/pyproject.toml ./pyproject.toml
COPY backend/requirements.txt ./requirements.txt
COPY backend/app ./app
RUN mkdir -p ./document_templates ./documents_generated
RUN pip install --no-cache-dir --retries 10 --timeout 120 -e .

ARG ALLOW_MISSING_LAND_USE=false
RUN --mount=type=secret,id=data_gov_sg_api_key \
    if [ -r /run/secrets/data_gov_sg_api_key ]; then export DATA_GOV_SG_API_KEY="$(cat /run/secrets/data_gov_sg_api_key)"; fi; \
    if python -c "from app.fraud_detection.external_validation.registry_lookups.land_use import cli; cli([])"; then :; \
    elif [ "$ALLOW_MISSING_LAND_USE" = "true" ]; then \
        echo "WARNING: URA land-use index not built (ALLOW_MISSING_LAND_USE=true); zoning checks will be skipped"; \
    else \
        echo "ERROR: could not build the URA land-use index; fix the cause or build with --build-arg ALLOW_MISSING_LAND_USE=true" >&2; \
        exit 1; \
    fi
ENV LAND_USE_RUNTIME_REBUILD=false

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- test stage, not shipped to prod ---
FROM base AS test
RUN pip install --no-cache-dir .[dev]
COPY backend/tests ./tests