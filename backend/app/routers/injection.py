from fastapi import APIRouter, HTTPException, status

from ..injection.schemas import (
    DocumentFieldInfo,
    InjectDefectRequest,
    InjectDefectResponse,
)
from ..injection.service import InjectionService
from ..injection.store import get_document

router = APIRouter(prefix="/api/dev", tags=["dev-test-dataset"])
injection_service = InjectionService()


@router.get("/documents/{document_id}/fields", response_model=list[DocumentFieldInfo])
def get_document_fields(document_id: str) -> list[DocumentFieldInfo]:
    """List recognised field names and text for a PDF or image template."""
    document = get_document(document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return [
        DocumentFieldInfo(
            field_name=name,
            text=field.text,
            page=field.page,
            source=document.field_source,
        )
        for name, field in sorted(document.fields.items())
    ]


@router.post("/inject", response_model=InjectDefectResponse)
def inject_defect(request: InjectDefectRequest) -> InjectDefectResponse:
    return injection_service.inject_defect(request)
