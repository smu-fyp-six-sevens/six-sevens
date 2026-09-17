from __future__ import annotations

from collections import defaultdict
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
    field_confidences: Optional[dict] = None,
    default_confidence: Optional[float],
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
    ExtractedField only declares what font_consistency_v2 actually uses.
    """
    result = (
        supabase_client.table("extracted_fields")
        .select("*")
        .eq("document_id", document_id)
        .execute()
    )
    return [ExtractedField(**row) for row in result.data]


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
    for row in result.data:
        by_document[row["document_id"]].append(ExtractedField(**row))
    return by_document
