"""
Run-scoped cache for a document's ingested raw text.

Graph state never carries raw_text (PII, and too big for a free-tier
checkpoint row — see LANGGRAPH_MIGRATION_PLAN.md section 5 and 7). Instead
`ingest` fills this in-memory cache, later nodes in the same process read
from it, and `text_hash` in state lets a node detect a cache miss (e.g. after
a process restart) and re-ingest from the stored file — RapidOCR is
deterministic and free, so this is a cache, not a source of truth.

A plain dict is enough while everything runs in one FastAPI process; nothing
here needs to survive a restart.
"""

from __future__ import annotations

import hashlib

_raw_text_by_document_id: dict[str, str] = {}


def text_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16]


def put(document_id: str, raw_text: str) -> str:
    """Stores raw_text for this run and returns its hash for state.text_hash."""
    _raw_text_by_document_id[document_id] = raw_text
    return text_hash(raw_text)


def get(document_id: str) -> str | None:
    return _raw_text_by_document_id.get(document_id)


def discard(document_id: str) -> None:
    _raw_text_by_document_id.pop(document_id, None)
