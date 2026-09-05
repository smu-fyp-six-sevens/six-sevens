from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi import HTTPException, status

from .injectors import get_injector
from .schemas import (
    DefectType,
    InjectDefectRequest,
    InjectDefectResponse,
    InjectedDefect,
)
from .store import (
    get_template_document,
    list_documents,
    save_document,
    stage_generated_document,
)

CHANGED_KEYS: dict[DefectType, set[str]] = {
    DefectType.FONT: {"font"},
    DefectType.FONT_SIZE: {"font_size"},
    DefectType.NUMERICAL: {"text"},
    DefectType.METADATA: {"metadata"},
    DefectType.SPELLING: {"text"},
    DefectType.MISALIGNMENT: {"x", "y"},
    DefectType.EMPHASIS: {"style"},
    DefectType.TEXT_OVERLAY: {"overlay"},
    DefectType.IMAGE_MANIPULATION: {"manipulation"},
}


class InjectionService:
    def inject_defect(self, request: InjectDefectRequest) -> InjectDefectResponse:
        document = get_template_document(request.document_id)
        if document is None:
            available = ", ".join(sorted(doc.id for doc in list_documents())) or "none"
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Document '{request.document_id}' not found. "
                    f"Use a template id such as Bank_Statement-1. Available: {available}"
                ),
            )

        field_name = request.field_name
        if field_name not in document.fields:
            available = ", ".join(sorted(document.fields))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Field '{field_name}' not found on document '{request.document_id}'. "
                    f"Available fields: {available}"
                ),
            )
        if (
            document.fields[field_name].kind == "image"
            and request.defect_type
            not in {DefectType.IMAGE_MANIPULATION, DefectType.METADATA}
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Field '{field_name}' is an embedded PDF image. "
                    "Use defect_type 'image_manipulation' for image fields."
                ),
            )

        working_copy = deepcopy(document)
        try:
            generated = stage_generated_document(working_copy, request.defect_type.value)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        try:
            injected_defect = get_injector(request.defect_type).inject(
                working_copy, field_name, overrides=request.overrides()
            )
        except ValueError as exc:
            Path(generated).unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        _assert_single_defect(injected_defect, request.defect_type)
        working_copy.defects.append(injected_defect)
        saved = save_document(working_copy)
        return InjectDefectResponse(
            document_id=saved.id,
            document_type=saved.document_type,
            filename=saved.filename,
            defect_type=injected_defect.defect_type,
            field_name=injected_defect.field_name,
            description=injected_defect.description,
            before_text=str(injected_defect.before.get("text", "")),
            after_text=str(injected_defect.after.get("text", "")),
            defect_count=len(saved.defects),
            generated_filename=generated.name,
        )


def _assert_single_defect(injected_defect: InjectedDefect, defect_type: DefectType) -> None:
    if injected_defect.defect_type != defect_type:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Injector returned unexpected defect type",
        )

    changed_keys = {
        key
        for key in injected_defect.before
        if injected_defect.before[key] != injected_defect.after[key]
    }
    expected = CHANGED_KEYS[defect_type]
    if not changed_keys or not changed_keys.issubset(expected):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                f"Expected changes within {sorted(expected)}, "
                f"but got {sorted(changed_keys)}"
            ),
        )
