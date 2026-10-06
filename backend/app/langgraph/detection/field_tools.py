"""The batched numerical checks tool. The binder fills in each check's inputs from the document's fields,
then fraud_detection/numerical_validation runs the checks."""

from __future__ import annotations

from typing import Annotated

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from ...fraud_detection.numerical_validation.runner import finding_dict, run_numerical_checks
from .tools import _result, _with_ids, document_fields

TOOL_NAME = "numerical_checks_tool"


@tool
async def numerical_checks_tool(
    document_id: Annotated[str, InjectedState("document_id")],
    document_type: Annotated[str, InjectedState("document_type")],
) -> dict:
    """Runs every numerical check that applies to this document type. A check whose inputs can't be matched to the document's fields is returned under skipped with the reason. Rows of a statement that no check uses are returned as unassigned."""
    from .binder import bind_numerical_checks  # local: binder imports specialists, which imports this module

    fields = await document_fields(document_id)
    if not fields:
        return _result(TOOL_NAME, [], unable="No fields are extracted for this document")
    binding = await bind_numerical_checks(document_type, fields)
    outcome = run_numerical_checks(document_type, fields, binding.bindings)
    findings = _with_ids(TOOL_NAME, [finding_dict(f) for f in outcome.findings])
    result = _result(TOOL_NAME, findings)
    result.update(
        skipped=[{"check": s.check_code, "reason": s.reason} for s in outcome.skipped],
        breakdown=outcome.breakdown,
        unassigned=outcome.unassigned,
        bindings_applied=outcome.applied,
        binding_issues=outcome.issues,
        binding_error=binding.error,
    )
    return result
