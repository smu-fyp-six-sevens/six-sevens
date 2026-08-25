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
    DIFY_CLASSIFIER_WORKFLOW_ID: str = os.environ.get("DIFY_CLASSIFIER_WORKFLOW_ID", "")

    # AC2: invocation should be attempted within this window of being dequeued
    DEQUEUE_TO_INVOKE_TARGET_SECONDS: float = 1.0
    # AC3: retry policy for a failed workflow invocation
    MAX_INVOCATION_RETRIES: int = 3
    RETRY_BACKOFF_SECONDS: float = 1.5
    # AC4: a document idle in queued/processing longer than this is "stuck"
    STUCK_THRESHOLD_SECONDS: int = 10

    # Classification: filename-rule matches scoring below this are treated
    # as unrecognised/low-confidence and flagged for manual review.
    CLASSIFICATION_CONFIDENCE_THRESHOLD: float = 0.6

    ALLOWED_CONTENT_TYPES: set[str] = {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    MAX_FILE_SIZE_BYTES: int = 25 * 1024 * 1024  # 25 MB


settings = Settings()