"""
Shared FastAPI dependencies for the routers that fetch a document or case by
id — collapses the repeated "fetch -> 502 on error -> 404 if missing" block
that used to be copy-pasted at the top of most handlers in documents.py,
cases.py and detection.py. FastAPI matches these by parameter name, so any
route with a {document_id}/{case_id} path parameter can just declare
`document: DocumentRecord = Depends(get_document_or_404)` (or the case
equivalent) instead of repeating the fetch.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException, status

from .. import case_status, document_repository
from ..models import CaseRecord, DocumentRecord

logger = logging.getLogger(__name__)


async def get_document_or_404(document_id: str) -> DocumentRecord:
    try:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


async def get_case_or_404(case_id: str) -> CaseRecord:
    try:
        case = await asyncio.to_thread(case_status.get_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case
