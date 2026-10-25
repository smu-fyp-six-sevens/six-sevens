"""
Loan officers' reviews of detection findings, and the precision and recall per check they give.

On the case page an officer marks each finding correct or wrong, per document and for the cross-document
findings, and reports issues detection missed against the check that should have caught them. Every change
returns the case's reviews and metrics afresh, so the page never computes them itself.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from .. import detection_metrics, document_repository, finding_reviews_repository
from ..auth import CurrentUser
from ..models import (
    CaseFindingReviews, CaseRecord, DetectionMetrics, ReportMissedIssueRequest, SetFindingVerdictRequest,
    SetFindingVerdictsRequest,
)
from ._dependencies import bad_gateway_on_error, get_case_or_404, to_thread_or_502

router = APIRouter(tags=["finding-reviews"])
logger = logging.getLogger(__name__)

_SAVE_FAILED = "Failed to save the review. Check Supabase table access."


async def _case_reviews(case_id: str) -> CaseFindingReviews:
    reviews = await asyncio.to_thread(finding_reviews_repository.list_for_case, case_id)
    return detection_metrics.case_metrics(reviews)


async def _check_target(case_id: str, scope: str, document_id: UUID | None, check_name: str) -> str | None:
    """The document id as a string, after checking the document is in this case and the check fits the scope."""
    if check_name not in detection_metrics.checks_by_scope()[scope]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"'{check_name}' is not a {scope} check")
    if document_id is None:
        return None
    document = await asyncio.to_thread(document_repository.get_document, str(document_id), include_fields=False)
    if document is None or document.case_id != case_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found in this case")
    return str(document_id)


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
        return await _case_reviews(case_id)


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
        return await _case_reviews(case_id)


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
        return await _case_reviews(case_id)


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
        return await _case_reviews(case_id)


@router.get("/detection-metrics", response_model=DetectionMetrics)
async def get_detection_metrics() -> DetectionMetrics:
    """Precision and recall per check across every reviewed case, with targets (the dashboard)."""
    reviews = await to_thread_or_502(
        finding_reviews_repository.list_all,
        detail="Failed to load the finding reviews. Check Supabase table access.",
        log=("Failed to list finding reviews",),
    )
    return detection_metrics.overall_metrics(reviews)
