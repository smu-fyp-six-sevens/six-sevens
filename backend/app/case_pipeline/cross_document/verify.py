"""
Code-side check of an investigator's verdict. A verdict stands only if the
evidence it cites is real, its refutation probe ran, and its severity and
category fit what the evidence is. The model's own account is never trusted.
"""

from __future__ import annotations

from typing import Any, Optional, cast

from ..models import (
    MODEL_CHOOSABLE_CATEGORIES, CrossDocFinding, FindingCategory, Hypothesis, Severity,
)
from .bias import has_protected_text
from .evidence import CaseContext
from .tools import BREACH_FLAGS, COMPUTED_TOOLS, IDENTITY_FLAGS, VARIANCE_FLAGS

_SEVERITIES = ("low", "medium", "high")
_MAX_CITATIONS = 12


def _read_keys(context: CaseContext, call: dict[str, Any]) -> set[tuple[str, str]]:
    """The (document, field) pairs one tool call read."""
    return {
        (entry["doc_key"], entry["field"])
        for ref in call.get("refs", []) if (entry := context.ledger.refs.get(ref))
    }


def _probe_ran(context: CaseContext, hypothesis: Hypothesis, calls: list[dict[str, Any]]) -> bool:
    """
    Whether a real attempt to disprove the claim ran. Reading back the very fields the claim's own
    checks already read tests nothing, so that doesn't count: a probe has to look at something else.
    """
    supporting: set[tuple[str, str]] = set()
    for call in calls:
        if not call.get("probe"):
            supporting |= _read_keys(context, call)
    for call in calls:
        if not call.get("probe") or not call.get("available"):
            continue
        if call.get("tool") == "get_fields" and not (_read_keys(context, call) - supporting):
            continue
        if hypothesis.refutation_document is None:
            return True
        touched = {context.ledger.refs[r]["doc_key"] for r in call.get("refs", []) if r in context.ledger.refs}
        touched.add(call.get("document"))
        if hypothesis.refutation_document in touched:
            return True
    return False


def verify_verdict(
    context: CaseContext, hypothesis: Hypothesis, verdict: dict[str, Any], calls: list[dict[str, Any]]
) -> tuple[Optional[CrossDocFinding], str]:
    """(finding, "") when the verdict stands, else (None, why it was discarded)."""
    if verdict.get("conclusive") is not True:
        return None, "not conclusive"

    citations = [c for c in verdict.get("citations") or [] if isinstance(c, str)][:_MAX_CITATIONS]
    if not citations:
        return None, "no citations"
    unknown = [c for c in citations if c not in context.ledger.refs]
    if unknown:
        return None, f"citation not issued by a tool: {unknown[0]}"
    if not _probe_ran(context, hypothesis, calls):
        return None, "refutation probe did not run"

    # The whole account is screened, not just the headline: any mention is grounds to discard it.
    text = " ".join(str(verdict.get(k) or "") for k in ("summary", "rationale", "probe_summary"))
    if has_protected_text(f"{hypothesis.claim} {text}"):
        return None, "rests on a protected attribute"

    entries = [context.ledger.refs[c] for c in citations]
    # This module relates documents to each other. One document's own figures looking odd is the
    # per-document scan's business, unless the evidence is something computed over the data.
    computed = any(e.get("source") in COMPUTED_TOOLS or e.get("field") == "transaction_summary" for e in entries)
    if len({e["doc_key"] for e in entries}) < 2 and not computed:
        return None, "cites a single document and no computed fact"
    flags = sorted({e["breach_flag"] for e in entries if e.get("breach_flag") in BREACH_FLAGS})
    registry_backed = any(e.get("category") == FindingCategory.REGISTRY_DISCREPANCY.value for e in entries)

    severity = verdict.get("severity") if verdict.get("severity") in _SEVERITIES else "medium"
    if severity == "high" and not (flags or registry_backed):
        severity = "medium"

    if any(f in IDENTITY_FLAGS for f in flags):
        category = FindingCategory.IDENTITY_ENTITY_CONFLICT
    elif any(f in VARIANCE_FLAGS for f in flags):
        category = FindingCategory.CROSS_DOCUMENT_VARIANCE
    else:
        try:
            proposed = FindingCategory(verdict.get("category"))
        except ValueError:
            proposed = FindingCategory.BEHAVIOURAL_ANOMALY
        category = proposed if proposed in MODEL_CHOOSABLE_CATEGORIES else FindingCategory.BEHAVIOURAL_ANOMALY

    document_ids = []
    for entry in entries:
        view = context.docs.get(entry["doc_key"])
        if view and view.id not in document_ids:
            document_ids.append(view.id)

    return CrossDocFinding(
        id=f"xd-{hypothesis.id}",
        hypothesis_id=hypothesis.id,
        summary=str(verdict.get("summary") or hypothesis.claim)[:400],
        rationale=str(verdict.get("rationale") or "")[:1200],
        severity=cast(Severity, severity),
        category=category,
        citations=citations,
        document_ids=document_ids,
        probe_summary=str(verdict.get("probe_summary") or "")[:400],
        breach_flags=flags,
    ), ""
