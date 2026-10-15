"""numerical_checks_tool: calculations on the document's own figures."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ....fraud_detection.numerical_validation.runner import finding_dict, run_numerical_checks
from ..tools import _result, _with_ids, document_fields
from .bindings import BindingArg, to_bindings

TOOL_NAME = "numerical_checks_tool"


@tool
async def numerical_checks_tool(
    document_id: Annotated[str, InjectedState("document_id")],
    document_type: Annotated[str, InjectedState("document_type")],
    bindings: list[BindingArg] | None = None,
) -> dict:
    """Runs every numerical check (a calculation on the document's own figures: totals, balances, casts, arithmetic, checksums) that applies to this document type. A check whose inputs are not bound is returned under skipped with the reason. Rows of a statement that no check uses are returned as unassigned. Pass `bindings` for the inputs named in the numerical check menu; never invent a role, field or row."""
    fields = await document_fields(document_id)
    if not fields:
        return _result(TOOL_NAME, [], unable="No fields are extracted for this document")
    outcome = run_numerical_checks(document_type, fields, to_bindings(bindings), group="numerical")
    result = _result(TOOL_NAME, _with_ids(TOOL_NAME, [finding_dict(f) for f in outcome.findings]))
    result.update(
        skipped=[{"check": s.check_code, "reason": s.reason} for s in outcome.skipped],
        breakdown=outcome.breakdown,
        unassigned=outcome.unassigned,
        bindings_applied=outcome.applied,
        binding_issues=outcome.issues,
    )
    return result
