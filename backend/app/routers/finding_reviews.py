"""
Loan officers' reviews of detection findings, and the precision and recall per check they give.

On the case page an officer marks each finding correct or wrong, per document and for the cross-document
findings, and reports issues detection missed against the check that should have caught them. A verdict is
stored on the finding's row (findings_repository), named by its fingerprint. Every change returns the case's
reviews and metrics afresh, so the page never computes them itself. Only a case created with reviews
(cases.reviews_required) takes them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import (
    case_stage,
    detection_metrics,
    document_repository,
    events,
    findings_repository,
)
from ..auth import CurrentUser
from ..events import officer_event, types
from ..models import (
    CaseFindingReviews, CaseRecord, CaseStage, DetectionMetrics, FindingReview, ReportMissedIssueRequest,
    SetFindingVerdictsRequest, SignOffRequest, UpdateMissedIssueRequest,
)
from ._dependencies import bad_gateway_on_error, get_case_or_404, get_editable_case_or_404, to_thread_or_502
from ..snapshots import push_case_snapshot


def _taking_reviews(case: CaseRecord) -> CaseRecord:
    if not case.reviews_required:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This case was created without finding reviews.")
    return case


async def _reviewed_case(case: CaseRecord = Depends(get_case_or_404)) -> CaseRecord:
    return _taking_reviews(case)


async def _editable_reviewed_case(case: CaseRecord = Depends(get_editable_case_or_404)) -> CaseRecord:
    return _taking_reviews(case)


router = APIRouter(tags=["finding-reviews"])
logger = logging.getLogger(__name__)

_SAVE_FAILED = "Failed to save the review. Check Supabase table access."


async def _case_reviews(case_id: str) -> CaseFindingReviews:
    reviews, sign_offs = await asyncio.gather(
        asyncio.to_thread(findings_repository.list_reviews_for_case, case_id),
        asyncio.to_thread(findings_repository.list_sign_offs, case_id),
    )
    return detection_metrics.case_metrics(reviews, sign_offs)


async def _document_in_case(case_id: str, document_id: UUID | None) -> str | None:
    """The document id as a string, after checking the document is in this case."""
    if document_id is None:
        return None
    document = await asyncio.to_thread(document_repository.get_document, str(document_id), include_fields=False)
    if document is None or document.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found in this case")
    return str(document_id)


def _check_fits(scope: str, check_name: str, note: str | None) -> None:
    if check_name not in detection_metrics.checks_by_scope()[scope]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"That check can't be reviewed on a {scope}.")
    if check_name == detection_metrics.OTHER_CHECK and not note:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Describe what was missed when it fits none of the checks.",
        )


async def _review_in_case(case_id: str, review_id: UUID) -> FindingReview:
    review = await asyncio.to_thread(findings_repository.get_review, str(review_id))
    if review is None or review.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found in this case")
    return review


async def _record_saved(case_id: str, verdicts: list[tuple[str, str | None, str | None]]) -> None:
    """One audit entry per document (or the cross-document group) reviewed: who, and how many findings they
    marked real, false alarms, or cleared. `verdicts` is (scope, document id, verdict) per finding."""
    groups: dict[tuple[str, str | None], list[str | None]] = {}
    for scope, document_id, verdict in verdicts:
        groups.setdefault((scope, document_id), []).append(verdict)
    for (scope, document_id), marks in groups.items():
        await events.record(officer_event(
            types.FINDING_REVIEW_SAVED, case_id=case_id, document_id=document_id,
            detail={
                "scope": scope, "real": marks.count("correct"), "false_alarms": marks.count("wrong"),
                "cleared": marks.count(None),
            },
        ))
    # A verdict can finish the evaluation (Pending Evaluation -> Case Analysed), or put it back; then show the page.
    try:
        await asyncio.to_thread(case_stage.sync_case_stage, case_id)
    except Exception:
        logger.exception("Failed to update the stage of case %s", case_id)
    await push_case_snapshot(case_id)


@router.get("/cases/{case_id}/finding-reviews", response_model=CaseFindingReviews)
async def get_case_finding_reviews(case_id: str, case: CaseRecord = Depends(_reviewed_case)) -> CaseFindingReviews:
    """Every review in the case, with precision and recall per document, across documents and for the case."""
    with bad_gateway_on_error(
        "Failed to load the case's reviews. Check Supabase table access.", "Failed to list reviews for case %s", case_id
    ):
        return await _case_reviews(case_id)


@router.put("/cases/{case_id}/finding-reviews/verdicts", response_model=CaseFindingReviews)
async def set_finding_verdicts(
    case_id: str, request: SetFindingVerdictsRequest, user: CurrentUser, case: CaseRecord = Depends(_editable_reviewed_case),
) -> CaseFindingReviews:
    """Saves one group's review in one request: the officer ticked the findings that are real (correct) and left
    the rest (wrong); a null verdict clears the mark. Any officer can change a verdict; the latest one stands.
    Every finding is looked up before anything is written, so a bad entry saves nothing."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save verdicts on case %s", case_id):
        targets: list[tuple[str, str | None, str]] = []
        for item in request.verdicts:
            document_id = await _document_in_case(case_id, item.document_id)
            row = await asyncio.to_thread(
                findings_repository.find_current, case_id, item.scope, document_id, item.fingerprint,
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A finding was replaced by a newer run. Reload the case and review again.",
                )
            targets.append((item.scope, document_id, row["id"]))
        for (_, _, row_id), item in zip(targets, request.verdicts):
            await asyncio.to_thread(findings_repository.set_verdict, row_id, item.verdict, user)
        reviews = await _case_reviews(case_id)
    await _record_saved(
        case_id, [(scope, document_id, item.verdict) for (scope, document_id, _), item in zip(targets, request.verdicts)],
    )
    return reviews


@router.put("/cases/{case_id}/finding-reviews/sign-offs", response_model=CaseFindingReviews)
async def sign_off_group(
    case_id: str, request: SignOffRequest, user: CurrentUser, case: CaseRecord = Depends(_editable_reviewed_case),
) -> CaseFindingReviews:
    """Saves the review of a group with no findings to mark, so it moves to Reviewed like any other."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save a sign-off on case %s", case_id):
        document_id = await _document_in_case(case_id, request.document_id)
        await asyncio.to_thread(findings_repository.sign_off, case_id, request.scope, document_id, user)
        reviews = await _case_reviews(case_id)
    await events.record(officer_event(
        types.FINDING_REVIEW_SAVED, case_id=case_id, document_id=document_id,
        detail={"scope": request.scope, "real": 0, "false_alarms": 0, "cleared": 0},
    ))
    await push_case_snapshot(case_id)
    return reviews


@router.post(
    "/cases/{case_id}/finding-reviews/missed", response_model=CaseFindingReviews, status_code=status.HTTP_201_CREATED,
)
async def report_missed_issue(
    case_id: str, request: ReportMissedIssueRequest, user: CurrentUser, case: CaseRecord = Depends(_editable_reviewed_case),
) -> CaseFindingReviews:
    """Records an issue no finding raised, against the check that should have caught it: a false negative."""
    _check_fits(request.scope, request.check_name, request.note)
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save a missed issue on case %s", case_id):
        document_id = await _document_in_case(case_id, request.document_id)
        await asyncio.to_thread(
            findings_repository.add_missed,
            case_id=case_id, scope=request.scope, document_id=document_id, check_name=request.check_name,
            note=request.note, reviewer=user,
        )
        reviews = await _case_reviews(case_id)
    await events.record(officer_event(
        types.MISSED_ISSUE_REPORTED, case_id=case_id, document_id=document_id,
        detail={"scope": request.scope, "check_name": request.check_name},
    ))
    return reviews


@router.patch("/cases/{case_id}/finding-reviews/{review_id}", response_model=CaseFindingReviews)
async def update_missed_issue(
    case_id: str, review_id: UUID, request: UpdateMissedIssueRequest, user: CurrentUser,
    case: CaseRecord = Depends(_editable_reviewed_case),
) -> CaseFindingReviews:
    """Corrects a reported missed issue (its check or note), e.g. one filed against the wrong check. A verdict
    on a finding is changed by reviewing the finding again, not here."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to update review %s", review_id):
        review = await _review_in_case(case_id, review_id)
        if review.kind != "missed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a missed issue can be edited")
        _check_fits(review.scope, request.check_name, request.note)
        await asyncio.to_thread(
            findings_repository.update_missed, str(review_id),
            check_name=request.check_name, note=request.note, reviewer=user,
        )
        reviews = await _case_reviews(case_id)
    await events.record(officer_event(
        types.MISSED_ISSUE_EDITED, case_id=case_id, document_id=review.document_id,
        detail={
            "review_id": review.id, "scope": review.scope, "from_check": review.check_name,
            "check_name": request.check_name,
        },
    ))
    return reviews


@router.delete("/cases/{case_id}/finding-reviews/{review_id}", response_model=CaseFindingReviews)
async def delete_finding_review(
    case_id: str, review_id: UUID, user: CurrentUser, case: CaseRecord = Depends(_editable_reviewed_case),
) -> CaseFindingReviews:
    """Removes a missed issue (e.g. one reported by mistake), or clears a verdict; the finding itself stays."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to delete review %s", review_id):
        review = await _review_in_case(case_id, review_id)
        if review.kind == "missed":
            await asyncio.to_thread(findings_repository.delete_missed, str(review_id))
        else:
            await asyncio.to_thread(findings_repository.set_verdict, str(review_id), None, user)
        reviews = await _case_reviews(case_id)
    await events.record(officer_event(
        types.FINDING_REVIEW_REMOVED, case_id=case_id, document_id=review.document_id,
        detail={
            "review_id": review.id, "scope": review.scope, "kind": review.kind, "check_name": review.check_name,
            "verdict": review.verdict,
        },
    ))
    if review.kind != "missed":
        await _record_saved(case_id, [])
    return reviews


@router.get("/detection-metrics", response_model=DetectionMetrics)
async def get_detection_metrics(
    since: date | None = None,
    status_filter: list[CaseStage] = Query(default=[], alias="status"),
) -> DetectionMetrics:
    """
    Precision and recall per check across every reviewed case, with targets (the dashboard). Optionally only
    cases created on or after `since` and at these stages, as the dashboard's filters pick.
    """
    reviews = await to_thread_or_502(
        findings_repository.list_reviews, since, [stage.value for stage in status_filter],
        detail="Failed to load the finding reviews. Check Supabase table access.",
        log=("Failed to list finding reviews",),
    )
    return detection_metrics.overall_metrics(reviews)
