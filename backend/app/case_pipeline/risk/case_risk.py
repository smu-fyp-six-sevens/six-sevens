"""
One risk level per case, rolled up from the detection reports of its
documents (see langgraph/detection/graph.py's merge_reports for a report's
shape). Pure and deterministic — no LLM: the specialists already did the
judgement (dismissing false positives, setting each finding's severity),
this only adds their findings up so a loan officer can see which cases
need attention first.

Computed only by a case analysis run (see case_pipeline/service.py), stored
on that run and in risk_scores (see store.py). The case views read it from
there and never recompute it.

Scoring:
- Points per finding from the specialist's final severity (SEVERITY_POINTS).
  The tools' own severity_hint is ignored: it's only an input to the
  specialist, and two tools use a different scale for it.
- Each check counts once per document: its most severe finding there scores
  in full, every further finding from the same check on the same document
  scores REPEAT_WEIGHT of its points. A check reporting one edit as several
  findings (e.g. the same offset on four rows) is one pattern, not four
  pieces of evidence — two different checks both firing still count in full.
- The same finding on several documents (same check, same stated value; see _repeat_key) scores once, on the
  document where it is strongest. The copies on other documents score 0 and are labelled; they still show.
- Low/medium points are capped per document (NOISE_CAP_PER_DOCUMENT) so a
  long document full of minor hits (e.g. spelling) can't push a case to
  High on its own. High findings are never capped.
- The total maps to Low/Medium/High (LEVEL_THRESHOLDS).
- Critical needs corroborated tampering: high findings on 3+ different
  documents. A finding's severity tops out at "high", so Critical can only
  come from the case as a whole.
- The 0-100 score shown to officers is anchored to the level (SCORE_BANDS),
  so a Critical case always outscores a High one even when the High case
  has more raw points — Critical comes from corroboration, not the total.
  Within a level the score fills on a curve that flattens towards the
  level's ceiling, so bigger cases keep ranking above smaller ones instead
  of all piling up at the top.
"""

from __future__ import annotations

from typing import Any

from ..categories import category_for_finding
from ..models import CrossDocFinding
from ...finding_keys import UNASSESSED_RATIONALE, report_findings
from ...models import (
    APPROVED_STATUSES,
    CaseRisk,
    ContributingFinding,
    DismissedFinding,
    DocumentRecord,
    DocumentStatus,
    RiskLevel,
    UnassessedCheck,
    skips_detection,
)

SEVERITY_POINTS: dict[str, float] = {"info": 0, "low": 1, "medium": 3, "high": 8}
REPEAT_WEIGHT = 0.25
NOISE_CAP_PER_DOCUMENT = 6.0
# (minimum total, level), checked highest first.
LEVEL_THRESHOLDS: list[tuple[float, RiskLevel]] = [(8, "High"), (3, "Medium"), (0, "Low")]
CRITICAL_MIN_HIGH_DOCUMENTS = 3
# level -> (lowest score, highest score, the level's minimum points, half-fill
# points). The score starts at the lowest at the minimum points, and every
# further half-fill points closes half of the remaining gap to the highest —
# so it rises fast at first and flattens towards (but never past) the
# ceiling. Critical needs three highs, so it never has under 24 points.
SCORE_BANDS: dict[str, tuple[int, int, float, float]] = {
    "Low": (0, 24, 0, 1.5),
    "Medium": (25, 49, 3, 2.5),
    "High": (50, 79, 8, 16),
    "Critical": (80, 100, 24, 40),
}

def _detection_started(document: DocumentRecord, reports: dict[str, Any], failed_ids: set[str]) -> bool:
    return (
        document.status in (DocumentStatus.DETECTING, DocumentStatus.DETECTED)
        or document.id in reports
        or document.id in failed_ids
    )


def counts_towards_risk(
    document: DocumentRecord, reports: dict[str, Any], failed_ids: set[str]
) -> bool:
    """
    Documents the score is built from: approved ones detection runs on. A document not yet approved (still
    processing, waiting for an officer, failed, rejected or cancelled) is left out, so a case can be scored on what
    is approved; approving another makes the analysis out of date (state.compute_fingerprint) until it runs
    again. An Other document counts only once its detection has started, so one approved before Other documents
    were scanned (no run, no report) stays out instead of holding its case at pending.
    """
    if skips_detection(document) or document.status not in APPROVED_STATUSES:
        return False
    if document.document_type == "unrecognised":
        return _detection_started(document, reports, failed_ids)
    return True


def _level_for(score: float) -> RiskLevel:
    return next(level for minimum, level in LEVEL_THRESHOLDS if score >= minimum)


def _score_out_of_100(level: RiskLevel, points: float) -> int:
    low_score, high_score, minimum_points, half_fill_points = SCORE_BANDS[level]
    position = 1 - 0.5 ** (max(0.0, points - minimum_points) / half_fill_points)
    return round(low_score + position * (high_score - low_score))


def _repeat_key(finding: dict[str, Any]) -> tuple | None:
    """
    What makes two findings one fact: the same check on the same stated value (a UEN a registry has no record of).
    A finding without both is evidence about its own document (a font, a metadata field) and is never merged:
    the same wording on three documents is three pieces of evidence.
    """
    stated = str(finding.get("stated") or "").strip().lower()
    if finding.get("check_code") and stated:
        return (finding.get("tool"), finding["check_code"], stated)
    return None


class _Repeats:
    """Findings that also appear on another document: which ones score 0, and how often the scoring one recurs."""

    def __init__(self) -> None:
        self.zero: dict[str, set[str]] = {}  # document id -> report finding ids
        self.primary: dict[tuple[str, str], int] = {}  # (document id, report finding id) -> other documents carrying it
        self.repeat_of: dict[tuple[str, str], str] = {}  # (document id, report finding id) -> primary finding_id


def _plan_repeats(documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]) -> _Repeats:
    seen: dict[tuple, list[tuple[str, str, float]]] = {}
    for document in documents:
        for finding in reports[document.id].get("findings") or []:
            points, key = SEVERITY_POINTS.get(finding.get("severity"), 0), _repeat_key(finding)
            if points > 0 and key is not None:
                seen.setdefault(key, []).append((document.id, finding["id"], points))
    plan = _Repeats()
    for occurrences in seen.values():
        document_ids = {d for d, _, _ in occurrences}
        if len(document_ids) < 2:
            continue
        keep = max(occurrences, key=lambda o: o[2])  # strongest, the first of equals
        plan.primary[(keep[0], keep[1])] = len(document_ids) - 1
        for document_id, finding_id, _ in occurrences:
            if document_id != keep[0]:
                plan.zero.setdefault(document_id, set()).add(finding_id)
                plan.repeat_of[(document_id, finding_id)] = f"{keep[0][:8]}:{keep[1]}"
    return plan


def _score_document(
    document: DocumentRecord, report: dict[str, Any], repeats: _Repeats | None = None,
) -> list[ContributingFinding]:
    findings = [f for f in report.get("findings") or [] if SEVERITY_POINTS.get(f.get("severity"), 0) > 0]
    # Most severe first, so each check's first finding here is its strongest.
    findings.sort(key=lambda f: -SEVERITY_POINTS[f["severity"]])
    zero = repeats.zero.get(document.id, set()) if repeats else set()
    base_points: list[float] = []
    checks_seen: set[str | None] = set()
    for finding in findings:
        if finding["id"] in zero:
            base_points.append(0.0)  # scored on another document; it must not use up this document's noise cap
            continue
        weight = REPEAT_WEIGHT if finding.get("tool") in checks_seen else 1.0
        checks_seen.add(finding.get("tool"))
        base_points.append(SEVERITY_POINTS[finding["severity"]] * weight)

    noise_total = sum(points for finding, points in zip(findings, base_points) if finding["severity"] != "high")
    # Scales every low/medium finding down evenly once the document hits the
    # cap, so each finding's points still add up to the document's total.
    noise_scale = min(1.0, NOISE_CAP_PER_DOCUMENT / noise_total) if noise_total else 1.0
    return [
        ContributingFinding(
            document_id=document.id,
            filename=document.filename,
            document_type=document.document_type,
            # Report ids ("numerical_checks_tool-1") repeat across documents; the prefix keeps them unique per case.
            finding_id=f"{document.id[:8]}:{finding['id']}",
            tool=finding.get("tool"),
            check_code=finding.get("check_code") if isinstance(finding.get("check_code"), str) else None,
            severity=finding["severity"],
            summary=finding.get("summary") or "",
            rationale=finding.get("rationale") or "",
            points=round(points * (1.0 if finding["severity"] == "high" else noise_scale), 2),
            unreviewed=finding.get("rationale") == UNASSESSED_RATIONALE,
            fingerprint=finding["fingerprint"],
            category=category_for_finding(finding).value,
            also_on=repeats.primary.get((document.id, finding["id"]), 0) if repeats else 0,
            repeat_of=repeats.repeat_of.get((document.id, finding["id"])) if repeats else None,
        )
        for finding, points in zip(findings, base_points)
    ]


def _dismissed(document: DocumentRecord, report: dict[str, Any]) -> list[DismissedFinding]:
    """What the specialists set aside unscored: the findings they dismissed and the ones they kept as info."""
    return [
        DismissedFinding(
            document_id=document.id,
            filename=document.filename,
            document_type=document.document_type,
            finding_id=f"{document.id[:8]}:{finding['id']}",
            tool=finding.get("tool"),
            check_code=finding.get("check_code") if isinstance(finding.get("check_code"), str) else None,
            summary=finding.get("summary") or "",
            reason=finding.get("reason") or finding.get("rationale") or "",
            category=category_for_finding(finding).value,
            fingerprint=finding["fingerprint"],
        )
        for finding, decision in report_findings(report)
        if decision == "dismissed" or finding.get("severity") == "info"
    ]


# A finding backed by several documents counts more, up to this multiple.
_CORROBORATION_STEP = 0.25
_CORROBORATION_CAP = 1.5


def contributing_from_cross_document(
    finding: CrossDocFinding, documents_by_id: dict[str, DocumentRecord]
) -> ContributingFinding:
    anchor = next((documents_by_id[d] for d in finding.document_ids if d in documents_by_id), None)
    multiple = min(_CORROBORATION_CAP, 1 + _CORROBORATION_STEP * (len(finding.document_ids) - 1))
    return ContributingFinding(
        document_id=anchor.id if anchor else "",
        filename=anchor.filename if anchor else "Case",
        document_type=anchor.document_type if anchor else None,
        finding_id=finding.id,
        tool="cross_document_analysis",
        severity=finding.severity,
        summary=finding.summary,
        rationale=finding.rationale,
        points=round(SEVERITY_POINTS[finding.severity] * max(1.0, multiple), 2),
        category=finding.category.value,
        source="cross_document",
        fingerprint=finding.fingerprint,
    )


def rate_findings(findings: list[ContributingFinding]) -> tuple[RiskLevel, int, float, str]:
    """(level, 0-100 score, points, reason) for a set of findings: the same banding a case read uses."""
    points = round(sum(f.points for f in findings), 2)
    level = _level_for(points)
    reason = (
        f"Rated {level} from the combined severity of {len(findings)} finding(s)."
        if findings
        else "Rated Low: detection found nothing worth flagging."
    )
    high_documents = {f.document_id for f in findings if f.severity == "high" and f.points > 0}
    if len(high_documents) >= CRITICAL_MIN_HIGH_DOCUMENTS:
        level = "Critical"
        reason = f"Rated Critical: high-severity findings on {len(high_documents)} documents."
    return level, _score_out_of_100(level, points), points, reason


def scoring_breakdown(risk: CaseRisk) -> dict[str, Any]:
    """How a risk was arrived at, for the case analysis trace: each finding's points and the rules applied."""
    return {
        "state": risk.state,
        "level": risk.level,
        "score": risk.score,
        "points": risk.points,
        "reasons": risk.reasons,
        "documents_scanned": risk.documents_scanned,
        "documents_total": risk.documents_total,
        "severity_points": SEVERITY_POINTS,
        "level_thresholds": LEVEL_THRESHOLDS,
        "critical_needs_high_documents": CRITICAL_MIN_HIGH_DOCUMENTS,
        "noise_cap_per_document": NOISE_CAP_PER_DOCUMENT,
        "repeat_weight": REPEAT_WEIGHT,
        "findings": [
            {
                "finding_id": f.finding_id, "document": f.filename, "tool": f.tool, "severity": f.severity,
                "points": f.points, "category": f.category, "source": f.source, "unreviewed": f.unreviewed,
                "also_on": f.also_on, "repeat_of": f.repeat_of,
            }
            for f in risk.contributing_findings
        ],
        "unassessed_checks": [
            {"document": c.filename, "tool": c.tool, "reason": c.reason} for c in risk.unassessed_checks
        ],
    }


def compute_case_risk(
    documents: list[DocumentRecord],
    reports: dict[str, dict[str, Any]],
    failed_ids: set[str],
    cross_findings: list[CrossDocFinding] | None = None,
    *,
    dedupe: bool = True,
) -> CaseRisk:
    """
    reports: document_id -> its stored detection report.
    failed_ids: documents whose last detection run crashed or was cancelled
    and left no report (see langgraph/trace.py).
    cross_findings: the case analysis's verified findings, when a current one
    exists (see case_pipeline.service); a case that was never analysed passes none.
    """
    eligible = [d for d in documents if counts_towards_risk(d, reports, failed_ids)]
    scanned = [d for d in eligible if d.status == DocumentStatus.DETECTED and d.id in reports]
    scanned_count, total_count = len(scanned), len(eligible)

    if not eligible:
        return CaseRisk(state="no_documents", documents_scanned=scanned_count, documents_total=total_count)
    if any(d.id in failed_ids and d.id not in reports for d in eligible):
        return CaseRisk(state="failed", documents_scanned=scanned_count, documents_total=total_count)
    if len(scanned) < len(eligible):
        return CaseRisk(state="pending", documents_scanned=scanned_count, documents_total=total_count)

    findings: list[ContributingFinding] = []
    dismissed: list[DismissedFinding] = []
    unassessed: list[UnassessedCheck] = []
    repeats = _plan_repeats(scanned, reports) if dedupe else None
    for document in scanned:
        report = reports[document.id]
        findings.extend(_score_document(document, report, repeats))
        dismissed.extend(_dismissed(document, report))
        unassessed.extend(
            UnassessedCheck(
                document_id=document.id,
                filename=document.filename,
                tool=item.get("tool"),
                reason=item.get("reason"),
            )
            for item in report.get("unable_to_assess") or []
        )

    documents_by_id = {d.id: d for d in documents}
    findings.extend(contributing_from_cross_document(f, documents_by_id) for f in cross_findings or [])

    level, score, points, reason = rate_findings(findings)

    findings.sort(key=lambda f: (-f.points, -SEVERITY_POINTS[f.severity], f.filename))
    return CaseRisk(
        state="scored",
        level=level,
        score=score,
        points=points,
        contributing_findings=findings,
        dismissed_findings=dismissed,
        unassessed_checks=unassessed,
        reasons=[reason],
        documents_scanned=scanned_count, documents_total=total_count,
    )
