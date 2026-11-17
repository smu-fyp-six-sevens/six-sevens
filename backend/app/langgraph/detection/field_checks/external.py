"""external_validation_tool: the document's facts against outside references."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ....fraud_detection.external_validation.runner import run_external_validation
from ..tools import tool_result, with_ids, document_fields
from .bindings import BindingArg, binding_problems, to_bindings

TOOL_NAME = "external_validation_tool"


@tool
async def external_validation_tool(
    document_id: Annotated[str, InjectedState("document_id")],
    document_type: Annotated[str, InjectedState("document_type")],
    bindings: list[BindingArg] | None = None,
) -> dict:
    """Checks the document's facts against outside references that apply to this document type: live ACRA (UEN exists, name, status, incorporation date, registered address), OneMap and HDB addresses, and published tables (CPF rates and ceilings, GST rate, IRAS tax rules, a bank's published identifiers and account format, public holidays for GIRO dates). Pass `bindings` for the inputs named in the external check menu; never invent a role, field or row."""
    fields = await document_fields(document_id)
    if not fields:
        return tool_result(TOOL_NAME, [], unable="No fields are extracted for this document")
    outcome = await run_external_validation(document_id, document_type, fields, to_bindings(bindings))
    if outcome.unable_to_assess:
        return tool_result(TOOL_NAME, [], unable=outcome.unable_to_assess_reason or "Unable to assess")

    findings = [
        {
            "check_code": finding.check_code,
            "severity_hint": finding.severity,
            "source": finding.source,
            "field": finding.field,
            "stated": finding.stated,
            "expected": finding.expected,
            "needs_review": finding.needs_review,
            "summary": finding.summary,
        }
        for finding in outcome.findings
    ]
    result = tool_result(TOOL_NAME, with_ids(TOOL_NAME, findings))
    result.update(
        skipped=[{"check": s.check_code, "reason": s.reason} for s in outcome.skipped_checks],
        bindings_applied=outcome.bindings_applied,
        binding_issues=[*binding_problems(bindings), *outcome.binding_issues],
    )
    return result
