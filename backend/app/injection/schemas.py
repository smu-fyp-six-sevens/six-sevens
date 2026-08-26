from enum import Enum

from pydantic import BaseModel, Field


class DefectType(str, Enum):
    FONT = "font"
    FONT_SIZE = "font_size"
    NUMERICAL = "numerical"
    METADATA = "metadata"
    SPELLING = "spelling"
    MISALIGNMENT = "misalignment"
    EMPHASIS = "emphasis"


class FieldValue(BaseModel):
    text: str
    font: str = "helv"
    font_size: float = 12.0
    metadata: dict[str, str] = Field(default_factory=dict)
    x: float = 0.0
    y: float = 0.0
    page: int = 0
    rect: list[float] = Field(default_factory=list)
    style: str = Field(
        default="",
        description="Text emphasis: '', 'bold', 'italic', or 'underline'",
    )


class InjectedDefect(BaseModel):
    defect_type: DefectType
    field_name: str
    description: str
    before: dict
    after: dict


class InjectableDocument(BaseModel):
    """Any PDF or image registered for fraud injection."""

    id: str
    document_type: str
    filename: str = Field(description="Working copy filename, preserving original extension")
    fields: dict[str, FieldValue]
    defects: list[InjectedDefect] = Field(default_factory=list)
    field_source: str = Field(
        default="pdf_text",
        description="How fields were recognised: 'pdf_text' or 'ocr'",
    )

    @property
    def pdf_filename(self) -> str:
        """Alias kept for older callers."""
        return self.filename


# Backwards-compatible alias used by older imports/tests
SampleDocument = InjectableDocument


class DocumentFieldInfo(BaseModel):
    """A recognised injectable field (PDF text span or OCR text on an image)."""

    field_name: str
    text: str
    page: int = 0
    source: str = Field(
        description="How the field was recognised: 'pdf_text' or 'ocr'",
    )


class InjectDefectRequest(BaseModel):
    document_id: str
    defect_type: DefectType
    field_name: str | None = Field(
        default=None,
        description="Optional. If omitted, a suitable word/number in the document is chosen at random.",
    )


class InjectDefectResponse(BaseModel):
    document_id: str
    document_type: str
    filename: str
    defect_type: DefectType
    field_name: str
    description: str
    before_text: str
    after_text: str
    defect_count: int = Field(
        description="Total defects injected into this working document so far"
    )
