"""Static tool -> category map for per-document findings. Code decides this, never the model."""

from __future__ import annotations

from .models import FindingCategory

TOOL_CATEGORIES: dict[str, FindingCategory] = {
    "font_consistency_tool": FindingCategory.TAMPERING_FORENSIC,
    "layout_anomalies_tool": FindingCategory.TAMPERING_FORENSIC,
    "hidden_text_tool": FindingCategory.TAMPERING_FORENSIC,
    "spelling_tool": FindingCategory.TAMPERING_FORENSIC,
    "date_consistency_tool": FindingCategory.TAMPERING_FORENSIC,
    "metadata_tool": FindingCategory.TAMPERING_FORENSIC,
    "image_manipulation_tool": FindingCategory.TAMPERING_FORENSIC,
    "image_tampering_tool": FindingCategory.TAMPERING_FORENSIC,
    "ai_generation_tool": FindingCategory.TAMPERING_FORENSIC,
    "prompt_injection_tool": FindingCategory.TAMPERING_FORENSIC,
    "numerical_checks_tool": FindingCategory.TAMPERING_FORENSIC,
    "external_validation_tool": FindingCategory.REGISTRY_DISCREPANCY,
    "company_name_consistency_tool": FindingCategory.CROSS_DOCUMENT_VARIANCE,
    "address_consistency_tool": FindingCategory.CROSS_DOCUMENT_VARIANCE,
}


_IDENTIFIER_CHECKS = frozenset({"individual_id", "company_uen"})


def category_for_finding(finding: dict) -> FindingCategory:
    """Category of one finding in a detection report."""
    tool = finding.get("tool")
    if tool == "case_identity_match":
        # The check is the second part of its id ("case-identity:<check>:...").
        check = str(finding.get("id") or "").split(":")[1:2]
        if check and check[0] in _IDENTIFIER_CHECKS:
            return FindingCategory.IDENTITY_ENTITY_CONFLICT
        return FindingCategory.CROSS_DOCUMENT_VARIANCE
    # An unmapped tool is non-reducible: never let an unknown finding be argued away.
    return TOOL_CATEGORIES.get(tool or "", FindingCategory.TAMPERING_FORENSIC)
