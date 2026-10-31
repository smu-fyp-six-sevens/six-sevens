"""
Precision and recall per detection check, from loan officers' reviews of findings (the findings table).

A reviewed finding is a true positive when the officer marked it correct and a false positive when marked
wrong. A false negative is something real the system didn't flag: a missed issue (no finding raised it) against
the check that should have caught it, or a finding the specialists set aside (dismissed, or kept as info) that the
officer marked correct (one marked wrong was right to go, and counts for neither). So, per check:

* precision = correct / (correct + wrong)    None when none of the check's findings were reviewed
* recall    = correct / (correct + missed)   None when there was nothing the check should have caught

None is shown as "no reviews" / "no examples", never as 0% or 100%. Unreviewed findings count for neither.
"""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import Iterable

from .case_pipeline.models import FindingCategory
from .models import (
    CaseFindingReviews, CheckMetrics, DetectionMetrics, FindingReview, MetricsSummary, ReviewScope, ReviewSignOff,
)

# Placeholders until the team agrees targets: a check under either is flagged on the dashboard.
TARGET_PRECISION = 0.8
TARGET_RECALL = 0.8
# Below this many reviewed findings and missed issues, a check's rates are flagged as not yet meaningful.
MIN_REVIEWS = 5

# The checks a missed issue can be reported against. Per-case: the categories a cross-document finding is
# filed under. Per-document: whatever tools the detection specialists carry, read from them so the list can't
# drift as checks are added (imported on first use: the specialists module pulls in the whole agent stack).
CASE_CHECKS: tuple[str, ...] = tuple(category.value for category in FindingCategory)


@lru_cache(maxsize=1)
def document_checks() -> tuple[str, ...]:
    from .langgraph.detection.specialists import SPECIALISTS

    return tuple(dict.fromkeys(tool.name for spec in SPECIALISTS.values() for tool in spec.tools))


def checks_by_scope() -> dict[str, tuple[str, ...]]:
    return {"document": document_checks(), "case": CASE_CHECKS}


def _rate(hits: int, others: int) -> float | None:
    total = hits + others
    return round(hits / total, 4) if total else None


def _flags(precision: float | None, recall: float | None, reviews: int) -> list[str]:
    flags = []
    if precision is not None and precision < TARGET_PRECISION:
        flags.append("Precision below target")
    if recall is not None and recall < TARGET_RECALL:
        flags.append("Recall below target")
    if reviews < MIN_REVIEWS:
        flags.append("Few reviews")
    return flags


def summarize(reviews: Iterable[FindingReview]) -> MetricsSummary:
    counts: dict[tuple[ReviewScope, str], dict[str, int]] = defaultdict(lambda: {"correct": 0, "wrong": 0, "missed": 0})
    for review in reviews:
        bucket = counts[(review.scope, review.check_name)]
        if review.kind == "missed":
            bucket["missed"] += 1
        elif review.agent_decision == "dismissed" or review.severity == "info":
            if review.verdict == "correct":
                bucket["missed"] += 1
        elif review.verdict in ("correct", "wrong"):
            bucket[review.verdict] += 1

    by_check = []
    for (scope, check_name), c in sorted(counts.items()):
        precision, recall = _rate(c["correct"], c["wrong"]), _rate(c["correct"], c["missed"])
        by_check.append(CheckMetrics(
            check_name=check_name, scope=scope, correct=c["correct"], wrong=c["wrong"], missed=c["missed"],
            precision=precision, recall=recall,
            flags=_flags(precision, recall, c["correct"] + c["wrong"] + c["missed"]),
        ))
    correct = sum(m.correct for m in by_check)
    wrong = sum(m.wrong for m in by_check)
    missed = sum(m.missed for m in by_check)
    return MetricsSummary(
        correct=correct, wrong=wrong, missed=missed,
        precision=_rate(correct, wrong), recall=_rate(correct, missed), by_check=by_check,
    )


def case_metrics(reviews: list[FindingReview], sign_offs: Iterable[ReviewSignOff] = ()) -> CaseFindingReviews:
    """One case: per document (its own findings), across documents (the case analysis), and the whole case."""
    by_document: dict[str, list[FindingReview]] = defaultdict(list)
    for review in reviews:
        if review.scope == "document" and review.document_id:
            by_document[review.document_id].append(review)
    return CaseFindingReviews(
        reviews=reviews,
        sign_offs=list(sign_offs),
        case=summarize(reviews),
        documents={document_id: summarize(items) for document_id, items in by_document.items()},
        cross_document=summarize(r for r in reviews if r.scope == "case"),
        checks={scope: list(checks) for scope, checks in checks_by_scope().items()},
        target_precision=TARGET_PRECISION,
        target_recall=TARGET_RECALL,
    )


def overall_metrics(reviews: list[FindingReview]) -> DetectionMetrics:
    """Every reviewed case, per check: the dashboard's view."""
    return DetectionMetrics(
        overall=summarize(reviews),
        cases_reviewed=len({r.case_id for r in reviews}),
        documents_reviewed=len({r.document_id for r in reviews if r.document_id}),
        target_precision=TARGET_PRECISION,
        target_recall=TARGET_RECALL,
        min_reviews=MIN_REVIEWS,
    )
