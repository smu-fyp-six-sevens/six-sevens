from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import cast

from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import row as _row
from .fraud_detection.font_consistency import ExtractedField


_NUMBER_TEXT_RE = re.compile(r"^-?\(?\d[\d,]*(\.\d+)?\)?$")
_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")


def normalise_field_name(name: str) -> str:
    """'Total Equity (SGD)' -> 'total_equity_sgd'. Non-alphanumerics become single underscores."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def _infer_field_type(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        return _infer_text_type(value.strip())
    return "string"


def _infer_text_type(text: str) -> str:
    """Values typed in the UI arrive as strings, so guess from their text."""
    if text.lower() in ("true", "false"):
        return "boolean"
    if _NUMBER_TEXT_RE.match(text):
        return "number"
    for fmt in _DATE_FORMATS:
        try:
            datetime.strptime(text, fmt)
            return "date"
        except ValueError:
            continue
    return "string"


def replace_extracted_fields(
    document_id: str,
    fields: dict,
    *,
    field_confidences: dict | None = None,
    default_confidence: float | None,
    required_field_names: set[str],
) -> None:
    """
    Persists a document's extracted field values as one row per field —
    the sole place field values live (documents.extracted_fields is not
    written; document_repository.get_document/list_documents read it back
    from here) — so individual fields can be queried, joined, or reported
    on directly.

    Replaces (delete-then-insert) rather than upserts: re-processing a
    document (a reclassification escalation, or a loan officer's manual
    correction — see routers/documents.py's PATCH endpoint) should leave
    exactly the current field set, not accumulate stale rows from a
    previous attempt under different field names.

    field_category records whether a field was one of the document type's
    required schema fields (see extraction_prompts.EXTRACTION_SCHEMAS) or
    one the LLM added on its own initiative. confidence is per-field where
    the LLM actually returned one (field_confidences), falling back to
    default_confidence — the document-level confidence, or 1.0 for a
    human-reviewed manual save — for any field it didn't.
    """
    supabase_client.table("extracted_fields").delete().eq("document_id", document_id).execute()

    if not fields:
        return

    field_confidences = field_confidences or {}
    rows = []
    for name, value in fields.items():
        per_field_confidence = field_confidences.get(name)
        rows.append({
            "document_id": document_id,
            "field_name": name,
            "field_value": value,
            "confidence": per_field_confidence if per_field_confidence is not None else default_confidence,
            "field_category": "required" if name in required_field_names else "additional",
            "field_type": _infer_field_type(value),
        })
    supabase_client.table("extracted_fields").insert(rows).execute()



def get_extracted_fields(document_id: str) -> list[ExtractedField]:
    """
    Extra columns on the extracted_fields table (id, document_id, idx,
    extracted_at, field_category, ...) are dropped automatically —
    ExtractedField only declares what font_consistency actually uses.
    """
    result = (
        supabase_client.table("extracted_fields")
        .select("*")
        .eq("document_id", document_id)
        .execute()
    )
    return [ExtractedField(**_row(row)) for row in result.data]


def get_extracted_fields_for_documents(
    document_ids: list[str],
) -> dict[str, list[ExtractedField]]:
    """
    Same data as get_extracted_fields, batched across a whole case's
    documents in one query — used by the case status WebSocket
    (routers/ws.py), which re-fetches every connected case on each poll
    tick and would otherwise issue one query per document per tick.
    """
    if not document_ids:
        return {}

    result = (
        supabase_client.table("extracted_fields")
        .select("*")
        .in_("document_id", document_ids)
        .execute()
    )
    by_document: dict[str, list[ExtractedField]] = defaultdict(list)
    for raw_row in result.data:
        row = _row(raw_row)
        by_document[row["document_id"]].append(ExtractedField(**row))
    return by_document

def get_extracted_fields_dict(document_id: str) -> dict:
    """Plain {field_name: value} shape — what documents.extracted_fields
    used to hold before it was retired in favour of this table."""
    return {field.field_name: field.field_value for field in get_extracted_fields(document_id)}


def get_extracted_fields_dicts_for_documents(document_ids: list[str]) -> dict[str, dict]:
    """Batched version of get_extracted_fields_dict, for document_repository's
    list_documents/get_documents_in_status to enrich a whole page in one query."""
    by_document = get_extracted_fields_for_documents(document_ids)
    return {
        document_id: {field.field_name: field.field_value for field in fields}
        for document_id, fields in by_document.items()
    }


def add_extracted_field(
    document_id: str,
    field_name: str,
    field_value: object,
    *,
    required_field_names: set[str],
) -> None:
    """
    Adds one manually entered extracted field.

    Manually added fields are treated as human-verified,
    so their confidence is always 1.0.
    """
    # field_value is intentionally `object` (any JSON-serialisable value a
    # human can enter) — too wide for postgrest's strict JSON param type, so
    # cast the row itself rather than narrow the public parameter.
    supabase_client.table("extracted_fields").insert(cast(dict, {
        "document_id": document_id,
        "field_name": field_name,
        "field_value": field_value,
        "confidence": 1.0,
        "field_category": (
            "required" if field_name in required_field_names else "additional"
        ),
        "field_type": _infer_field_type(field_value),
    })).execute()


def delete_fields_for_document(document_id: str) -> None:
    """Removes every extracted-field row for a document that is being deleted."""
    service_supabase_client().table("extracted_fields").delete().eq(
        "document_id", document_id
    ).execute()


def delete_fields_for_documents(document_ids: list[str]) -> None:
    """Batch version of delete_fields_for_document — one round-trip for many documents."""
    if not document_ids:
        return
    service_supabase_client().table("extracted_fields").delete().in_(
        "document_id", document_ids
    ).execute()


def delete_extracted_field(
    document_id: str,
    field_id: str,
) -> None:
    """
    Deletes one extracted field belonging to the specified document.
    """
    (
        supabase_client
        .table("extracted_fields")
        .delete()
        .eq("id", field_id)
        .eq("document_id", document_id)
        .execute()
    )