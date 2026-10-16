"""OneMap (Singapore Land Authority) postal code search.

https://www.onemap.gov.sg/apidocs/ — the search API returns every address at a postal
code (block, road, building). It works without a token; ONEMAP_API_TOKEN is sent
when set.
"""

from __future__ import annotations

import re

from ....config import settings
from .http import get

# Six digits not part of a longer number, so "S049514" and "Singapore 049514" both count.
POSTAL_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
SOURCE = "OneMap (SLA)"
_SEARCH_URL = "https://www.onemap.gov.sg/api/common/elastic/search"


async def addresses_at_postal_code(postal_code: str) -> list[dict]:
    headers = {"Authorization": settings.ONEMAP_API_TOKEN} if settings.ONEMAP_API_TOKEN else None
    body = await get(
        _SEARCH_URL,
        params={"searchVal": postal_code, "returnGeom": "Y", "getAddrDetails": "Y", "pageNum": 1},
        headers=headers, rate_limit_hint="Set ONEMAP_API_TOKEN.",
    )
    results = body.get("results") if isinstance(body, dict) else None
    return [r for r in results or [] if str(r.get("POSTAL")) == postal_code]
