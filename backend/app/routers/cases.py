from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from .. import case_service, document_repository, events
from ..case_service import CaseSort
from ..events import officer_event, types
from ._dependencies import get_case_or_404, to_thread_or_502
from ..case_match import CaseMatchResult
from ..case_pipeline import service as analysis_service
from ..case_pipeline.models import CaseAnalysis
from ..models import (
    IN_FLIGHT_STATUSES,
    MAX_COMPANY_NAME_LENGTH,
    CaseCounts,
    CaseDocumentsStatus,
    CaseListItem,
    CaseListPage,
    CaseRecord,
    CaseSearchResult,
    CaseSummary,
    UpdateCaseRequest,
)

router = APIRouter(prefix="/cases", tags=["cases"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CaseRecord, status_code=status.HTTP_201_CREATED)
async def create_case(company_name: str | None = Body(None, embed=True, max_length=MAX_COMPANY_NAME_LENGTH)) -> CaseRecord:
    case = await to_thread_or_502(
        case_service.create_case, company_name,
        detail="Failed to create the case. Check Supabase table access.",
        log=("Failed to create case",),
    )
    await events.record(officer_event(types.CASE_CREATED, case_id=case.id, detail={"company_name": company_name}))
    return case


@router.get("", response_model=CaseListPage)
async def list_cases(
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    sort: CaseSort = "newest",
    status_filter: list[CaseDocumentsStatus] = Query(default=[], alias="status"),
    q: str | None = Query(None, max_length=MAX_COMPANY_NAME_LENGTH),
) -> CaseListPage:
    """One page of the case queue: newest or oldest first, or highest risk first; optionally only these documents
    statuses and only cases whose number or name contains q."""
    return await to_thread_or_502(
        case_service.list_cases_page, limit=limit, offset=offset, sort=sort, statuses=status_filter, q=q,
        detail="Failed to list cases. Check Supabase table access.",
        log=("Failed to list cases",),
    )


# The fixed paths below come before /{case_id}, or "counts" would be read as a case id.
@router.get("/counts", response_model=CaseCounts)
async def case_counts() -> CaseCounts:
    return await to_thread_or_502(
        case_service.case_counts,
        detail="Failed to count cases. Check Supabase table access.",
        log=("Failed to count cases",),
    )


@router.get("/search", response_model=CaseSearchResult)
async def search_cases(
    q: str | None = Query(None, max_length=MAX_COMPANY_NAME_LENGTH),
    limit: int = Query(8, ge=1, le=50),
) -> CaseSearchResult:
    """Jump-to-case suggestions, best match first; without q, the newest cases."""
    return await to_thread_or_502(
        case_service.search_cases, q, limit,
        detail="Failed to search cases. Check Supabase table access.",
        log=("Failed to search cases",),
    )


@router.get("/by-number/{number}", response_model=CaseListItem)
async def get_case_by_number(number: str) -> CaseListItem:
    """The case behind a URL such as /cases/GLDB-137."""
    item = await to_thread_or_502(
        case_service.get_case_item, number,
        detail="Failed to fetch the case. Check Supabase table access.",
        log=("Failed to fetch case %s", number),
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return item


@router.get("/{case_id}", response_model=CaseSummary)
async def get_case(case_id: str) -> CaseSummary:
    summary = await to_thread_or_502(
        case_service.get_case_summary, case_id,
        detail="Failed to fetch the case. Check Supabase table access.",
        log=("Failed to fetch case %s", case_id),
    )

    if summary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return summary


@router.patch("/{case_id}", response_model=CaseSummary)
async def update_case(
    case_id: str, request: UpdateCaseRequest, case: CaseRecord = Depends(get_case_or_404)
) -> CaseSummary:
    await to_thread_or_502(
        case_service.update_case,
        case_id,
        request.company_name,
        detail="Failed to save the case. Check Supabase table access.",
        log=("Failed to update case %s", case_id),
    )
    await events.record(officer_event(
        types.CASE_RENAMED, case_id=case_id, detail={"from": case.company_name, "to": request.company_name},
    ))

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
    documents = await to_thread_or_502(
        document_repository.list_documents, case_id,
        detail="Failed to fetch the case's documents. Check Supabase table access.",
        log=("Failed to fetch documents for case %s", case_id),
    )

    if any(document.status in IN_FLIGHT_STATUSES for document in documents):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Case has a document that's still being processed; cancel it "
                "first, or wait for it to finish, before deleting the case."
            ),
        )

    await to_thread_or_502(
        case_service.delete_case, case_id,
        detail="Failed to delete the case. Check Supabase table access.",
        log=("Failed to delete case %s", case_id),
    )
    await events.record(officer_event(
        types.CASE_DELETED, case_id=case_id,
        detail={"company_name": case.company_name, "documents": len(documents)},
    ))


@router.get("/{case_id}/case-match", response_model=CaseMatchResult)
async def get_case_match(case_id: str) -> CaseMatchResult:
    """
    Whether each of the case's documents belongs to the case's company and
    individuals, checked against the certificate of incumbency — see
    case_match.py. Computed live on every call; nothing is written.
    """
    result = await to_thread_or_502(
        case_service.get_case_match_for_case, case_id,
        detail="Failed to fetch the case. Check Supabase table access.",
        log=("Failed to fetch case %s for case-match", case_id),
    )

    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return result


@router.post(
    "/{case_id}/analysis/run", response_model=CaseAnalysis, status_code=status.HTTP_202_ACCEPTED
)
async def run_case_analysis(case_id: str, case: CaseRecord = Depends(get_case_or_404)) -> CaseAnalysis:
    """
    Starts the per-case analysis (cross-document findings, risk outlook and
    summary) in the background. Only once every approved document has been
    scanned; poll GET .../analysis, or watch the case snapshot, for the result.
    """
    try:
        return await analysis_service.start_analysis(case_id)
    except analysis_service.AnalysisDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Case analysis is switched off."
        ) from exc
    except analysis_service.AnalysisNotReady as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Every approved document must finish scanning before the case can be analysed.",
        ) from exc
    except analysis_service.AnalysisAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="An analysis is already running for this case."
        ) from exc
    except Exception as exc:
        logger.exception("Failed to start the analysis for case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to start the analysis. Check Supabase table access.",
        ) from exc


@router.get("/{case_id}/analysis", response_model=CaseAnalysis)
async def get_case_analysis(case_id: str, case: CaseRecord = Depends(get_case_or_404)) -> CaseAnalysis:
    """The case's latest analysis, marked stale when its documents have changed since it ran."""
    analysis = await to_thread_or_502(
        analysis_service.get_current, case_id,
        detail="Failed to fetch the analysis. Check Supabase table access.",
        log=("Failed to fetch the analysis for case %s", case_id),
    )
    if analysis is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This case has not been analysed.")
    return analysis
