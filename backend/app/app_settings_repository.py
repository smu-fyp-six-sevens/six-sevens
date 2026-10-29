"""
The `app_settings` table (backend/sql/app_settings.sql): app-wide switches an admin sets on the Settings page,
one row per key with a JSON value.

finding_reviews_enabled: whether loan officers review detection findings. Off, nobody sees the review controls
or the precision and recall they give, and the review endpoints refuse (routers/finding_reviews.py). On by
default, and while the table hasn't been created yet, so a deployment without it behaves as it always has.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, cast

from .supabase_client import service_supabase_client

TABLE = "app_settings"
FINDING_REVIEWS_ENABLED = "finding_reviews_enabled"

logger = logging.getLogger(__name__)


def get_finding_reviews_enabled() -> bool:
    try:
        rows = cast(list[dict[str, Any]], (
            service_supabase_client().table(TABLE).select("value")
            .eq("key", FINDING_REVIEWS_ENABLED).limit(1).execute().data or []
        ))
    except Exception:
        logger.warning("Couldn't read app_settings; finding reviews stay on", exc_info=True)
        return True
    return rows[0]["value"] is not False if rows else True


def set_finding_reviews_enabled(enabled: bool, updated_by: str) -> None:
    service_supabase_client().table(TABLE).upsert({
        "key": FINDING_REVIEWS_ENABLED,
        "value": enabled,
        "updated_by": updated_by,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
