from __future__ import annotations

"""
In-memory holding spot for the Classifier's raw evidence output.

Evidence isn't persisted to Supabase (no schema change for it) — it only
needs to survive long enough for the frontend to poll for it once, so a
process-local dict is enough. Entries are lost on backend restart, and
there's no eviction since a single dev/test process never holds enough
documents for this to matter. Confidence used to live here too, but now
has a real column on DocumentRecord — see document_repository.record_classification.
"""

from typing import Optional


_cache: dict[str, list[str]] = {}


def set_evidence(document_id: str, evidence: list[str]) -> None:
    _cache[document_id] = evidence


def get_evidence(document_id: str) -> Optional[list[str]]:
    return _cache.get(document_id)
