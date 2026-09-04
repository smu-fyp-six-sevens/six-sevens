import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    """
    Centralised config. All values come from environment variables so
    nothing sensitive (API keys, service role key) is hardcoded.
    """

    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

    SUPABASE_URL: str = os.environ.get("SUPABASE_URL", "")
    SUPABASE_PUBLISHABLE_KEY: str = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
    SUPABASE_STORAGE_BUCKET: str = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")

    DIFY_BASE_URL: str = os.environ.get("DIFY_BASE_URL", "https://api.dify.ai/v1")
    DIFY_API_KEY: str = os.environ.get("DIFY_API_KEY", "")
    # Classifier (Intake Coordinator) is a Dify Chatflow app, not a Workflow
    # app — Dify identifies a chat app purely by its own API key (sent as
    # the Authorization header on /chat-messages), so there's no workflow_id
    # to pass in the request body. DIFY_CLASSIFIER_WORKFLOW_ID is kept only
    # for reference/documentation of which Dify app this is.
    DIFY_CLASSIFIER_API_KEY: str = os.environ.get("DIFY_CLASSIFIER_API_KEY", "")
    DIFY_CLASSIFIER_WORKFLOW_ID: str = os.environ.get("DIFY_CLASSIFIER_WORKFLOW_ID", "")
    DIFY_EXTRACTION_WORKFLOW_ID: str = os.environ.get("DIFY_EXTRACTION_WORKFLOW_ID", "")

    DEEPSEEK_BASE_URL: str = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_API_KEY: str = os.environ.get("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL: str = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    # AC2: invocation should be attempted within this window of being dequeued
    DEQUEUE_TO_INVOKE_TARGET_SECONDS: float = 1.0
    # AC3: retry policy for a failed Dify workflow invocation (any workflow —
    # applies uniformly via DifyClient.run_workflow_with_retry)
    MAX_INVOCATION_RETRIES: int = 3
    RETRY_BACKOFF_SECONDS: float = 1.5
    # AC4: a document idle in queued/processing longer than this is "stuck"
    STUCK_THRESHOLD_SECONDS: int = 10

    # Bounded classification<->extraction escalation loop (see orchestrator.py)
    MAX_RECLASSIFICATION_ATTEMPTS: int = 2

    # Classification: filename-rule matches scoring below this are treated
    # as unrecognised/low-confidence and flagged for manual review.
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.6

    ALLOWED_CONTENT_TYPES: set[str] = {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB
    MAX_BULK_FILES: int = 20

    SIGNED_URL_EXPIRY_SECONDS: int = 300


settings = Settings()