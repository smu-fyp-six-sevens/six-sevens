"""data.gov.sg: datastore search, and the catalogue that says which datasets make up a collection.

Datasets are looked up through their collection (a stable, published number) every time
the cache expires, never hardcoded: if an agency republishes under new dataset IDs, the
next refresh picks them up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ....config import settings
from .http import LookupUnavailable, get

_DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
_CATALOGUE_URL = "https://api-production.data.gov.sg/v2/public/api"
_KEY_HINT = "Set DATA_GOV_SG_API_KEY for a higher rate limit."
# Which datasets make up a collection: rechecked hourly.
_COLLECTION_TTL_SECONDS = 3600
# A dataset ID's name never changes, so its name is kept for a day.
_DATASET_NAME_TTL_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Collection:
    dataset_ids: tuple[str, ...]
    last_updated: datetime | None


def _headers() -> dict | None:
    return {"x-api-key": settings.DATA_GOV_SG_API_KEY} if settings.DATA_GOV_SG_API_KEY else None


async def collection(collection_id: int) -> Collection:
    body = await get(f"{_CATALOGUE_URL}/collections/{collection_id}/metadata", headers=_headers(),
                     rate_limit_hint=_KEY_HINT, cache_ttl=_COLLECTION_TTL_SECONDS)
    metadata = (body.get("data") or {}).get("collectionMetadata") if isinstance(body, dict) else None
    if not metadata or not metadata.get("childDatasets"):
        raise LookupUnavailable(f"data.gov.sg returned no datasets for collection {collection_id}")
    updated = metadata.get("lastUpdatedAt")
    return Collection(
        dataset_ids=tuple(metadata["childDatasets"]),
        last_updated=datetime.fromisoformat(updated) if updated else None,
    )


async def dataset_name(dataset_id: str) -> str:
    body = await get(f"{_CATALOGUE_URL}/datasets/{dataset_id}/metadata", headers=_headers(),
                     rate_limit_hint=_KEY_HINT, cache_ttl=_DATASET_NAME_TTL_SECONDS)
    name = (body.get("data") or {}).get("name") if isinstance(body, dict) else None
    if not name:
        raise LookupUnavailable(f"data.gov.sg returned no name for dataset {dataset_id}")
    return str(name)


async def dataset_last_updated(dataset_id: str) -> str | None:
    """When the publisher last updated a dataset (ISO 8601), from its catalogue entry; rechecked hourly."""
    body = await get(f"{_CATALOGUE_URL}/datasets/{dataset_id}/metadata", headers=_headers(),
                     rate_limit_hint=_KEY_HINT, cache_ttl=_COLLECTION_TTL_SECONDS)
    updated = (body.get("data") or {}).get("lastUpdatedAt") if isinstance(body, dict) else None
    return str(updated) if updated else None


async def datastore_search(resource_id: str, filters: dict[str, str], limit: int = 10) -> list[dict]:
    body = await get(
        _DATASTORE_URL,
        params={"resource_id": resource_id, "filters": json.dumps(filters), "limit": limit},
        headers=_headers(), rate_limit_hint=_KEY_HINT, cache_ttl=_COLLECTION_TTL_SECONDS,
    )
    if not isinstance(body, dict) or not body.get("success"):
        raise LookupUnavailable(f"data.gov.sg returned an error for dataset {resource_id}")
    return list(body.get("result", {}).get("records") or [])
