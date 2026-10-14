"""Cached GETs to the public registries, with rate-limit backoff.

Registry data changes slowly (ACRA's datasets monthly, OneMap's addresses rarely), so
answers are cached in-process for a day. A 429 is retried after the server's
Retry-After, up to a total wait; past that the lookup is reported as unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("fraud_detection.registry_lookups")

_CACHE_TTL_SECONDS = 24 * 3600
_MAX_TOTAL_WAIT_SECONDS = 20.0
_TIMEOUT_SECONDS = 15.0
_cache: dict[str, tuple[float, Any]] = {}


class LookupUnavailable(Exception):
    """The registry couldn't answer: unreachable, an error status, or rate limited."""


def clear_cache() -> None:
    _cache.clear()


async def get(url: str, *, params: dict | None = None, headers: dict | None = None, as_json: bool = True,
              rate_limit_hint: str = "", cache_ttl: float = _CACHE_TTL_SECONDS) -> Any:
    key = f"{url}?{json.dumps(params or {}, sort_keys=True)}"
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < cache_ttl:
        return cached[1]

    waited = 0.0
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "six-sevens-kyc"}) as client:
        for attempt in range(5):
            try:
                response = await client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise LookupUnavailable(f"{url} could not be reached ({type(exc).__name__})") from exc
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After") or 2 * (attempt + 1))
                if waited + wait > _MAX_TOTAL_WAIT_SECONDS:
                    break
                logger.info("Rate limited by %s, retrying in %.0fs", url, wait)
                await asyncio.sleep(wait)
                waited += wait
                continue
            if response.status_code >= 400:
                raise LookupUnavailable(f"{url} returned HTTP {response.status_code}")
            body = response.json() if as_json else response.text
            _cache[key] = (time.monotonic(), body)
            return body
    raise LookupUnavailable(f"{url} is rate limiting these requests.{(' ' + rate_limit_hint) if rate_limit_hint else ''}")
