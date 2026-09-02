from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


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
    filename: str = Field(description="Original template filename, preserving extension")
    fields: dict[str, FieldValue]
    defects: list[InjectedDefect] = Field(default_factory=list)
    field_source: str = Field(
        default="pdf_text",
        description="How fields were recognised: 'pdf_text' or 'ocr'",
    )
    inject_path: str | None = Field(
        default=None,
        exclude=True,
        description="Path of the generated file currently being written.",
    )


class DocumentFieldInfo(BaseModel):
    """A recognised injectable field (PDF text span or OCR text on an image)."""

    field_name: str
    text: str
    page: int = 0
    source: str = Field(
        description="How the field was recognised: 'pdf_text' or 'ocr'",
    )


class InjectOverrides(BaseModel):
    """Per-request replacement; omitted fields keep the injector's random behaviour."""

    new_text: str | None = None
    font: str | None = None
    font_size: float | None = None
    x: float | None = None
    y: float | None = None
    style: str | None = None
    metadata: dict[str, str] | None = None


class InjectDefectRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "document_id": "Bank_Statement-1",
                    "defect_type": "spelling",
                    "field_name": "tan_wei_ming",
                    "new_text": "TAN WEI NING",
                },
                {
                    "document_id": "Bank_Statement-1",
                    "defect_type": "numerical",
                    "field_name": "3_250_00",
                    "new_text": "4,250.00",
                },
                {
                    "document_id": "Bank_Statement-1",
                    "defect_type": "font",
                    "field_name": "atm_withdrawal",
                    "font": "cour",
                },
            ]
        }
    )
    document_id: str
    defect_type: DefectType
    field_name: str | None = Field(
        default=None,
        description="Optional. If omitted, a suitable word/number in the document is chosen at random.",
    )
    new_text: str | None = Field(
        default=None,
        description="Optional replacement text for spelling/numerical. Label prefixes are kept if you pass only the value.",
    )
    font: str | None = Field(
        default=None,
        description="Optional typeface for font defects (e.g. 'cour', 'tiro', 'arial', 'helv').",
    )
    font_size: float | None = Field(
        default=None,
        description="Optional point size for font_size defects.",
    )
    x: float | None = Field(
        default=None,
        description="Optional absolute x for misalignment defects.",
    )
    y: float | None = Field(
        default=None,
        description="Optional absolute y for misalignment defects.",
    )
    style: str | None = Field(
        default=None,
        description="Optional emphasis: 'bold', 'italic', or 'underline'.",
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Optional metadata keys to set (author, creator, title, subject).",
    )

    def overrides(self) -> InjectOverrides:
        return InjectOverrides(
            new_text=self.new_text,
            font=self.font,
            font_size=self.font_size,
            x=self.x,
            y=self.y,
            style=self.style,
            metadata=self.metadata,
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
    generated_filename: str | None = Field(
        default=None,
        description="Output file in documents_generated/, safe to delete after you copy it out.",
    )
