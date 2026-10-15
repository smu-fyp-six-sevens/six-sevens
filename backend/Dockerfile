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

# URA land-use zoning for the address checks (app/fraud_detection/registry_lookups/land_use.py):
# downloaded from data.gov.sg and built into the image (~12 MB, a couple of minutes), so a
# running server never has to. If data.gov.sg can't be reached the image still builds, without
# it: zoning is then skipped, with the reason, until the next deploy.
RUN python -c "from app.fraud_detection.registry_lookups.land_use import download_and_build; download_and_build()" \
    || echo "WARNING: URA land-use index not built; address zoning checks will be skipped"
ENV LAND_USE_RUNTIME_REBUILD=false

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# --- test stage, not shipped to prod ---
FROM base AS test
RUN pip install --no-cache-dir .[dev]
COPY backend/tests ./tests