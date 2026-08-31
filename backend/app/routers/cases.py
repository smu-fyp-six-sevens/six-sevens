from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, HTTPException, status

from .. import case_repository, case_status
from ..models import CaseRecord, CaseSummary

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
