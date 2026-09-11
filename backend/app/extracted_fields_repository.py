from __future__ import annotations

from typing import Optional

from .supabase_client import supabase_client
from .fraud_detection.font_consistency_v2 import ExtractedField

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
    return "string"


def replace_extracted_fields(
    document_id: str,
    fields: dict,
    *,
    confidence: Optional[float],
    required_field_names: set[str],
) -> None:
    """
    Mirrors a document's extracted_fields into the normalised extracted_fields
    table — one row per field, rather than the single jsonb blob on
    documents.extracted_fields — so individual fields can be queried,
    joined, or reported on directly.

    Replaces (delete-then-insert) rather than upserts: re-processing a
    document (a reclassification escalation, or a loan officer's manual
    correction — see routers/documents.py's PATCH endpoint) should leave
    exactly the current field set, not accumulate stale rows from a
    previous attempt under different field names.

    field_category records whether a field was one of the document type's
    required schema fields (see extraction_prompts.EXTRACTION_SCHEMAS) or
    one the LLM added on its own initiative — confidence is currently a
    single document-level value (the LLM doesn't return per-field
    confidence), so every row for a given call shares the same number.
    """
    supabase_client.table("extracted_fields").delete().eq("document_id", document_id).execute()

    if not fields:
        return

    rows = [
        {
            "document_id": document_id,
            "field_name": name,
            "field_value": value,
            "confidence": confidence,
            "field_category": "required" if name in required_field_names else "additional",
            "field_type": _infer_field_type(value),
        }
        for name, value in fields.items()
    ]
    supabase_client.table("extracted_fields").insert(rows).execute()



def get_extracted_fields(document_id: str) -> list[ExtractedField]:
    """
    Extra columns on the extracted_fields table (id, document_id, idx,
    extracted_at, field_category, ...) are dropped automatically —
    ExtractedField only declares what font_consistency_v2 actually uses.
    """
    result = (
        supabase_client.table("extracted_fields")
        .select("*")
        .eq("document_id", document_id)
        .execute()
    )
    return [ExtractedField(**row) for row in result.data]
