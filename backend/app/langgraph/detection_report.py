"""
Reading a stored detection report. Reports are JSON in detection_results, so a reader gets every key it
indexes, with a safe default when a report lacks one (a run that stopped partway, or a later schema change).
"""

from __future__ import annotations

from typing import Any

SCHEMA_VERSION = 2

_SEVERITIES = ("info", "low", "medium", "high")
_VERDICTS = ("clean", "flagged", "inconclusive")
_LIST_KEYS = ("findings", "unable_to_assess", "nothing_to_check", "consulted", "dismissed")


def _finding(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    severity = raw.get("severity")
    return {
        **raw,
        "id": raw.get("id") if isinstance(raw.get("id"), str) else "",
        "tool": raw.get("tool") if isinstance(raw.get("tool"), str) else None,
        "severity": severity if severity in _SEVERITIES else "medium",
        "summary": raw.get("summary") if isinstance(raw.get("summary"), str) else "",
        "rationale": raw.get("rationale") if isinstance(raw.get("rationale"), str) else "",
    }


def severity_counts(findings: Any) -> dict[str, int]:
    """How many of a report's findings carry each severity (the Documents list's anomaly counts)."""
    counts: dict[str, int] = dict.fromkeys(_SEVERITIES, 0)
    for finding in findings if isinstance(findings, list) else []:
        severity = finding.get("severity") if isinstance(finding, dict) else None
        counts[severity if severity in _SEVERITIES else "medium"] += 1
    return counts


def read_counts(raw: Any) -> dict[str, int] | None:
    """Stored counts as written by detection_results.save_report, or None for a report saved before them."""
    if not isinstance(raw, dict) or not all(isinstance(raw.get(s), int) for s in _SEVERITIES):
        return None
    return {s: raw[s] for s in _SEVERITIES}


def read_report(raw: Any) -> dict | None:
    """The report with its expected keys present, or None when `raw` is not a report at all."""
    if not isinstance(raw, dict):
        return None
    report = dict(raw)
    for key in _LIST_KEYS:
        value = report.get(key)
        report[key] = value if isinstance(value, list) else []
    report["findings"] = [f for f in map(_finding, report["findings"]) if f is not None]
    # A report with no readable verdict is not a clean one.
    report["verdict"] = report.get("verdict") if report.get("verdict") in _VERDICTS else "inconclusive"
    if not isinstance(report.get("summary"), str):
        report["summary"] = ""
    if not isinstance(report.get("schema_version"), int):
        report["schema_version"] = 0
    return report
