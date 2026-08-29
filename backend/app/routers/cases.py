from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import case_repository
from ..models import CaseRecord

router = APIRouter(prefix="/cases", tags=["cases"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CaseRecord, status_code=status.HTTP_201_CREATED)
async def create_case() -> CaseRecord:
    try:
        return await asyncio.to_thread(case_repository.create_case)
    except Exception as exc:
        logger.exception("Failed to create case")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to create the case. Check Supabase table access.",
        ) from exc


@router.get("/{case_id}", response_model=CaseRecord)
async def get_case(case_id: str) -> CaseRecord:
    try:
        case = await asyncio.to_thread(case_repository.get_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case
