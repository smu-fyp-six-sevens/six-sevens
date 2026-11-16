from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import cast

from .supabase_client import service_supabase_client, supabase_client
from .supabase_rows import fetch_all, row as _row
from .fraud_detection.font_consistency import ExtractedField
from .models import DocumentStatus


_DOCUMENT_CHUNK = 100  # keeps the in.(...) filter well under URL length limits
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


def _statuses(expected: list[DocumentStatus]) -> list[str]:
    return [status.value for status in expected]


def replace_extracted_fields(
    document_id: str,
    fields: dict,
    *,
    field_confidences: dict | None = None,
    default_confidence: float | None,
    expected_statuses: list[DocumentStatus],
) -> bool:
    """
    Replaces the document's whole field set, one row per field, in one transaction (the replace_extracted_fields
    function). False, writing nothing, when the document has left `expected_statuses`: the function locks the
    document row, so the check and the write can't be split by an approval or a cancel.

    confidence is per field where the model returned one, else default_confidence.
    """
    field_confidences = field_confidences or {}
    rows = []
    for name, value in (fields or {}).items():
        per_field_confidence = field_confidences.get(name)
        rows.append({
            "field_name": name,
            "field_value": value,
            "confidence": per_field_confidence if per_field_confidence is not None else default_confidence,
            "field_type": _infer_field_type(value),
        })
    result = service_supabase_client().rpc(
        "replace_extracted_fields",
        {"p_document_id": document_id, "p_rows": rows, "p_expected_statuses": _statuses(expected_statuses)},
    ).execute()
    return result.data is True


def get_extracted_fields(document_id: str) -> list[ExtractedField]:
    """ExtractedField drops the columns it doesn't declare (document_id)."""
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
    by_document: dict[str, list[ExtractedField]] = defaultdict(list)
    # Paged: a case's statements alone can hold more rows than PostgREST returns in one response.
    for start in range(0, len(document_ids), _DOCUMENT_CHUNK):
        chunk = document_ids[start : start + _DOCUMENT_CHUNK]
        rows = fetch_all(lambda chunk=chunk: supabase_client.table("extracted_fields").select("*").in_("document_id", chunk), "id")
        for row in rows:
            by_document[row["document_id"]].append(ExtractedField(**row))
    return by_document

def get_extracted_fields_dict(document_id: str) -> dict:
    """The document's fields as a plain {field_name: value} dict."""
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
    document_id: str, field_name: str, field_value: object, *, expected_statuses: list[DocumentStatus],
) -> bool:
    """One field an officer typed in, stored at confidence 1.0. False when the document has left `expected_statuses`."""
    # field_value is any JSON value an officer can enter: too wide for postgrest's param type.
    result = service_supabase_client().rpc("add_extracted_field", cast(dict, {
        "p_document_id": document_id,
        "p_field_name": field_name,
        "p_field_value": field_value,
        "p_field_type": _infer_field_type(field_value),
        "p_expected_statuses": _statuses(expected_statuses),
    })).execute()
    return result.data is True


def delete_extracted_field(document_id: str, field_id: str, *, expected_statuses: list[DocumentStatus]) -> bool:
    """Deletes one of this document's fields (never another's). False when it has left `expected_statuses`."""
    result = service_supabase_client().rpc("delete_extracted_field", {
        "p_document_id": document_id, "p_field_id": field_id, "p_expected_statuses": _statuses(expected_statuses),
    }).execute()
    return result.data is True