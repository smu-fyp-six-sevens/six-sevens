from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DefectType(str, Enum):
    FONT = "font"
    FONT_SIZE = "font_size"
    NUMERICAL = "numerical"
    METADATA = "metadata"
    SPELLING = "spelling"
    MISALIGNMENT = "misalignment"
    EMPHASIS = "emphasis"
    TEXT_OVERLAY = "text_overlay"
    IMAGE_MANIPULATION = "image_manipulation"


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
    overlay: str = Field(
        default="",
        description="Extra text drawn over the field for a text_overlay defect.",
    )
    manipulation: str = Field(
        default="",
        description="Image-manipulation indicator applied to this field's region.",
    )
    kind: str = Field(
        default="text",
        description=(
            "How this field was found: 'text' (PDF span / OCR) or 'image' "
            "(embedded PDF image, or a pictorial region on PNG/JPEG)."
        ),
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
    """A recognised injectable field (PDF text span, OCR text, or embedded image)."""

    field_name: str
    text: str
    page: int = 0
    source: str = Field(
        description="How the field was recognised: 'pdf_text' or 'ocr'",
    )
    kind: str = Field(
        default="text",
        description=(
            "'text' or 'image'. image_manipulation targets embedded PDF images "
            "or pictorial regions on PNG/JPEG."
        ),
    )


class InjectOverrides(BaseModel):
    """Explicit replacement for one inject. Every defect type requires its control."""

    new_text: str | None = None
    font: str | None = None
    font_size: float | None = None
    x: float | None = None
    y: float | None = None
    style: str | None = None
    metadata: dict[str, str] | None = None
    indicator: str | None = None
    scale_x: float | None = None
    scale_y: float | None = None


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
                {
                    "document_id": "C001_CertIncumbency",
                    "defect_type": "text_overlay",
                    "field_name": "golden_harvest_trading_pte_ltd",
                    "new_text": "CERT COPY",
                },
                {
                    "document_id": "Bank_Statement",
                    "defect_type": "image_manipulation",
                    "field_name": "image_0_50_35",
                    "indicator": "compression_mismatch",
                },
                {
                    "document_id": "NRIC_Sample",
                    "defect_type": "image_manipulation",
                    "field_name": "image_0_456_124",
                    "indicator": "scaling_anomaly",
                    "scale_x": 1.6,
                    "scale_y": 0.55,
                },
            ]
        }
    )
    document_id: str
    defect_type: DefectType
    field_name: str = Field(
        min_length=1,
        description="Field to inject into. Required.",
    )
    new_text: str | None = Field(
        default=None,
        description="Required for spelling, numerical, and text_overlay. Label prefixes are kept if you pass only the value.",
    )
    font: str | None = Field(
        default=None,
        description="Required for font defects (e.g. 'cour', 'tiro', 'arial', 'helv').",
    )
    font_size: float | None = Field(
        default=None,
        description="Required for font_size defects.",
    )
    x: float | None = Field(
        default=None,
        description="Required for misalignment (with y) — absolute x.",
    )
    y: float | None = Field(
        default=None,
        description="Required for misalignment (with x) — absolute y.",
    )
    style: str | None = Field(
        default=None,
        description="Required for emphasis: 'bold', 'italic', or 'underline'.",
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Required for metadata defects (author, creator, title, subject).",
    )
    indicator: str | None = Field(
        default=None,
        description=(
            "Required for image_manipulation: resolution_mismatch, "
            "compression_mismatch, visible_boundary, scaling_anomaly, "
            "image_overlay, quality_mismatch."
        ),
    )
    scale_x: float | None = Field(
        default=None,
        description=(
            "Optional scaling_anomaly horizontal factor. Values >1 spread wider. "
            "Default 1.85."
        ),
    )
    scale_y: float | None = Field(
        default=None,
        description=(
            "Optional scaling_anomaly vertical factor. Values <1 compress shorter. "
            "Default 0.45."
        ),
    )

    @model_validator(mode="after")
    def require_explicit_controls(self) -> "InjectDefectRequest":
        if not self.field_name.strip():
            raise ValueError("field_name is required")
        required: dict[DefectType, tuple[str, ...]] = {
            DefectType.FONT: ("font",),
            DefectType.FONT_SIZE: ("font_size",),
            DefectType.NUMERICAL: ("new_text",),
            DefectType.SPELLING: ("new_text",),
            DefectType.TEXT_OVERLAY: ("new_text",),
            DefectType.METADATA: ("metadata",),
            DefectType.EMPHASIS: ("style",),
            DefectType.IMAGE_MANIPULATION: ("indicator",),
        }
        if self.defect_type == DefectType.MISALIGNMENT:
            if self.x is None and self.y is None:
                raise ValueError("misalignment requires x and/or y")
            return self
        names = required.get(self.defect_type, ())
        for name in names:
            value = getattr(self, name)
            if value is None:
                raise ValueError(f"{self.defect_type.value} requires {name}")
            if isinstance(value, str) and not value.strip():
                raise ValueError(f"{self.defect_type.value} requires {name}")
            if name == "metadata" and not value:
                raise ValueError("metadata requires at least one key")
        return self

    def overrides(self) -> InjectOverrides:
        return InjectOverrides(
            new_text=self.new_text,
            font=self.font,
            font_size=self.font_size,
            x=self.x,
            y=self.y,
            style=self.style,
            metadata=self.metadata,
            indicator=self.indicator,
            scale_x=self.scale_x,
            scale_y=self.scale_y,
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
