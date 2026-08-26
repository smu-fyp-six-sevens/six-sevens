from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status

from .. import case_repository
from ..models import CaseRecord

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", response_model=CaseRecord, status_code=status.HTTP_201_CREATED)
async def create_case() -> CaseRecord:
    return await asyncio.to_thread(case_repository.create_case)


@router.get("/{case_id}", response_model=CaseRecord)
async def get_case(case_id: str) -> CaseRecord:
    case = await asyncio.to_thread(case_repository.get_case, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case
