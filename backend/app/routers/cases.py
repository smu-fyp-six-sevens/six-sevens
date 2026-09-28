from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, HTTPException, status

from .. import case_repository, case_status, document_repository
from ..case_match import CaseMatchResult, check_case_match
from ..models import CaseRecord, CaseSummary, UpdateCaseRequest

router = APIRouter(prefix="/cases", tags=["cases"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CaseRecord, status_code=status.HTTP_201_CREATED)
async def create_case(company_name: str | None = Body(None, embed=True)) -> CaseRecord:
    try:
        return await asyncio.to_thread(case_repository.create_case, company_name)
    except Exception as exc:
        logger.exception("Failed to create case")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create the case. Check Supabase table access.",
        ) from exc


@router.get("", response_model=list[CaseSummary])
async def list_cases() -> list[CaseSummary]:
    try:
        return await asyncio.to_thread(case_status.list_case_summaries)
    except Exception as exc:
        logger.exception("Failed to list cases")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to list cases. Check Supabase table access.",
        ) from exc


@router.get("/{case_id}", response_model=CaseSummary)
async def get_case(case_id: str) -> CaseSummary:
    try:
        summary = await asyncio.to_thread(case_status.get_case_summary, case_id)
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
async def update_case(case_id: str, request: UpdateCaseRequest) -> CaseSummary:
    try:
        existing = await asyncio.to_thread(case_repository.get_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    try:
        await asyncio.to_thread(
            case_repository.update_case, case_id, request.company_name
        )
    except Exception as exc:
        logger.exception("Failed to update case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to save the case. Check Supabase table access.",
        ) from exc

    updated = await asyncio.to_thread(case_status.get_case_summary, case_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return updated


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case_id: str) -> None:
    """
    Removes a case from the active queue, along with every document that
    belongs to it. A following GET /cases or GET /cases/{id} no longer
    returns it.
    """
    try:
        existing = await asyncio.to_thread(case_repository.get_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    try:
        documents = await asyncio.to_thread(document_repository.list_documents, case_id)
        for document in documents:
            await asyncio.to_thread(document_repository.remove_document, document)
        await asyncio.to_thread(case_repository.delete_case, case_id)
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
        case = await asyncio.to_thread(case_repository.get_case, case_id)
        documents = (
            await asyncio.to_thread(document_repository.list_documents, case_id)
            if case is not None
            else []
        )
    except Exception as exc:
        logger.exception("Failed to fetch case %s for case-match", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return check_case_match(case, documents)
