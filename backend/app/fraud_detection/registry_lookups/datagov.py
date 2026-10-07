"""data.gov.sg datastore search, used for the ACRA and HDB datasets."""

from __future__ import annotations

import json

from ...config import settings
from .http import LookupUnavailable, get

_DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
_KEY_HINT = "Set DATA_GOV_SG_API_KEY for a higher rate limit."


async def datastore_search(resource_id: str, filters: dict[str, str], limit: int = 10) -> list[dict]:
    headers = {"x-api-key": settings.DATA_GOV_SG_API_KEY} if settings.DATA_GOV_SG_API_KEY else None
    body = await get(
        _DATASTORE_URL,
        params={"resource_id": resource_id, "filters": json.dumps(filters), "limit": limit},
        headers=headers, rate_limit_hint=_KEY_HINT,
    )
    if not isinstance(body, dict) or not body.get("success"):
        raise LookupUnavailable(f"data.gov.sg returned an error for dataset {resource_id}")
    return list(body.get("result", {}).get("records") or [])
