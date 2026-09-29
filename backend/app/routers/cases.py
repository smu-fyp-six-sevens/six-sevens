from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, status

from .. import case_service, document_repository
from ._dependencies import get_case_or_404
from ..case_match import CaseMatchResult
from ..models import IN_FLIGHT_STATUSES, CaseRecord, CaseSummary, UpdateCaseRequest

router = APIRouter(prefix="/cases", tags=["cases"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CaseRecord, status_code=status.HTTP_201_CREATED)
async def create_case(company_name: str | None = Body(None, embed=True)) -> CaseRecord:
    try:
        return await asyncio.to_thread(case_service.create_case, company_name)
    except Exception as exc:
        logger.exception("Failed to create case")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create the case. Check Supabase table access.",
        ) from exc


@router.get("", response_model=list[CaseSummary])
async def list_cases() -> list[CaseSummary]:
    try:
        return await asyncio.to_thread(case_service.list_case_summaries)
    except Exception as exc:
        logger.exception("Failed to list cases")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list cases. Check Supabase table access.",
        ) from exc


@router.get("/{case_id}", response_model=CaseSummary)
async def get_case(case_id: str) -> CaseSummary:
    try:
        summary = await asyncio.to_thread(case_service.get_case_summary, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return summary


@router.patch("/{case_id}", response_model=CaseSummary)
async def update_case(
    case_id: str, request: UpdateCaseRequest, case: CaseRecord = Depends(get_case_or_404)
) -> CaseSummary:
    try:
        await asyncio.to_thread(
            case_service.update_case, case_id, request.company_name
        )
    except Exception as exc:
        logger.exception("Failed to update case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save the case. Check Supabase table access.",
        ) from exc

    updated = await asyncio.to_thread(case_service.get_case_summary, case_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return updated


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: str, case: CaseRecord = Depends(get_case_or_404)) -> None:
    """
    Removes a case from the active queue, along with every document that
    belongs to it. A following GET /cases or GET /cases/{id} no longer
    returns it.

    Only allowed once every one of the case's documents has reached a
    terminal state (ANALYZED, FAILED, REJECTED, CANCELLED, APPROVED) — same
    reasoning as delete_document: deleting out from under a document a
    worker is still actively writing to (status, extracted fields) would
    race the pipeline and orphan a document row mid-flight. Cancel the
    in-flight document(s) first, or wait for them to finish.
    """
    try:
        documents = await asyncio.to_thread(document_repository.list_documents, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch documents for case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case's documents. Check Supabase table access.",
        ) from exc

    if any(document.status in IN_FLIGHT_STATUSES for document in documents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Case has a document that's still being processed; cancel it "
                "first, or wait for it to finish, before deleting the case."
            ),
        )

    try:
        await asyncio.to_thread(case_service.delete_case, case_id)
    except Exception as exc:
        logger.exception("Failed to delete case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to delete the case. Check Supabase table access.",
        ) from exc


@router.get("/{case_id}/case-match", response_model=CaseMatchResult)
async def get_case_match(case_id: str) -> CaseMatchResult:
    """
    Whether each of the case's documents belongs to the case's company and
    individuals, checked against the certificate of incumbency — see
    case_match.py. Computed live on every call; nothing is written.
    """
    try:
        result = await asyncio.to_thread(case_service.get_case_match_for_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s for case-match", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return result
