"""
Reading a stored detection report. Reports are JSON saved by whichever version of the code ran the
detection, so a reader gets every key it indexes, with a safe default when an older report lacks it.
Nothing is migrated in the database.
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
