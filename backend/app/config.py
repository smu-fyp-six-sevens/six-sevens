import os

from dotenv import load_dotenv


load_dotenv()


def _env(name: str, default: str = "") -> str:
    """
    os.environ.get, stripped — a copy-pasted secret easily picks up a
    trailing newline, which is silently harmless for most settings but
    fatal as an HTTP header value (e.g. SUPABASE_PUBLISHABLE_KEY).
    """
    return os.environ.get(name, default).strip()


class Settings:
    """
    Centralised config. All values come from environment variables so
    nothing sensitive (API keys, service role key) is hardcoded.
    """
    # Shared-credential auth for the public demo (see app/auth.py).
    # AUTH_SECRET_KEY signs bearer tokens — must be a real random value in
    # any deployed environment; the "" default is only safe for local dev.
    AUTH_USERNAME: str = _env("AUTH_USERNAME")
    AUTH_PASSWORD: str = _env("AUTH_PASSWORD")
    AUTH_SECRET_KEY: str = _env("AUTH_SECRET_KEY")

    # Source of truth for "is this process the cloud deployment or local
    # dev" — local and cloud currently share one Supabase project, so this
    # drives case_repository's dev flag and the queue's dev-case filtering.
    # Set to "cloud" only by the Cloud Run deploy workflow.
    DEPLOYMENT_ENV: str = _env("DEPLOYMENT_ENV", "dev")
    IS_CLOUD_DEPLOYMENT: bool = DEPLOYMENT_ENV.lower() == "cloud"

    # Comma-separated list of exact origins the frontend is served from, in
    # addition to local dev.
    ALLOWED_ORIGINS: list[str] = [
        origin.strip()
        for origin in _env("ALLOWED_ORIGINS").split(",")
        if origin.strip()
    ] + ["http://localhost:5173"]

    # Regex alternative to ALLOWED_ORIGINS: Cloud Run hands one service
    # multiple valid hostnames, so pinning to one exact origin is fragile.
    # Set by the deploy workflow.
    ALLOWED_ORIGIN_REGEX: str | None = _env("ALLOWED_ORIGIN_REGEX") or None

    SUPABASE_URL: str = _env("SUPABASE_URL")
    SUPABASE_PUBLISHABLE_KEY: str = _env("SUPABASE_PUBLISHABLE_KEY")
    # Bypasses RLS — backend-only, for the Realtime subscription
    # (realtime_listener.py). Never sent to the frontend.
    SUPABASE_SECRET_KEY: str = _env("SUPABASE_SECRET_KEY")
    SUPABASE_STORAGE_BUCKET: str = _env("SUPABASE_STORAGE_BUCKET", "documents")

    # AC2: invocation should be attempted within this window of being dequeued
    DEQUEUE_TO_INVOKE_TARGET_SECONDS: float = 1.0
    # A document idle in queued/processing longer than this is "stuck"
    # (drives the monitoring dashboard's stuck-document listing).
    STUCK_THRESHOLD_SECONDS: int = 600
    # Separate, longer threshold specifically for retry eligibility: a
    # document sitting in PROCESSING (not any other in-flight status) past
    # this long is considered stuck and can be retried without waiting for
    # it to reach FAILED. See routers/documents.py's retry_document.
    STUCK_PROCESSING_RETRY_THRESHOLD_SECONDS: int = 600

    # Bounded classify<->extract escalation loop (langgraph/graph.py) — one
    # re-classification attempt before routing to manual review.
    MAX_RECLASSIFICATION_ATTEMPTS: int = 1

    # Concurrent documents processed by the in-memory queue worker pool
    # (queue_service.py) — bounded by Anthropic rate limits, not local resources.
    QUEUE_WORKER_CONCURRENCY: int = int(os.environ.get("QUEUE_WORKER_CONCURRENCY", "4"))

    # Threads for PDF/OCR/forensic work (cpu_pool.py). 1 = PyMuPDF only ever runs on one thread.
    CPU_WORKER_CONCURRENCY: int = int(os.environ.get("CPU_WORKER_CONCURRENCY", "1"))
    # Detection runs (lead + 3 specialists each) allowed at once; the rest wait their turn.
    DETECTION_CONCURRENCY: int = int(os.environ.get("DETECTION_CONCURRENCY", "2"))
    # Most model calls one detection run may make (lead, specialists, reviews, binders). Past it the run
    # stops asking, reports what it has, and the verdict cannot be "clean".
    DETECTION_MAX_MODEL_CALLS: int = int(os.environ.get("DETECTION_MAX_MODEL_CALLS", "30"))
    # Most model calls one document's classify and extract run may make (agent turns, the two classifier
    # tools, a repair, extraction and its one retry, and one reclassification).
    DOCUMENT_MAX_MODEL_CALLS: int = int(os.environ.get("DOCUMENT_MAX_MODEL_CALLS", "24"))
    # Retries the Anthropic client makes by itself on a rate limit or 5xx; each one is billed again.
    LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "1"))
    # One model request. Extraction can write 16k tokens, so this is generous.
    LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))
    # One detection tool. This includes waiting for the single CPU worker, so it is not the check's own
    # run time. The slow ones call a model or an outside registry.
    TOOL_TIMEOUT_SECONDS: float = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "60"))
    SLOW_TOOL_TIMEOUT_SECONDS: float = float(os.environ.get("SLOW_TOOL_TIMEOUT_SECONDS", "120"))

    # Below this, classification is routed to manual review regardless of
    # the guessed document_type (see langgraph/nodes._is_below_threshold).
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.85

    # Below this, extracted fields render as editable so a loan officer can
    # correct them. Set above CLASSIFICATION_CONFIDENCE_THRESHOLD since a
    # wrong-looking field is costlier than a wrongly-routed document.
    EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.9

    ALLOWED_CONTENT_TYPES: set[str] = {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB
    MAX_BULK_FILES: int = 20

    # Signed URLs need to outlive a slow run, not just one request.
    SIGNED_URL_EXPIRY_SECONDS: int = 1800

    # --- LLM pipeline (backend/app/langgraph) ---
    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY")
    # Two-tier models: Sonnet for
    # classifier/vision/extraction/detection-lead, Haiku for
    # llm_classify/repair/specialists. Overridable per-tier.
    LANGGRAPH_SONNET_MODEL: str = _env("LANGGRAPH_SONNET_MODEL", "claude-sonnet-4-6")
    LANGGRAPH_HAIKU_MODEL: str = _env("LANGGRAPH_HAIKU_MODEL", "claude-haiku-4-5-20251001")
    # Ceiling on the classify agent's tool-call loop and the full
    # record_escalation -> reclassify -> validate -> extract pass —
    # comfortably covers MAX_RECLASSIFICATION_ATTEMPTS with room to spare.
    LANGGRAPH_RECURSION_LIMIT: int = 60

    # --- Observability (see telemetry.py) ---
    # Off by default: importing the SDK is harmless with no cost/behaviour
    # change until this is set.
    OTEL_ENABLED: bool = os.environ.get("OTEL_ENABLED", "false").lower() == "true"
    # httpx's own INFO line per Supabase call. Off by default: query_stats.py logs one summary per unit of work.
    LOG_HTTP_REQUESTS: bool = os.environ.get("LOG_HTTP_REQUESTS", "false").lower() == "true"
    # Required for the Cloud Trace exporter; left unset -> traces print to
    # the console instead.
    GOOGLE_CLOUD_PROJECT: str = _env("GOOGLE_CLOUD_PROJECT", "")
    OTEL_SERVICE_NAME: str = _env("OTEL_SERVICE_NAME", "six-sevens-backend")

    # --- Public registry lookups (fraud_detection/external_validation/registry_lookups) ---
    # Both optional: without them the anonymous limits apply, and a lookup
    # that hits its rate limit is reported as unable to assess.
    DATA_GOV_SG_API_KEY: str = _env("DATA_GOV_SG_API_KEY")
    ONEMAP_API_TOKEN: str = _env("ONEMAP_API_TOKEN")
    # URA Data Service access key (eservice.ura.gov.sg/maps/api): for "Approved Residential
    # Use". Optional: without it that lookup is skipped and zoning alone is used.
    URA_API_KEY: str = _env("URA_API_KEY")
    # URA zoning index (registry_lookups/land_use.py): whether a running server may download
    # and build it itself (minutes, ~700 MB) when it's missing or URA has updated the map.
    # The Docker image builds it in and sets this to false; local runs keep it on.
    LAND_USE_RUNTIME_REBUILD: bool = os.environ.get("LAND_USE_RUNTIME_REBUILD", "true").lower() == "true"

    # --- Per-case analysis (case_pipeline/) ---
    # Kill switch: off falls back to document-level detection and scoring only.
    CASE_ANALYSIS_ENABLED: bool = os.environ.get("CASE_ANALYSIS_ENABLED", "true").lower() == "true"


settings = Settings()
