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


ALL_ROLES = ("api", "doc-worker", "detection-worker", "case-worker", "audit-metrics")


def _roles(raw: str) -> frozenset[str]:
    """ROLE: "all" (one process runs everything), one role, or a comma list. An unknown role stops the app."""
    names = {name.strip() for name in raw.split(",") if name.strip()} or {"all"}
    if "all" in names:
        return frozenset(ALL_ROLES)
    unknown = names - set(ALL_ROLES)
    if unknown:
        raise ValueError(f"Unknown ROLE {sorted(unknown)}; expected 'all' or some of {list(ALL_ROLES)}")
    return frozenset(names)


def _users(raw: str) -> dict[str, str]:
    """AUTH_USERS: "alice:password1;bob:password2". A malformed entry stops the app at start-up."""
    users: dict[str, str] = {}
    for entry in raw.split(";"):
        if not entry.strip():
            continue
        name, separator, password = entry.partition(":")
        name, password = name.strip(), password.strip()
        if not separator or not name or not password:
            raise ValueError('AUTH_USERS must look like "alice:password1;bob:password2"')
        users[name] = password
    return users


class Settings:
    """
    Centralised config. All values come from environment variables so
    nothing sensitive (API keys, service role key) is hardcoded.
    """
    # Every account that can log in, in one place (see app/auth.py).
    # AUTH_SECRET_KEY signs bearer tokens — must be a real random value in
    # any deployed environment; the "" default is only safe for local dev.
    AUTH_USERS: dict[str, str] = _users(_env("AUTH_USERS"))
    AUTH_SECRET_KEY: str = _env("AUTH_SECRET_KEY")
    # The accounts that can change app-wide settings (routers/settings.py), comma-separated. Every other
    # account is a loan officer. No role system beyond this list.
    ADMIN_USERS: frozenset[str] = frozenset(
        name.strip() for name in _env("ADMIN_USERS", "carol").split(",") if name.strip()
    )

    # --- Services and events (CLOUD_ARCHITECTURE_PLAN.md) ---
    # Which roles this process runs; the default runs all of them, as uvicorn always has.
    ROLES: frozenset[str] = _roles(_env("ROLE", "all"))
    # "inprocess" calls handlers in this process; "pubsub" publishes to Pub/Sub (or the emulator
    # when PUBSUB_EMULATOR_HOST is set) and workers receive events on POST /push.
    EVENT_TRANSPORT: str = _env("EVENT_TRANSPORT", "inprocess").lower()
    PUBSUB_TOPIC: str = _env("PUBSUB_TOPIC", "domain-events")
    PUBSUB_PUBLISH_TIMEOUT_SECONDS: float = float(os.environ.get("PUBSUB_PUBLISH_TIMEOUT_SECONDS", "30"))
    # When set, POST /push requires a Google OIDC token for this audience from PUSH_SERVICE_ACCOUNT.
    # Needed on a public service (audit-metrics sits behind Kong); private workers are also guarded by Cloud Run IAM.
    PUSH_AUDIENCE: str = _env("PUSH_AUDIENCE")
    PUSH_SERVICE_ACCOUNT: str = _env("PUSH_SERVICE_ACCOUNT")
    # Off behind Kong, which answers CORS itself; two sets of CORS headers make browsers refuse the response.
    CORS_IN_APP: bool = os.environ.get("CORS_IN_APP", "true").lower() == "true"
    # A case analysis still "running" after this long was interrupted; the sweeper then marks it failed.
    CASE_ANALYSIS_TIMEOUT_SECONDS: int = int(os.environ.get("CASE_ANALYSIS_TIMEOUT_SECONDS", "900"))

    # --- Stuck work and recovery (sweeper.py, CLOUD_ARCHITECTURE_PLAN.md section 16) ---
    # Off by default: the database is shared, so a local process must never sweep cloud work. Terraform turns it on.
    SWEEPER_ENABLED: bool = os.environ.get("SWEEPER_ENABLED", "false").lower() == "true"
    # uploaded/queued past this means its event was lost; processing/extracting/detecting past the other means
    # its run died (both well past the 600 s push deadline, so a recovery never races a live redelivery).
    STUCK_WAITING_SECONDS: int = int(os.environ.get("STUCK_WAITING_SECONDS", "300"))
    STUCK_RUNNING_SECONDS: int = int(os.environ.get("STUCK_RUNNING_SECONDS", "900"))
    RECOVERY_MAX_ATTEMPTS: int = int(os.environ.get("RECOVERY_MAX_ATTEMPTS", "3"))
    # How often a running pipeline or detection checks for a cancel made through another instance (pubsub only).
    CANCEL_POLL_SECONDS: float = float(os.environ.get("CANCEL_POLL_SECONDS", "5"))

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

    # Documents one doc-worker process runs at once (workers/documents.py) — bounded by Anthropic rate limits.
    QUEUE_WORKER_CONCURRENCY: int = int(os.environ.get("QUEUE_WORKER_CONCURRENCY", "4"))

    # Threads for PDF/OCR/forensic work (cpu_pool.py). 1 = PyMuPDF only ever runs on one thread.
    CPU_WORKER_CONCURRENCY: int = int(os.environ.get("CPU_WORKER_CONCURRENCY", "1"))
    # Processes for that work instead (each with its own PyMuPDF); 0 keeps the thread. Set on the deployed workers.
    CPU_WORKER_PROCESSES: int = int(os.environ.get("CPU_WORKER_PROCESSES", "0"))
    # Detection runs (lead + 3 specialists each) allowed at once; the rest wait their turn.
    DETECTION_CONCURRENCY: int = int(os.environ.get("DETECTION_CONCURRENCY", "2"))
    # Most model calls one detection run may make (lead, specialists, reviews, binders). Past it the run
    # stops asking, reports what it has, and the verdict cannot be "clean".
    DETECTION_MAX_MODEL_CALLS: int = int(os.environ.get("DETECTION_MAX_MODEL_CALLS", "30"))
    # Most model calls one document's classify and extract run may make (agent turns, the two classifier
    # tools, a repair, extraction and its one retry, and one reclassification).
    DOCUMENT_MAX_MODEL_CALLS: int = int(os.environ.get("DOCUMENT_MAX_MODEL_CALLS", "24"))
    # A document the content guard finds worded as an instruction to the AI fails at ingest, before classification
    # or extraction (guardrails/content_guard.blocking_reason). "false" lets it through, flagged instead.
    GUARD_BLOCK_INJECTION: bool = os.environ.get("GUARD_BLOCK_INJECTION", "true").lower() == "true"
    # Retries the Anthropic client makes by itself on a rate limit or 5xx; each one is billed again.
    LLM_MAX_RETRIES: int = int(os.environ.get("LLM_MAX_RETRIES", "1"))
    # One model request. Extraction can write 16k tokens, so this is generous.
    LLM_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_TIMEOUT_SECONDS", "300"))
    # One detection tool. This includes waiting for the single CPU worker, so it is not the check's own
    # run time. The slow ones call a model or an outside registry.
    TOOL_TIMEOUT_SECONDS: float = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "60"))
    SLOW_TOOL_TIMEOUT_SECONDS: float = float(os.environ.get("SLOW_TOOL_TIMEOUT_SECONDS", "120"))
    # The most a tool waits for the CPU worker before it starts; its own limit only runs once it has started.
    CPU_QUEUE_TIMEOUT_SECONDS: float = float(os.environ.get("CPU_QUEUE_TIMEOUT_SECONDS", "300"))

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
    LANGGRAPH_SONNET_MODEL: str = _env("LANGGRAPH_SONNET_MODEL", "claude-sonnet-5-5")
    LANGGRAPH_HAIKU_MODEL: str = _env("LANGGRAPH_HAIKU_MODEL", "claude-haiku-5-5")
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
    # K_SERVICE / K_REVISION are set by Cloud Run, so each service's traces carry its own name.
    OTEL_SERVICE_NAME: str = _env("OTEL_SERVICE_NAME", os.environ.get("K_SERVICE", "six-sevens-backend"))
    SERVICE_REVISION: str = os.environ.get("K_REVISION", "local")
    # Set to send traces over OTLP instead (e.g. http://jaeger:4318 in the local observability profile).
    OTEL_EXPORTER_OTLP_ENDPOINT: str = _env("OTEL_EXPORTER_OTLP_ENDPOINT")
    OTEL_TRACES_SAMPLE_RATIO: float = float(os.environ.get("OTEL_TRACES_SAMPLE_RATIO", "1.0"))

    # --- Public registry lookups (fraud_detection/external_validation/registry_lookups) ---
    # Both optional: without them the anonymous limits apply, and a lookup
    # that hits its rate limit is reported as unable to assess.
    DATA_GOV_SG_API_KEY: str = _env("DATA_GOV_SG_API_KEY")
    ONEMAP_API_TOKEN: str = _env("ONEMAP_API_TOKEN")
    # URA Data Service access key (eservice.ura.gov.sg/maps/api): for "Approved Residential
    # Use". Optional: without it that lookup is skipped and zoning alone is used.
    URA_API_KEY: str = _env("URA_API_KEY")
    # URA zoning index (registry_lookups/land_use.py): whether a running server may download
    # and build it itself (~35 s, ~200 MB peak, measured) when it's missing or URA has updated the map.
    # The Docker image builds it in and sets this to false; local runs keep it on.
    LAND_USE_RUNTIME_REBUILD: bool = os.environ.get("LAND_USE_RUNTIME_REBUILD", "true").lower() == "true"

    # --- Per-case analysis (case_pipeline/) ---
    # Kill switch: off falls back to document-level detection and scoring only.
    CASE_ANALYSIS_ENABLED: bool = os.environ.get("CASE_ANALYSIS_ENABLED", "true").lower() == "true"
    # Whether cases created here need every required checklist document approved before they can be analysed.
    # Stored on each case when it's created (cases.checklist_enforced). On only in prod (Terraform).
    REQUIRE_COMPLETE_CHECKLIST: bool = os.environ.get("REQUIRE_COMPLETE_CHECKLIST", "false").lower() == "true"


settings = Settings()
