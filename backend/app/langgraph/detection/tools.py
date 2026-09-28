"""
Detection tools — one thin wrapper per check, each parameter-bound to the
document under review (`document_id` comes from graph state via InjectedState,
never from the model). Every tool returns the same shape:

    {"tool": name, "unable_to_assess": bool, "reason": str | None,
     "findings": [{"id": "<tool>-<n>", "summary": str, ...detail}], "truncated": int}

Findings are size-capped and fact-only; severity and rationale are added by
the specialist that called the tool, never by the tool itself. "Unable to
assess" is its own result, never a pass. Text inside findings is document
content and must be treated as untrusted data by whatever reads it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date, datetime
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ... import document_repository, extracted_fields_repository, storage_service
from ...fraud_detection.font_consistency_v2 import check_font_consistency_v2
from ...fraud_detection.spelling_anomaly import check_spelling_anomalies

logger = logging.getLogger("classifier_pipeline.langgraph.detection.tools")

MAX_FINDINGS = 15
_MAX_TEXT = 80

# Per-run cache so several specialists calling tools on the same document
# don't each re-download the file or re-read the row. Cleared by
# graph.run_detection when the run ends.
_context: dict[str, dict[str, Any]] = {}


def clear_context(document_id: str) -> None:
    _context.pop(document_id, None)


async def _document(document_id: str):
    entry = _context.setdefault(document_id, {})
    if "document" not in entry:
        entry["document"] = await asyncio.to_thread(document_repository.get_document, document_id)
    return entry["document"]


async def _extracted_fields(document_id: str) -> list:
    entry = _context.setdefault(document_id, {})
    if "fields" not in entry:
        try:
            entry["fields"] = await asyncio.to_thread(
                extracted_fields_repository.get_extracted_fields, document_id
            )
        except Exception:  # noqa: BLE001 — enrichment only; checks degrade without it
            logger.exception("Failed to fetch extracted fields for document %s", document_id)
            entry["fields"] = []
    return entry["fields"]


async def _file_bytes(document_id: str) -> bytes | None:
    entry = _context.setdefault(document_id, {})
    if "bytes" not in entry:
        document = await _document(document_id)
        if document is None or not document.storage_path:
            entry["bytes"] = None
        else:
            entry["bytes"] = await asyncio.to_thread(storage_service.download_file, document.storage_path)
    return entry["bytes"]


def _result(tool_name: str, findings: list[dict], *, unable: str | None = None) -> dict:
    truncated = max(0, len(findings) - MAX_FINDINGS)
    return {
        "tool": tool_name,
        "unable_to_assess": unable is not None,
        "reason": unable,
        "findings": findings[:MAX_FINDINGS],
        "truncated": truncated,
    }


def _with_ids(tool_name: str, findings: list[dict]) -> list[dict]:
    return [{"id": f"{tool_name}-{i + 1}", **finding} for i, finding in enumerate(findings)]


def _short(text: Any) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= _MAX_TEXT else text[: _MAX_TEXT - 1] + "…"


def _field_value(fields: list, name: str) -> Any:
    for field in fields:
        if field.field_name == name:
            value = field.field_value
            if isinstance(value, str) and value.strip()[:1] in ('[', '{', '"'):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return value
            return value
    return None


# --- Layout & Rendering -----------------------------------------------------


@tool
async def font_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the document's rendered text for font, font-size and emphasis inconsistencies — text that deviates from the surrounding row/region or from how the document renders the same kind of field elsewhere (a classic sign of edited text). Only meaningful for PDFs with native text."""
    file_bytes = await _file_bytes(document_id)
    if file_bytes is None:
        return _result("font_consistency_tool", [], unable="Document file is not available")
    fields = await _extracted_fields(document_id)
    result = await asyncio.to_thread(check_font_consistency_v2, document_id, file_bytes, fields)
    if result.unable_to_assess:
        return _result("font_consistency_tool", [], unable=result.unable_to_assess_reason or "Unable to assess")

    findings = []
    for anomaly in result.anomalies:
        where = [f"p{loc.page}: '{_short(loc.text)}'" for loc in anomaly.locations[:3]]
        finding = {
            "summary": (
                f"{anomaly.deviation_type} deviation ({anomaly.occurrence_count}x): "
                f"detected {anomaly.detected}, expected {anomaly.expected}"
            ),
            "deviation_type": anomaly.deviation_type,
            "locations": where,
        }
        if anomaly.field_name:
            finding["field_name"] = anomaly.field_name
        findings.append(finding)
    return _result("font_consistency_tool", _with_ids("font_consistency_tool", findings))


# --- Content Validation -----------------------------------------------------


@tool
async def spelling_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Finds words in the document that look misspelled (after ignoring legal/financial terms, names and the document's own extracted values). Genuine typos in a formal issued document can indicate tampering or forgery."""
    document = await _document(document_id)
    file_bytes = await _file_bytes(document_id)
    if document is None or file_bytes is None:
        return _result("spelling_tool", [], unable="Document file is not available")
    fields = await _extracted_fields(document_id)
    result = await check_spelling_anomalies(document_id, file_bytes, document.filename, fields)
    if result.unable_to_assess:
        return _result("spelling_tool", [], unable=result.unable_to_assess_reason or "Unable to assess")

    findings = [
        {
            "summary": (
                f"'{a.word}' on page {a.page}"
                + (f" (did you mean '{a.suggested_correction}'?)" if a.suggested_correction else "")
            ),
            "word": a.word,
            "page": a.page,
            "context": _short(a.surrounding_text),
        }
        for a in result.anomalies
    ]
    return _result("spelling_tool", _with_ids("spelling_tool", findings))


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d.%m.%Y")
# (earlier field, later field) — the later date must not precede the earlier.
_DATE_ORDER_PAIRS = (
    ("date_of_issue", "date_of_expiry"),
    ("incorporation_date", "certificate_date"),
    ("date_of_birth", "date_of_issue"),
)
_FUTURE_ONLY_FIELDS = ("date_of_expiry",)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@tool
async def date_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the dates extracted from the document for logical problems: dates in the future, an expiry before its issue date, a certificate dated before the company existed, a birth date after issue."""
    fields = await _extracted_fields(document_id)
    dates: dict[str, date] = {}
    for field in fields:
        if "date" in field.field_name:
            parsed = _parse_date(_field_value(fields, field.field_name))
            if parsed:
                dates[field.field_name] = parsed
    if not dates:
        return _result("date_consistency_tool", [], unable="No parseable dates among the extracted fields")

    today = date.today()
    findings = []
    for name, value in dates.items():
        if value > today and name not in _FUTURE_ONLY_FIELDS:
            findings.append({"summary": f"{name} ({value.isoformat()}) is in the future", "fields": [name]})
    for earlier, later in _DATE_ORDER_PAIRS:
        if earlier in dates and later in dates and dates[later] < dates[earlier]:
            findings.append({
                "summary": f"{later} ({dates[later].isoformat()}) is before {earlier} ({dates[earlier].isoformat()})",
                "fields": [earlier, later],
            })
    return _result("date_consistency_tool", _with_ids("date_consistency_tool", findings))


# --- Digital Authenticity ---------------------------------------------------

_EDITING_SOFTWARE = re.compile(
    r"photoshop|gimp|canva|illustrator|inkscape|sejda|ilovepdf|smallpdf|pdfescape|pdf-?xchange|"
    r"foxit|nitro|microsoft\s*word|libreoffice|openoffice|\bwps\b|paint\.net|pixlr",
    re.IGNORECASE,
)
_SOFTWARE_KEYS = ("Software", "Creator", "Producer", "CreatorTool")
_EXIF_DATE = re.compile(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_MODIFIED_AFTER_CREATED_SECONDS = 24 * 3600


def _exif_datetime(value: Any) -> datetime | None:
    match = _EXIF_DATE.match(str(value or ""))
    if not match:
        return None
    return datetime(*(int(g) for g in match.groups()))


@tool
async def metadata_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Inspects the file's embedded metadata (author, creation/modification dates, producing software) for signs of editing: consumer editing software in the producer chain, or a modification date well after creation."""
    document = await _document(document_id)
    metadata = (document.metadata if document else None) or {}
    if not metadata:
        return _result("metadata_tool", [], unable="No embedded metadata available for this file")

    findings = []
    for key in _SOFTWARE_KEYS:
        value = metadata.get(key)
        if value and _EDITING_SOFTWARE.search(str(value)):
            findings.append({
                "summary": f"{key} names editing software not typical of an issuing institution: '{_short(value)}'",
                "metadata_key": key,
            })
    created = _exif_datetime(metadata.get("CreateDate"))
    modified = _exif_datetime(metadata.get("ModifyDate"))
    if created and modified and (modified - created).total_seconds() > _MODIFIED_AFTER_CREATED_SECONDS:
        findings.append({
            "summary": f"File was modified {modified.isoformat()} — after it was created {created.isoformat()}",
            "metadata_key": "ModifyDate",
        })
    return _result("metadata_tool", _with_ids("metadata_tool", findings))


# --- Numerical checks (Content Validation) ----------------------------------

_BALANCE_TOLERANCE = 0.01


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


@tool
async def transaction_balance_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Recomputes a bank statement's running balance from its extracted transactions (previous balance + amount should equal the next balance) and reports rows where the arithmetic doesn't hold — a strong sign a figure was altered."""
    fields = await _extracted_fields(document_id)
    transactions = _field_value(fields, "transactions")
    if not isinstance(transactions, list) or not transactions:
        return _result("transaction_balance_tool", [], unable="No extracted transactions on this document")

    rows = [
        (t.get("date"), _number(t.get("amount")), _number(t.get("balance")), t.get("description"))
        for t in transactions
        if isinstance(t, dict)
    ]
    findings = []
    checked = 0
    previous_balance: float | None = None
    for row_date, amount, balance, description in rows:
        if balance is not None and previous_balance is not None and amount is not None:
            checked += 1
            expected = round(previous_balance + amount, 2)
            if abs(expected - balance) > _BALANCE_TOLERANCE:
                findings.append({
                    "summary": (
                        f"Row '{_short(description)}' ({row_date}): balance {balance:,.2f} but previous "
                        f"balance {previous_balance:,.2f} + amount {amount:,.2f} = {expected:,.2f}"
                    ),
                    "row_date": row_date,
                })
        if balance is not None:
            previous_balance = balance
    if checked == 0:
        return _result("transaction_balance_tool", [], unable="Transactions carry no running balances to verify")
    return _result("transaction_balance_tool", _with_ids("transaction_balance_tool", findings))
