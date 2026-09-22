import os

from dotenv import load_dotenv


load_dotenv()


def _env(name: str, default: str = "") -> str:
    """
    os.environ.get, stripped of surrounding whitespace. A value copied out
    of a .env file or a GitHub Actions secret very easily picks up a
    trailing newline (the file/textbox ends with one, the copy includes it)
    — harmless for most settings, but fatal for anything sent as an HTTP
    header value (e.g. SUPABASE_PUBLISHABLE_KEY): httpx raises "Illegal
    header value" since a literal \\n in a header is invalid. Stripping
    every env-sourced string here means that mistake fails loudly and
    obviously at the copy-paste step instead of silently as a runtime error.
    """
    return os.environ.get(name, default).strip()


class Settings:
    """
    Centralised config. All values come from environment variables so
    nothing sensitive (API keys, service role key) is hardcoded.
    """
    # Shared-credential auth for the public demo deployment (see app/auth.py).
    # AUTH_SECRET_KEY signs bearer tokens — must be set to a real random value
    # in any deployed environment (an empty/default secret makes tokens
    # trivially forgeable), local dev is the only place the "" default is fine.
    AUTH_USERNAME: str = _env("AUTH_USERNAME")
    AUTH_PASSWORD: str = _env("AUTH_PASSWORD")
    AUTH_SECRET_KEY: str = _env("AUTH_SECRET_KEY")

    # Comma-separated list of exact origins the frontend is served from, in
    # addition to local dev.
    ALLOWED_ORIGINS: list[str] = [
        origin.strip()
        for origin in _env("ALLOWED_ORIGINS").split(",")
        if origin.strip()
    ] + ["http://localhost:5173"]

    # Regex alternative to ALLOWED_ORIGINS, for Cloud Run specifically: a
    # single service gets handed out more than one valid hostname (e.g. both
    # "fyp-frontend-xxxxx-as.a.run.app" and
    # "fyp-frontend-xxxxx.asia-southeast1.run.app" resolve to the same
    # service), so pinning ALLOWED_ORIGINS to one exact resolved URL is
    # fragile — whichever format the browser happens to be on can fail CORS
    # even though it's the right service. The deploy workflow sets this to
    # match any hostname Cloud Run might hand the frontend service, keyed
    # off the stable service name rather than the unpredictable per-deploy
    # hash/region suffix.
    ALLOWED_ORIGIN_REGEX: str | None = _env("ALLOWED_ORIGIN_REGEX") or None

    SUPABASE_URL: str = _env("SUPABASE_URL")
    SUPABASE_PUBLISHABLE_KEY: str = _env("SUPABASE_PUBLISHABLE_KEY")
    # Bypasses RLS — used only for the backend's own Realtime subscription
    # (see realtime_listener.py), never sent to the frontend. Supabase's
    # current replacement for the legacy "service_role" key.
    SUPABASE_SECRET_KEY: str = _env("SUPABASE_SECRET_KEY")
    SUPABASE_STORAGE_BUCKET: str = _env("SUPABASE_STORAGE_BUCKET", "documents")

    # Dify workflow/chatflow calls have been observed taking ~20-90s under
    # concurrent load (see SIGNED_URL_EXPIRY_SECONDS below) — httpx's default
    # request timeout is far shorter than that, so without an explicit
    # override every slow-but-otherwise-fine call gets killed by our own
    # client and reported as a failure (with an unhelpful, often-blank
    # error message, since a client-side timeout has no HTTP response body).
    # Extraction in particular got slower once bank_statement started asking
    # for a full transaction-by-transaction breakdown (see
    # extraction_prompts.EXTRA_FIELD_GUIDANCE) — 120s wasn't enough for that
    # on a vision-based (image) statement in testing, all 3 retries timed out.
    DIFY_REQUEST_TIMEOUT_SECONDS: float = 240.0

    DIFY_BASE_URL: str = _env("DIFY_BASE_URL", "https://api.dify.ai/v1")
    # Classifier (Intake Coordinator) is a Dify Chatflow app, not a Workflow
    # app — Dify identifies a chat app purely by its own API key (sent as
    # the Authorization header on /chat-messages), so there's no workflow_id
    # to pass in the request body at all.
    DIFY_CLASSIFIER_API_KEY: str = _env("DIFY_CLASSIFIER_API_KEY")
    DIFY_EXTRACTION_WORKFLOW_ID: str = _env("DIFY_EXTRACTION_WORKFLOW_ID")
    DIFY_EXTRACTION_API_KEY: str = _env("DIFY_EXTRACTION_API_KEY")

    # AC2: invocation should be attempted within this window of being dequeued
    DEQUEUE_TO_INVOKE_TARGET_SECONDS: float = 1.0
    # AC3: retry policy for a failed Dify workflow invocation (any workflow —
    # applies uniformly via DifyClient.run_workflow_with_retry)
    MAX_INVOCATION_RETRIES: int = 3
    RETRY_BACKOFF_SECONDS: float = 1.5
    # AC4: a document idle in queued/processing longer than this is "stuck"
    STUCK_THRESHOLD_SECONDS: int = 10

    # Bounded classification<->extraction escalation loop (see orchestrator.py).
    # Capped at one re-classification attempt: if Extraction still doesn't
    # recognise the document after the classifier has had one look at the
    # escalation reason, it's routed to manual review rather than looping again.
    MAX_RECLASSIFICATION_ATTEMPTS: int = 1

    # Number of documents processed concurrently by the in-memory queue
    # worker pool (see queue_service.py). Each worker independently calls
    # out to Dify per document, so this is mostly bounded by Dify's own
    # rate limits rather than local resources.
    QUEUE_WORKER_CONCURRENCY: int = int(os.environ.get("QUEUE_WORKER_CONCURRENCY", "4"))

    # Separate, usually-tighter cap on simultaneous Classifier chatflow
    # calls specifically (see DifyClient.run_chatflow) — the Classifier's
    # Agent node has shown crashes ('str' object has no attribute 'get')
    # under concurrent load that plain Workflow calls (Extraction) don't,
    # so this is deliberately independent of QUEUE_WORKER_CONCURRENCY
    # rather than reusing it. Start conservative (1 = fully serialized)
    # and raise it only after confirming Dify handles more at once.
    DIFY_CLASSIFIER_MAX_CONCURRENCY: int = int(
        os.environ.get("DIFY_CLASSIFIER_MAX_CONCURRENCY", "1")
    )

    # Classifier confidence below this is treated as low-confidence and
    # routed to manual review, regardless of what document_type was guessed
    # (see orchestrator._is_below_threshold).
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.8

    # Extraction: below this, the frontend renders extracted fields as
    # editable inputs (with a save button) instead of read-only text, so a
    # loan officer can correct values the LLM wasn't confident about. Set
    # a bit above the classification threshold since a human is meant to
    # actually act on these values (loan amounts, IDs), not just route the
    # document — a wrong-but-plausible-looking field is costlier than a
    # wrongly-routed document.
    EXTRACTION_CONFIDENCE_THRESHOLD: float = 0.95

    ALLOWED_CONTENT_TYPES: set[str] = {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB
    MAX_BULK_FILES: int = 20

    # A signed URL is generated once per Classifier invocation and reused
    # across all MAX_INVOCATION_RETRIES retry attempts (see dify_client.py),
    # so it needs to outlive a slow run + backoff, not just one request —
    # under concurrent load a single attempt alone has taken ~20-90s.
    SIGNED_URL_EXPIRY_SECONDS: int = 1800

    # --- LangGraph experiment (langgraph-test branch, backend/app/langgraph) ---
    # New to this backend: today Anthropic keys live only in Dify. Required
    # once the langgraph pipeline is actually invoked; not read by anything
    # on the Dify code path.
    ANTHROPIC_API_KEY: str = _env("ANTHROPIC_API_KEY")
    # Two-tier models (see LANGGRAPH_MIGRATION_PLAN.md section 5, "Models"):
    # Sonnet for the classifier agent/vision/extraction/detection lead,
    # Haiku for llm_classify/repair/specialists. Overridable per-tier so a
    # model swap doesn't need a code change.
    LANGGRAPH_SONNET_MODEL: str = _env("LANGGRAPH_SONNET_MODEL", "claude-sonnet-4-6")
    LANGGRAPH_HAIKU_MODEL: str = _env("LANGGRAPH_HAIKU_MODEL", "claude-haiku-4-5-20251001")
    # Router fast-path (see langgraph/router.py): skip the Sonnet agent
    # entirely when rule_classify and llm_classify already agree this
    # strongly — this is the "biggest saving" the migration plan calls out.
    LANGGRAPH_ROUTER_AGREEMENT_THRESHOLD: float = 0.8
    LANGGRAPH_RECURSION_LIMIT: int = 15


settings = Settings()