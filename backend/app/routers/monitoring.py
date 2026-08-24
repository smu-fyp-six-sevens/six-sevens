from __future__ import annotations

from fastapi import APIRouter

from ..models import StuckDocument
from ..monitoring import get_stuck_documents

router = APIRouter(prefix="/documents", tags=["monitoring"])


@router.get("/monitoring/stuck", response_model=list[StuckDocument])
def list_stuck_documents() -> list[StuckDocument]:
    """AC4: surfaces documents stuck in queued/processing past the threshold."""
    return get_stuck_documents()