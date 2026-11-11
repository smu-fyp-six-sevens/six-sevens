"""
What officers' corrections teach the classifier and the extractor, read from `audit_events` through two functions in
backend/sql/schema.sql: type_corrections (the types officers changed a document from and to) and field_edit_counts
(the fields they added and deleted, per document type). Labels and field names only, never values, and no new table.

A lesson counts once MIN_CASES cases agree, so one officer's mistake teaches nothing. Only corrections after
LEARNING_SINCE count: audit_events is never emptied, so a test that needs a clean start moves this date.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel

from .case_pipeline.cross_document.bias import has_protected_text, is_protected_field
from .supabase_client import service_supabase_client

# The database was wiped on 2026-10-31: corrections from before it describe documents that no longer exist.
LEARNING_SINCE = datetime(2026, 10, 31, tzinfo=timezone.utc)
MIN_CASES = 2
# An officer can type any field name: only plain snake_case names are taught.
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


class TypeCorrection(BaseModel):
    from_type: str
    to_type: str
    cases: int


class FieldEdit(BaseModel):
    document_type: str
    field_name: str
    edit: Literal["added", "deleted"]
    cases: int


def _rows(function: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], service_supabase_client().rpc(function, params).execute().data or [])


def type_corrections() -> list[TypeCorrection]:
    """Each recurring correction (from_type to to_type, in MIN_CASES cases or more), most cases first."""
    pairs = [TypeCorrection(**row) for row in _rows("type_corrections", {"p_since": LEARNING_SINCE.isoformat()})]
    return sorted((p for p in pairs if p.cases >= MIN_CASES), key=lambda p: (-p.cases, p.from_type, p.to_type))


def _teachable(name: str) -> bool:
    return bool(_FIELD_NAME.match(name)) and not is_protected_field(name) and not has_protected_text(name.replace("_", " "))


def field_edits(document_type: str) -> list[FieldEdit]:
    """The fields officers recurringly add to and delete from this document type, most cases first."""
    edits = [FieldEdit(**row) for row in _rows("field_edit_counts", {
        "p_since": LEARNING_SINCE.isoformat(), "p_document_type": document_type,
    })]
    return sorted(
        (e for e in edits if e.cases >= MIN_CASES and _teachable(e.field_name)),
        key=lambda e: (-e.cases, e.field_name),
    )
