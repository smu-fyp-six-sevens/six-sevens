from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from .field_text import replace_value, require_value, split_label_and_value
from .pdf import (
    MANIPULATION_INDICATORS,
    SCALING_ANOMALY_HEIGHT,
    SCALING_ANOMALY_WIDTH,
    apply_text_emphasis,
    manipulate_field_region,
    overlay_field_text,
    replace_field_text,
    read_document_metadata,
    set_document_metadata,
)
from .schemas import DefectType, FieldValue, InjectedDefect, InjectOverrides, InjectableDocument

_FONT_LABELS = {
    "tiro": "Times Roman",
    "cour": "Courier",
    "arial": "Arial",
    "helv": "Helvetica",
}


class DefectInjector(ABC):
    defect_type: DefectType

    @abstractmethod
    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        raise NotImplementedError


def _snapshot(field: FieldValue) -> dict:
    return field.model_dump()


def _working_path(document: InjectableDocument) -> Path:
    if not document.inject_path:
        raise ValueError("inject_path is not set")
    return Path(document.inject_path)


def _opts(overrides: InjectOverrides | None) -> InjectOverrides:
    return overrides or InjectOverrides()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text.lower() == "string":
        return None
    return text


def _require_text(value: str | None, name: str) -> str:
    text = _clean_text(value)
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _indicator_label(indicator: str) -> str:
    return indicator.replace("_", " ").title()


def _replacement_text(field: FieldValue, new_text: str) -> str:
    """Use `new_text` as the value, keeping a leading label if one exists."""
    if not new_text.strip():
        raise ValueError("new_text must not be empty")
    label, value = split_label_and_value(field.text)
    if label and value and not new_text.startswith(label):
        return replace_value(field.text, new_text)
    return new_text


def _require_injectable_value(document: InjectableDocument, field_name: str) -> str:
    field = document.fields[field_name]
    return require_value(field_name, field.text)


class FontInjector(DefectInjector):
    defect_type = DefectType.FONT

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        new_font = _require_text(_opts(overrides).font, "font")
        if new_font.lower() == (field.font or "").lower():
            raise ValueError(f"font replacement {new_font!r} matches the current font")
        replace_field_text(_working_path(document), field, field.text, font=new_font)
        field.font = new_font
        label = _FONT_LABELS.get(new_font, new_font)
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Changed field font from {before['font']} to {label}",
            before=before,
            after=_snapshot(field),
        )


class FontSizeInjector(DefectInjector):
    defect_type = DefectType.FONT_SIZE

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        opts = _opts(overrides)
        if opts.font_size is None:
            raise ValueError("font_size is required")
        new_size = max(6.0, round(float(opts.font_size), 1))
        if new_size == field.font_size:
            raise ValueError(
                f"font_size replacement {new_size} matches the current size"
            )
        replace_field_text(_working_path(document), field, field.text, font_size=new_size)
        field.font_size = new_size
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=(
                f"Changed field font size from {before['font_size']} to {new_size}"
            ),
            before=before,
            after=_snapshot(field),
        )


class NumericalInjector(DefectInjector):
    defect_type = DefectType.NUMERICAL

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        value = _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        match = re.search(r"\d+(?:\.\d+)?", value)
        if not match:
            raise ValueError(f"Field '{field_name}' does not contain a numeric value")
        original = match.group()
        new_text = _replacement_text(field, _require_text(_opts(overrides).new_text, "new_text"))
        if new_text == field.text:
            raise ValueError("new_text matches the current field text")
        replace_field_text(_working_path(document), field, new_text)
        field.text = new_text
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Tampered numeric value from {original} to {new_text}",
            before=before,
            after=_snapshot(field),
        )


class MetadataInjector(DefectInjector):
    defect_type = DefectType.METADATA

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        field = document.fields[field_name]
        before = _snapshot(field)
        path = _working_path(document)
        metadata = dict(read_document_metadata(path))
        updates = _opts(overrides).metadata
        if not updates:
            raise ValueError("metadata is required")
        notes: list[str] = []
        for key, value in updates.items():
            metadata[key] = value
            notes.append(f"{key}={value!r}")
        if metadata == before.get("metadata"):
            raise ValueError("metadata replacement matches the current metadata")
        set_document_metadata(path, metadata)
        field.metadata = metadata
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description="Tampered document metadata: " + ", ".join(notes),
            before=before,
            after=_snapshot(field),
        )


class SpellingInjector(DefectInjector):
    defect_type = DefectType.SPELLING

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        value = _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        new_text = _replacement_text(field, _require_text(_opts(overrides).new_text, "new_text"))
        if new_text == field.text:
            raise ValueError("new_text matches the current field text")
        replace_field_text(_working_path(document), field, new_text)
        field.text = new_text
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Introduced spelling anomaly: {value!r} → {new_text!r}",
            before=before,
            after=_snapshot(field),
        )


class MisalignmentInjector(DefectInjector):
    defect_type = DefectType.MISALIGNMENT

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        opts = _opts(overrides)
        if opts.x is None and opts.y is None:
            raise ValueError("misalignment requires x and/or y")
        new_x = field.x if opts.x is None else float(opts.x)
        new_y = field.y if opts.y is None else float(opts.y)
        notes: list[str] = []
        if new_x != field.x:
            notes.append(f"x {before['x']}→{new_x}")
        if new_y != field.y:
            notes.append(f"y {before['y']}→{new_y}")
        if new_x == field.x and new_y == field.y:
            raise ValueError("misalignment replacement matches the current position")
        replace_field_text(
            _working_path(document),
            field,
            field.text,
            x=new_x,
            y=new_y,
        )
        field.x = new_x
        field.y = new_y
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description="Shifted field position: " + ", ".join(notes),
            before=before,
            after=_snapshot(field),
        )


class EmphasisInjector(DefectInjector):
    defect_type = DefectType.EMPHASIS

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        _require_injectable_value(document, field_name)
        field = document.fields[field_name]
        before = _snapshot(field)
        style = _require_text(_opts(overrides).style, "style").lower()
        if style not in {"bold", "italic", "underline"}:
            raise ValueError("style must be 'bold', 'italic', or 'underline'")
        if style == field.style:
            raise ValueError(f"style replacement {style!r} matches the current style")
        apply_text_emphasis(_working_path(document), field, style)
        field.style = style
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Applied text emphasis: {style}",
            before=before,
            after=_snapshot(field),
        )


class TextOverlayInjector(DefectInjector):
    defect_type = DefectType.TEXT_OVERLAY

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        field = document.fields[field_name]
        before = _snapshot(field)
        opts = _opts(overrides)
        overlay = _require_text(opts.new_text, "new_text")
        overlay_field_text(
            _working_path(document),
            field,
            overlay,
            x=opts.x,
            y=opts.y,
        )
        field.overlay = overlay
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=f"Overlaid matching text on existing field: {overlay!r}",
            before=before,
            after=_snapshot(field),
        )


class ImageManipulationInjector(DefectInjector):
    defect_type = DefectType.IMAGE_MANIPULATION

    def inject(
        self,
        document: InjectableDocument,
        field_name: str,
        overrides: InjectOverrides | None = None,
    ) -> InjectedDefect:
        field = document.fields[field_name]
        before = _snapshot(field)
        path = _working_path(document)
        opts = _opts(overrides)
        indicator = _require_text(opts.indicator, "indicator").lower().replace(" ", "_")
        if indicator not in MANIPULATION_INDICATORS:
            raise ValueError(
                f"Unknown image-manipulation indicator {indicator!r}. "
                f"Use one of: {', '.join(MANIPULATION_INDICATORS)}"
            )
        if field.kind != "image":
            image_names = [
                name for name, item in document.fields.items() if item.kind == "image"
            ]
            if image_names:
                raise ValueError(
                    f"Field '{field_name}' is not an image field. "
                    f"Use one of: {', '.join(sorted(image_names))}"
                )
            raise ValueError(
                f"Document '{document.id}' has no image regions to manipulate"
            )
        if len(field.rect) != 4:
            raise ValueError("image_manipulation requires a field bounding box")
        manipulate_field_region(
            path,
            field,
            indicator,
            scale_x=opts.scale_x if indicator == "scaling_anomaly" else None,
            scale_y=opts.scale_y if indicator == "scaling_anomaly" else None,
        )
        field.manipulation = indicator
        description = f"Applied image manipulation: {_indicator_label(indicator)}"
        if indicator == "scaling_anomaly":
            width = opts.scale_x if opts.scale_x is not None else SCALING_ANOMALY_WIDTH
            height = opts.scale_y if opts.scale_y is not None else SCALING_ANOMALY_HEIGHT
            description += f" ({width:g}× wide, {height:g}× tall)"
        return InjectedDefect(
            defect_type=self.defect_type,
            field_name=field_name,
            description=description,
            before=before,
            after=_snapshot(field),
        )


INJECTORS: dict[DefectType, DefectInjector] = {
    DefectType.FONT: FontInjector(),
    DefectType.FONT_SIZE: FontSizeInjector(),
    DefectType.NUMERICAL: NumericalInjector(),
    DefectType.METADATA: MetadataInjector(),
    DefectType.SPELLING: SpellingInjector(),
    DefectType.MISALIGNMENT: MisalignmentInjector(),
    DefectType.EMPHASIS: EmphasisInjector(),
    DefectType.TEXT_OVERLAY: TextOverlayInjector(),
    DefectType.IMAGE_MANIPULATION: ImageManipulationInjector(),
}


def get_injector(defect_type: DefectType) -> DefectInjector:
    return INJECTORS[defect_type]
