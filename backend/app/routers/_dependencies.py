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
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from fastapi import HTTPException, status

from .. import case_service, document_repository
from ..models import CaseRecord, DocumentRecord

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def to_thread_or_502(fn: Callable[..., T], *args: Any, detail: str, log: tuple, **kwargs: Any) -> T:
    """Runs a blocking call off the event loop; on any error logs `log` (format, *args) and raises a 502."""
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except Exception as exc:
        logger.exception(*log)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


@contextmanager
def bad_gateway_on_error(detail: str, *log: Any) -> Iterator[None]:
    """For a block that does more than one blocking call: any error inside logs `log` (format, *args) and
    becomes a 502. An HTTPException raised inside (a 404, a 409) passes through unchanged."""
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(*log)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


async def _load_document(document_id: str, include_fields: bool, include_metadata: bool = False) -> DocumentRecord:
    try:
        document = await asyncio.to_thread(
            document_repository.get_document, document_id, include_fields=include_fields, include_metadata=include_metadata
        )
    except Exception as exc:
        logger.exception("Failed to fetch document %s", document_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the document. Check Supabase table access.",
        ) from exc

    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return document


async def get_document_or_404(document_id: str) -> DocumentRecord:
    """
    The document row only: most handlers read the fields themselves, in the shape they need
    (with ids and confidences), so loading them here too was a second, unused read.
    """
    return await _load_document(document_id, include_fields=False)


async def get_document_with_fields_or_404(document_id: str) -> DocumentRecord:
    """For the handlers that read document.extracted_fields (a plain {name: value} dict)."""
    return await _load_document(document_id, include_fields=True)


async def get_document_with_metadata_or_404(document_id: str) -> DocumentRecord:
    """For the one handler that reads document.metadata (the exiftool blob)."""
    return await _load_document(document_id, include_fields=False, include_metadata=True)


async def get_case_or_404(case_id: str) -> CaseRecord:
    try:
        case = await asyncio.to_thread(case_service.get_case, case_id)
    except Exception as exc:
        logger.exception("Failed to fetch case %s", case_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch the case. Check Supabase table access.",
        ) from exc

    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
    return case
