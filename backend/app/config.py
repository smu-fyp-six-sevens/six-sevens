import os

from dotenv import load_dotenv

from . import passwords


load_dotenv()


def _str(name: str, default: str = "") -> str:
    """
    os.environ.get, stripped — a copy-pasted secret easily picks up a
    trailing newline, which is silently harmless for most settings but
    fatal as an HTTP header value (e.g. SUPABASE_SECRET_KEY).
    """
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {raw!r}") from None


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None


def _bool(name: str, default: bool) -> bool:
    """On only when set to "true" (any case); any other value is off, as it always was."""
    raw = os.environ.get(name)
    return default if raw is None else raw.lower() == "true"


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
    """
    AUTH_USERS: "alice:<hash>;bob:<hash>", each hash made by `python -m scripts.hash_password` (passwords.py). A
    malformed entry, or a password stored in plain text, stops the app at start-up.
    """
    users: dict[str, str] = {}
    for entry in raw.split(";"):
        if not entry.strip():
            continue
        name, separator, stored = entry.partition(":")
        name, stored = name.strip(), stored.strip()
        if not separator or not name or not stored:
            raise ValueError('AUTH_USERS must look like "alice:<hash>;bob:<hash>"')
        if not passwords.is_hash(stored):
            raise ValueError(
                f"AUTH_USERS entry for {name!r} isn't a password hash. Make one with "
                "`python -m scripts.hash_password` in backend/ and paste the line it prints."
            )
        users[name] = stored
    return users


class Settings:
    """
    Centralised config. All values come from environment variables so
    nothing sensitive (API keys, service role key) is hardcoded.
    """
    # Every account that can log in, in one place (see app/auth.py).
    # AUTH_SECRET_KEY signs bearer tokens; outside local dev an empty one stops startup (main.check_signing_key).
    AUTH_USERS: dict[str, str] = _users(_str("AUTH_USERS"))
    AUTH_SECRET_KEY: str = _str("AUTH_SECRET_KEY")
    # The session cookie's Secure flag (auth.py). Browsers accept it on http://localhost too; false only for a client
    # that won't send a Secure cookie over plain HTTP.
    AUTH_COOKIE_SECURE: bool = _bool("AUTH_COOKIE_SECURE", True)
    # The commit an image was built from (backend/Dockerfile's GIT_COMMIT build argument), for agent_versions.py.
    GIT_COMMIT: str = _str("GIT_COMMIT", "local")

    # --- Services and events (HOW_IT_WORKS.md §9) ---
    # Which roles this process runs; the default runs all of them, as uvicorn always has.
    ROLES: frozenset[str] = _roles(_str("ROLE", "all"))
    # "inprocess" calls handlers in this process; "pubsub" publishes to Pub/Sub (or the emulator
    # when PUBSUB_EMULATOR_HOST is set) and workers receive events on POST /push.
    EVENT_TRANSPORT: str = _str("EVENT_TRANSPORT", "inprocess").lower()
    PUBSUB_TOPIC: str = _str("PUBSUB_TOPIC", "domain-events")
    PUBSUB_PUBLISH_TIMEOUT_SECONDS: float = _float("PUBSUB_PUBLISH_TIMEOUT_SECONDS", 30)
    # When set, POST /push requires a Google OIDC token for this audience from PUSH_SERVICE_ACCOUNT.
    # Needed on a public service (audit-metrics sits behind Kong); private workers are also guarded by Cloud Run IAM.
    PUSH_AUDIENCE: str = _str("PUSH_AUDIENCE")
    PUSH_SERVICE_ACCOUNT: str = _str("PUSH_SERVICE_ACCOUNT")
    # How workers receive events: "push" (Cloud Run: Pub/Sub posts to /push/<role>) or "pull" (GKE: events/pull.py
    # streams from the role's subscription). Same subscription either way, switched in place (HOW_IT_WORKS.md §9).
    # "none": no events at all, only HTTP (a role serving requests on GKE while Cloud Run still takes its events).
    EVENT_DELIVERY: str = _str("EVENT_DELIVERY", "push").lower()
    # Messages each role's subscription hands this process at once (the role's concurrency on Cloud Run).
    PULL_MAX_MESSAGES: int = _int("PULL_MAX_MESSAGES", 2)
    # The longest a run may hold its message; the client keeps extending the ack deadline until then.
    PULL_MAX_LEASE_SECONDS: int = _int("PULL_MAX_LEASE_SECONDS", 3600)
    # At shutdown, how long runs in flight get to finish before they're stopped and their messages redelivered.
    PULL_DRAIN_SECONDS: float = _float("PULL_DRAIN_SECONDS", 20)
    # When set (GKE), /metrics is also served on this port, apart from the app's, so the cluster's scraper reaches
    # it without the gateway key; the network policies let only the scraper in. 0: only the app's /metrics.
    METRICS_PORT: int = _int("METRICS_PORT", 0)
    # Kong sends this in X-Gateway-Key on every request it forwards; api and audit-metrics refuse a request without
    # it (gateway.py), so nobody can call them around Kong's rate limits. Empty in a plain uvicorn run.
    GATEWAY_SECRET: str = _str("GATEWAY_SECRET")
    # Off behind Kong, which answers CORS itself; two sets of CORS headers make browsers refuse the response.
    CORS_IN_APP: bool = _bool("CORS_IN_APP", True)
    # A case analysis still "running" after this long was interrupted; the sweeper then marks it failed.
    CASE_ANALYSIS_TIMEOUT_SECONDS: int = _int("CASE_ANALYSIS_TIMEOUT_SECONDS", 900)

    # --- Stuck work and recovery (sweeper.py, HOW_IT_WORKS.md §8, "Stuck work") ---
    # Off by default: the database is shared, so a local process must never sweep cloud work. Terraform turns it on.
    SWEEPER_ENABLED: bool = _bool("SWEEPER_ENABLED", False)
    # uploaded/queued past the first means its event was lost; processing/extracting/detecting past the second means
    # its run died. The case queue's Stuck badge and an officer's retry use the same two (monitoring.py).
    STUCK_WAITING_SECONDS: int = _int("STUCK_WAITING_SECONDS", 300)
    STUCK_RUNNING_SECONDS: int = _int("STUCK_RUNNING_SECONDS", 600)
    RECOVERY_MAX_ATTEMPTS: int = _int("RECOVERY_MAX_ATTEMPTS", 3)
    # How often a running pipeline or detection checks for a cancel made through another instance (pubsub only).
    CANCEL_POLL_SECONDS: float = _float("CANCEL_POLL_SECONDS", 5)

    # Comma-separated list of exact origins the frontend is served from, in
    # addition to local dev.
    ALLOWED_ORIGINS: list[str] = [
        origin.strip()
        for origin in _str("ALLOWED_ORIGINS").split(",")
        if origin.strip()
    ] + ["http://localhost:5173"]

    # Regex alternative to ALLOWED_ORIGINS: Cloud Run hands one service
    # multiple valid hostnames, so pinning to one exact origin is fragile.
    # Set by the deploy workflow.
    ALLOWED_ORIGIN_REGEX: str | None = _str("ALLOWED_ORIGIN_REGEX") or None

    SUPABASE_URL: str = _str("SUPABASE_URL")
    # Bypasses RLS: every backend read, write and Storage call uses it. Never sent to the frontend.
    SUPABASE_SECRET_KEY: str = _str("SUPABASE_SECRET_KEY")
    SUPABASE_STORAGE_BUCKET: str = _str("SUPABASE_STORAGE_BUCKET", "documents")

    # AC2: invocation should be attempted within this window of being dequeued
    DEQUEUE_TO_INVOKE_TARGET_SECONDS: float = 1.0

    # Bounded classify<->extract escalation loop (langgraph/graph.py) — one
    # re-classification attempt before routing to manual review.
    MAX_RECLASSIFICATION_ATTEMPTS: int = 1

    # Documents one doc-worker process runs at once (workers/documents.py) — bounded by Anthropic rate limits.
    QUEUE_WORKER_CONCURRENCY: int = _int("QUEUE_WORKER_CONCURRENCY", 4)

    # Threads for PDF/OCR/forensic work (cpu_pool.py). 1 = PyMuPDF only ever runs on one thread.
    CPU_WORKER_CONCURRENCY: int = _int("CPU_WORKER_CONCURRENCY", 1)
    # Processes for that work instead (each with its own PyMuPDF); 0 keeps the thread. Set on the deployed workers.
    CPU_WORKER_PROCESSES: int = _int("CPU_WORKER_PROCESSES", 0)
    # Detection runs (lead + 3 specialists each) allowed at once; the rest wait their turn.
    DETECTION_CONCURRENCY: int = _int("DETECTION_CONCURRENCY", 2)
    # Most model calls one detection run may make (lead, specialists, reviews, binders). Past it the run
    # stops asking, reports what it has, and the verdict cannot be "clean".
    DETECTION_MAX_MODEL_CALLS: int = _int("DETECTION_MAX_MODEL_CALLS", 30)
    # Most model calls one document's classify and extract run may make (agent turns, the two classifier
    # tools, a repair, extraction and its one retry, and one reclassification).
    DOCUMENT_MAX_MODEL_CALLS: int = _int("DOCUMENT_MAX_MODEL_CALLS", 24)
    # A document the content guard finds worded as an instruction to the AI fails at ingest, before classification
    # or extraction (guardrails/content_guard.blocking_reason). "false" lets it through, flagged instead.
    GUARD_BLOCK_INJECTION: bool = _bool("GUARD_BLOCK_INJECTION", True)
    # Retries the Anthropic client makes by itself on a rate limit or 5xx; each one is billed again.
    LLM_MAX_RETRIES: int = _int("LLM_MAX_RETRIES", 1)
    # One model request. Extraction can write 16k tokens, so this is generous.
    LLM_TIMEOUT_SECONDS: float = _float("LLM_TIMEOUT_SECONDS", 300)
    # One detection tool. This includes waiting for the single CPU worker, so it is not the check's own
    # run time. The slow ones call a model or an outside registry.
    TOOL_TIMEOUT_SECONDS: float = _float("TOOL_TIMEOUT_SECONDS", 60)
    SLOW_TOOL_TIMEOUT_SECONDS: float = _float("SLOW_TOOL_TIMEOUT_SECONDS", 120)
    # The most a tool waits for the CPU worker before it starts; its own limit only runs once it has started.
    CPU_QUEUE_TIMEOUT_SECONDS: float = _float("CPU_QUEUE_TIMEOUT_SECONDS", 300)

    # Below this, classification is routed to manual review regardless of
    # the guessed document_type (see langgraph/nodes._is_below_threshold).
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.85

    # Below this, extracted fields render as editable so a loan officer can
    # correct them. Set above CLASSIFICATION_CONFIDENCE_THRESHOLD since a
    # wrong-looking field is costlier than a wrongly-routed document.
    EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.9
    # The whole extraction second look. Past it the text pass stands and second_look_error is set.
    EXTRACTION_SECOND_LOOK_TIMEOUT_SECONDS: float = 90

    ALLOWED_CONTENT_TYPES: set[str] = {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB
    MAX_BULK_FILES: int = 20
    # How long the classifier's and extractor's correction hints are reused before audit_events is read again.
    CORRECTION_HINTS_CACHE_SECONDS: int = 300
    # A scanned PDF is rendered at 2x and OCR'd page by page: bound the work one upload can cause.
    OCR_MAX_PAGES: int = 50
    OCR_MAX_PAGE_PIXELS: int = 25_000_000

    # Signed URLs need to outlive a slow run, not just one request.
    SIGNED_URL_EXPIRY_SECONDS: int = 1800

    # --- LLM pipeline (backend/app/langgraph) ---
    ANTHROPIC_API_KEY: str = _str("ANTHROPIC_API_KEY")
    # Two-tier models: Sonnet for
    # classifier/vision/extraction/detection-lead, Haiku for
    # llm_classify/repair/specialists. Overridable per-tier.
    LANGGRAPH_SONNET_MODEL: str = _str("LANGGRAPH_SONNET_MODEL", "claude-sonnet-5-5")
    LANGGRAPH_HAIKU_MODEL: str = _str("LANGGRAPH_HAIKU_MODEL", "claude-haiku-5-5")
    # Ceiling on the classify agent's tool-call loop and the full
    # record_escalation -> reclassify -> validate -> extract pass —
    # comfortably covers MAX_RECLASSIFICATION_ATTEMPTS with room to spare.
    LANGGRAPH_RECURSION_LIMIT: int = 60

    # --- Observability (see telemetry.py) ---
    # Off by default: importing the SDK is harmless with no cost/behaviour
    # change until this is set.
    OTEL_ENABLED: bool = _bool("OTEL_ENABLED", False)
    # httpx's own INFO line per Supabase call. Off by default: query_stats.py logs one summary per unit of work.
    LOG_HTTP_REQUESTS: bool = _bool("LOG_HTTP_REQUESTS", False)
    # Required for the Cloud Trace exporter; left unset -> traces print to
    # the console instead.
    GOOGLE_CLOUD_PROJECT: str = _str("GOOGLE_CLOUD_PROJECT", "")
    # K_SERVICE / K_REVISION are set by Cloud Run, so each service's traces carry its own name.
    OTEL_SERVICE_NAME: str = _str("OTEL_SERVICE_NAME", _str("K_SERVICE", "six-sevens-backend"))
    SERVICE_REVISION: str = _str("K_REVISION", "local")
    # Set to send traces over OTLP instead (e.g. http://jaeger:4318 in the local observability profile).
    OTEL_EXPORTER_OTLP_ENDPOINT: str = _str("OTEL_EXPORTER_OTLP_ENDPOINT")
    OTEL_TRACES_SAMPLE_RATIO: float = _float("OTEL_TRACES_SAMPLE_RATIO", 1.0)

    # --- Public registry lookups (fraud_detection/external_validation/registry_lookups) ---
    # Both optional: without them the anonymous limits apply, and a lookup
    # that hits its rate limit is reported as unable to assess.
    DATA_GOV_SG_API_KEY: str = _str("DATA_GOV_SG_API_KEY")
    # Testing only: answer ACRA lookups for the synthetic test companies from registry_lookups/
    # synthetic_acra_registry.json instead of data.gov.sg, whose register doesn't hold their made-up UENs. Any
    # other UEN is still looked up for real. Never set it for real applications.
    SYNTHETIC_ACRA_REGISTRY: bool = _bool("SYNTHETIC_ACRA_REGISTRY", False)
    ONEMAP_API_TOKEN: str = _str("ONEMAP_API_TOKEN")
    # URA Data Service access key (eservice.ura.gov.sg/maps/api): for "Approved Residential
    # Use". Optional: without it that lookup is skipped and zoning alone is used.
    URA_API_KEY: str = _str("URA_API_KEY")
    # URA zoning index (registry_lookups/land_use.py): whether a running server may download
    # and build it itself (~35 s, ~200 MB peak, measured) when it's missing or URA has updated the map.
    # The Docker image builds it in and sets this to false; local runs keep it on.
    LAND_USE_RUNTIME_REBUILD: bool = _bool("LAND_USE_RUNTIME_REBUILD", True)

    # --- Per-case analysis (case_pipeline/) ---
    # Kill switch: off falls back to document-level detection and scoring only.
    CASE_ANALYSIS_ENABLED: bool = _bool("CASE_ANALYSIS_ENABLED", True)
    # Check payslips against CPF rate tables entered on an assumption (sg_reference/cpf.PROVISIONAL_RATE_TABLES,
    # today 2027's). Off: such a payslip's CPF checks are skipped until CPF publishes the real table.
    CPF_USE_PROVISIONAL_RATES: bool = _bool("CPF_USE_PROVISIONAL_RATES", False)
    # Runs started automatically per case per hour (each spends about 12k tokens); past it, the officer starts one.
    CASE_ANALYSIS_AUTO_RUNS_PER_HOUR: int = _int("CASE_ANALYSIS_AUTO_RUNS_PER_HOUR", 4)
    # Runs of any kind per case per hour past which an officer's "Run again" is refused (a stolen token can't loop it).
    CASE_ANALYSIS_RUNS_PER_HOUR: int = _int("CASE_ANALYSIS_RUNS_PER_HOUR", 10)
    # Claude spend (list price, every run on the case) past which no detection run or case analysis starts on it
    # (spend_cap.py). 0 turns the cap off.
    CASE_SPEND_CAP_USD: float = _float("CASE_SPEND_CAP_USD", 5.0)
    # Whether cases created here need every required checklist document approved before they can be analysed.
    # Stored on each case when it's created (cases.checklist_enforced). On only in prod (Terraform).
    REQUIRE_COMPLETE_CHECKLIST: bool = _bool("REQUIRE_COMPLETE_CHECKLIST", False)


settings = Settings()
