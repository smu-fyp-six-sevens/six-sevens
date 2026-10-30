"""
Loan officers' reviews of detection findings, and the precision and recall per check they give.

On the case page an officer marks each finding correct or wrong, per document and for the cross-document
findings, and reports issues detection missed against the check that should have caught them. Every change
returns the case's reviews and metrics afresh, so the page never computes them itself.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import app_settings_repository, case_service, detection_metrics, document_repository, events, finding_reviews_repository
from ..auth import CurrentUser
from ..events import officer_event, types
from ..models import (
    CaseDocumentsStatus, CaseFindingReviews, CaseRecord, DetectionMetrics, ReportMissedIssueRequest,
    SetFindingVerdictRequest, SetFindingVerdictsRequest, UpdateMissedIssueRequest,
)
from ._dependencies import bad_gateway_on_error, get_case_or_404, to_thread_or_502



async def _require_reviews_on() -> None:
    """Every route here, reads included: with finding reviews turned off (routers/settings.py), nobody marks
    findings or sees the precision and recall they give."""
    if not await asyncio.to_thread(app_settings_repository.get_finding_reviews_enabled):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Finding reviews are turned off by an admin.")


router = APIRouter(tags=["finding-reviews"], dependencies=[Depends(_require_reviews_on)])
logger = logging.getLogger(__name__)

_SAVE_FAILED = "Failed to save the review. Check Supabase table access."


async def _case_reviews(case_id: str) -> CaseFindingReviews:
    reviews = await asyncio.to_thread(finding_reviews_repository.list_for_case, case_id)
    return detection_metrics.case_metrics(reviews)


async def _check_target(case_id: str, scope: str, document_id: UUID | None, check_name: str) -> str | None:
    """The document id as a string, after checking the document is in this case and the check fits the scope."""
    if check_name not in detection_metrics.checks_by_scope()[scope]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"That check can't be reviewed on a {scope}.")
    if document_id is None:
        return None
    document = await asyncio.to_thread(document_repository.get_document, str(document_id), include_fields=False)
    if document is None or document.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found in this case")
    return str(document_id)


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


@router.get("/cases/{case_id}/finding-reviews", response_model=CaseFindingReviews)
async def get_case_finding_reviews(case_id: str, case: CaseRecord = Depends(get_case_or_404)) -> CaseFindingReviews:
    """Every review in the case, with precision and recall per document, across documents and for the case."""
    with bad_gateway_on_error(
        "Failed to load the case's reviews. Check Supabase table access.", "Failed to list reviews for case %s", case_id
    ):
        return await _case_reviews(case_id)


@router.put("/cases/{case_id}/finding-reviews/verdict", response_model=CaseFindingReviews)
async def set_finding_verdict(
    case_id: str, request: SetFindingVerdictRequest, user: CurrentUser, case: CaseRecord = Depends(get_case_or_404),
) -> CaseFindingReviews:
    """Marks one finding correct or wrong, signed by the officer; a null verdict clears the mark. Any officer
    can change a verdict; the latest one stands."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save a verdict on case %s", case_id):
        document_id = await _check_target(case_id, request.scope, request.document_id, request.check_name)
        if request.verdict is None:
            await asyncio.to_thread(finding_reviews_repository.clear_verdict, case_id, request.finding_id)
        else:
            await asyncio.to_thread(
                finding_reviews_repository.set_verdict,
                case_id=case_id, scope=request.scope, document_id=document_id, finding_id=request.finding_id,
                check_name=request.check_name, check_code=request.check_code, summary=request.summary,
                verdict=request.verdict, reviewer=user,
            )
        reviews = await _case_reviews(case_id)
    await _record_saved(case_id, [(request.scope, document_id, request.verdict)])
    return reviews


@router.put("/cases/{case_id}/finding-reviews/verdicts", response_model=CaseFindingReviews)
async def set_finding_verdicts(
    case_id: str, request: SetFindingVerdictsRequest, user: CurrentUser, case: CaseRecord = Depends(get_case_or_404),
) -> CaseFindingReviews:
    """Saves one group's review in one request: the officer ticked the findings that are real (correct) and left
    the rest (wrong). Every target is checked before anything is written, so a bad entry saves nothing."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save verdicts on case %s", case_id):
        documents = {
            (item.scope, item.document_id): await _check_target(case_id, item.scope, item.document_id, item.check_name)
            for item in request.verdicts
        }
        for item in request.verdicts:
            if item.verdict is None:
                await asyncio.to_thread(finding_reviews_repository.clear_verdict, case_id, item.finding_id)
                continue
            await asyncio.to_thread(
                finding_reviews_repository.set_verdict,
                case_id=case_id, scope=item.scope, document_id=documents[(item.scope, item.document_id)],
                finding_id=item.finding_id, check_name=item.check_name, check_code=item.check_code,
                summary=item.summary, verdict=item.verdict, reviewer=user,
            )
        reviews = await _case_reviews(case_id)
    await _record_saved(
        case_id, [(item.scope, documents[(item.scope, item.document_id)], item.verdict) for item in request.verdicts],
    )
    return reviews


@router.post(
    "/cases/{case_id}/finding-reviews/missed", response_model=CaseFindingReviews, status_code=status.HTTP_201_CREATED,
)
async def report_missed_issue(
    case_id: str, request: ReportMissedIssueRequest, user: CurrentUser, case: CaseRecord = Depends(get_case_or_404),
) -> CaseFindingReviews:
    """Records an issue no finding raised, against the check that should have caught it: a false negative."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to save a missed issue on case %s", case_id):
        document_id = await _check_target(case_id, request.scope, request.document_id, request.check_name)
        await asyncio.to_thread(
            finding_reviews_repository.add_missed,
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
    case: CaseRecord = Depends(get_case_or_404),
) -> CaseFindingReviews:
    """Corrects a reported missed issue (its check or note), e.g. one filed against the wrong check. A verdict
    on a finding is changed by reviewing the finding again, not here."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to update review %s", review_id):
        review = await asyncio.to_thread(finding_reviews_repository.get, str(review_id))
        if review is None or review.case_id != case_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found in this case")
        if review.kind != "missed":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only a missed issue can be edited")
        await _check_target(case_id, review.scope, None, request.check_name)
        await asyncio.to_thread(
            finding_reviews_repository.update_missed, str(review_id),
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
    case_id: str, review_id: UUID, case: CaseRecord = Depends(get_case_or_404),
) -> CaseFindingReviews:
    """Removes a verdict or a missed issue (e.g. one reported by mistake)."""
    with bad_gateway_on_error(_SAVE_FAILED, "Failed to delete review %s", review_id):
        review = await asyncio.to_thread(finding_reviews_repository.get, str(review_id))
        if review is None or review.case_id != case_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review not found in this case")
        await asyncio.to_thread(finding_reviews_repository.delete, str(review_id))
        reviews = await _case_reviews(case_id)
    await events.record(officer_event(
        types.FINDING_REVIEW_REMOVED, case_id=case_id, document_id=review.document_id,
        detail={
            "review_id": review.id, "scope": review.scope, "kind": review.kind, "check_name": review.check_name,
            "verdict": review.verdict,
        },
    ))
    return reviews


@router.get("/detection-metrics", response_model=DetectionMetrics)
async def get_detection_metrics(
    since: date | None = None,
    status_filter: list[CaseDocumentsStatus] = Query(default=[], alias="status"),
) -> DetectionMetrics:
    """
    Precision and recall per check across every reviewed case, with targets (the dashboard). Optionally only
    cases created on or after `since` and in these documents statuses, as the dashboard's filters pick.
    """
    reviews = await to_thread_or_502(
        finding_reviews_repository.list_all,
        detail="Failed to load the finding reviews. Check Supabase table access.",
        log=("Failed to list finding reviews",),
    )
    if since is not None or status_filter:
        wanted = await to_thread_or_502(
            case_service.case_ids_matching, since, status_filter,
            detail="Failed to filter cases. Check Supabase table access.",
            log=("Failed to filter cases for detection metrics",),
        )
        reviews = [review for review in reviews if review.case_id in wanted]
    return detection_metrics.overall_metrics(reviews)
