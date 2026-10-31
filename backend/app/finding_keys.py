"""
Fingerprints: the key that names one finding within a run, carried by the report (and the risk built from it) and by
the finding's row in the findings table, so an officer's verdict on the Risk Summary lands on the right row
(findings_repository). Built from what the finding says and where it points. Pure: no I/O, no app imports.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

# A specialist that never assessed a finding keeps it with this rationale (langgraph/detection/specialists.py).
UNASSESSED_RATIONALE = "Not assessed by the specialist."


def _digest(*parts: object) -> str:
    text = "\x1f".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:24]


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _bbox(finding: dict[str, Any]) -> str:
    bbox = finding.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox):
        return ",".join(str(round(v)) for v in bbox)
    return ""


def _numbered(keys: Iterable[str]) -> list[str]:
    """Two findings with the same key in one run (the same words at the same place) are told apart by order."""
    seen: dict[str, int] = {}
    numbered = []
    for key in keys:
        seen[key] = seen.get(key, 0) + 1
        numbered.append(key if seen[key] == 1 else f"{key}-{seen[key]}")
    return numbered


def _document_key(finding: dict[str, Any]) -> str:
    return _digest(finding.get("tool"), finding.get("check_code"), finding.get("page"), _bbox(finding), _text(finding.get("summary")))


def kept_decision(finding: dict[str, Any]) -> str:
    if finding.get("ai_accepted"):
        return "accepted"
    if finding.get("rationale") == UNASSESSED_RATIONALE:
        return "not_assessed"
    return "overridden"


def report_findings(report: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """
    Every finding a detection report's tools returned, once each, with the agents' decision: the kept ones, then
    the dismissed ones. As in merge_reports, a dismissed finding another report listed again (a second look) is
    the kept one, matched on tool and summary.
    """
    kept = list(report.get("findings") or [])
    listed = {(f.get("tool"), f.get("summary")) for f in kept}
    seen: set[str] = set()
    dismissed = []
    for consulted in report.get("consulted") or []:
        for item in consulted.get("dismissed") or []:
            key = _document_key(item)
            if (item.get("tool"), item.get("summary")) in listed or key in seen:
                continue
            seen.add(key)
            dismissed.append(item)
    return [(f, kept_decision(f)) for f in kept] + [(f, "dismissed") for f in dismissed]


def assign_document_fingerprints(report: dict[str, Any]) -> None:
    """Sets `fingerprint` on each of the report's findings, in place, before the report is saved."""
    findings = [finding for finding, _ in report_findings(report)]
    for finding, key in zip(findings, _numbered(_document_key(f) for f in findings)):
        finding["fingerprint"] = key


def _case_key(finding: Any) -> str:
    documents = ",".join(sorted(finding.document_ids))
    category = getattr(finding.category, "value", finding.category)
    if finding.hypothesis_id == "deterministic":
        # Written by code, so the words are the same each run.
        return _digest("deterministic", category, documents, _text(finding.summary))
    # The judge words its finding afresh each run: what it rests on is what stays.
    return _digest("agent", category, documents, ",".join(sorted(finding.citations)))


def assign_case_fingerprints(findings: list[Any]) -> None:
    """Sets `fingerprint` on each of a case analysis's findings (case_pipeline.models.CrossDocFinding), in place."""
    for finding, key in zip(findings, _numbered(_case_key(f) for f in findings)):
        finding.fingerprint = key
