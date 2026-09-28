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

STATUS: the five tools below are stubbed — their prior implementations were
confirmed incorrect and have been pulled out pending a rewire (see
BACKEND_CLEANUP_PLAN.md Step 5). Each stub keeps its real signature, @tool
decorator and docstring (so the lead/specialists still see it as a normal
tool and the graphs still compile), and honestly reports
`unable_to_assess=True` instead of silently returning a wrong result. The
shared plumbing below (`_document`, `_extracted_fields`, `_file_bytes`,
`_result`, `_with_ids`, `_short`, `_field_value`, `_number`, `_parse_date`)
is kept even though nothing currently calls it, since the rewire will need
it — the previous (incorrect) tool bodies are in git history, not here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ... import document_repository, extracted_fields_repository, storage_service

logger = logging.getLogger("classifier_pipeline.langgraph.detection.tools")

MAX_FINDINGS = 15
_MAX_TEXT = 80
_PENDING_REWIRE = "Not yet correctly implemented — pending rewire"

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


_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d.%m.%Y")


def _parse_date(value: Any):
    if not isinstance(value, str):
        return None
    text = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


# --- Layout & Rendering -----------------------------------------------------


@tool
async def font_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the document's rendered text for font, font-size and emphasis inconsistencies — text that deviates from the surrounding row/region or from how the document renders the same kind of field elsewhere (a classic sign of edited text). Only meaningful for PDFs with native text."""
    return _result("font_consistency_tool", [], unable=_PENDING_REWIRE)


# --- Content Validation -----------------------------------------------------


@tool
async def spelling_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Finds words in the document that look misspelled (after ignoring legal/financial terms, names and the document's own extracted values). Genuine typos in a formal issued document can indicate tampering or forgery."""
    return _result("spelling_tool", [], unable=_PENDING_REWIRE)


@tool
async def date_consistency_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Checks the dates extracted from the document for logical problems: dates in the future, an expiry before its issue date, a certificate dated before the company existed, a birth date after issue."""
    return _result("date_consistency_tool", [], unable=_PENDING_REWIRE)


@tool
async def transaction_balance_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Recomputes a bank statement's running balance from its extracted transactions (previous balance + amount should equal the next balance) and reports rows where the arithmetic doesn't hold — a strong sign a figure was altered."""
    return _result("transaction_balance_tool", [], unable=_PENDING_REWIRE)


# --- Digital Authenticity ---------------------------------------------------


@tool
async def metadata_tool(document_id: Annotated[str, InjectedState("document_id")]) -> dict:
    """Inspects the file's embedded metadata (author, creation/modification dates, producing software) for signs of editing: consumer editing software in the producer chain, or a modification date well after creation."""
    return _result("metadata_tool", [], unable=_PENDING_REWIRE)
