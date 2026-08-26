from __future__ import annotations

import random
import re
from copy import deepcopy

from fastapi import HTTPException, status

from .injectors import get_injector
from .schemas import (
    DefectType,
    InjectDefectRequest,
    InjectDefectResponse,
    InjectedDefect,
    InjectableDocument,
)
from .store import get_document, save_document

CHANGED_KEYS: dict[DefectType, set[str]] = {
    DefectType.FONT: {"font"},
    DefectType.FONT_SIZE: {"font_size"},
    DefectType.NUMERICAL: {"text"},
    DefectType.METADATA: {"metadata"},
    DefectType.SPELLING: {"text"},
    DefectType.MISALIGNMENT: {"x", "y"},
    DefectType.EMPHASIS: {"style"},
}


class InjectionService:
    def inject_defect(self, request: InjectDefectRequest) -> InjectDefectResponse:
        document = get_document(request.document_id)
        if document is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{request.document_id}' not found",
            )

        field_name = request.field_name
        if field_name is None:
            try:
                field_name = _pick_random_field(document, request.defect_type)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        elif field_name not in document.fields:
            available = ", ".join(sorted(document.fields))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Field '{field_name}' not found on document '{request.document_id}'. "
                    f"Available fields: {available}"
                ),
            )

        working_copy = deepcopy(document)
        try:
            injected_defect = get_injector(request.defect_type).inject(
                working_copy, field_name
            )
        except ValueError as exc:
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
        )


def _pick_random_field(document: InjectableDocument, defect_type: DefectType) -> str:
    """Choose a random suitable text span for the requested defect type."""
    if not document.fields:
        raise ValueError(f"Document '{document.id}' has no injectable fields")

    candidates: list[str]
    if defect_type == DefectType.NUMERICAL:
        candidates = [
            name
            for name, field in document.fields.items()
            if re.search(r"\d+(?:\.\d+)?", field.text)
        ]
        if not candidates:
            raise ValueError(
                f"Document '{document.id}' has no numeric text to inject a numerical defect into"
            )
    elif defect_type == DefectType.SPELLING:
        candidates = [
            name
            for name, field in document.fields.items()
            if re.search(r"[A-Za-z]", field.text) and len(field.text.strip()) > 1
        ]
        if not candidates:
            raise ValueError(
                f"Document '{document.id}' has no word-like text to inject a spelling defect into"
            )
    else:
        # font / font_size / metadata / misalignment / emphasis can target any text span
        candidates = list(document.fields.keys())

    return random.choice(candidates)


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
