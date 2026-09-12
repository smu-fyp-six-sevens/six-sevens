from __future__ import annotations

import json
import logging
from typing import Any, Literal

import pymupdf
from pydantic import BaseModel

# Deliberately reuses these unchanged from the v1 module rather than
# duplicating them — font-family normalization, dominant-pattern/tie
# handling, per-dimension deviation checks, span extraction (incl.
# underline-via-vector-drawing detection) and the style inventory are all
# still correct; only the comparison *scope* and *scoring* change in v2.
from .font_consistency import (
    AnomalyLocation,
    BBox,
    DetectedStyle,
    DominantPattern,
    _Span,
    _compile_detected_styles,
    _dimension_deviations,
    _dominant_pattern,
    _extract_spans,
)

logger = logging.getLogger("fraud_detection.font_consistency_v2")

# Spans on the same page whose baseline (bbox y1) fall within this many
# points of each other form one "row" — replaces PyMuPDF's own line
# grouping, which was confirmed (via direct testing) to split a single
# visual table row into multiple separate "line" entries whenever its cells
# are rendered as distinct text objects.
ROW_Y_TOLERANCE_PT = 2.0

# A field's extracted text is considered "found" at a given page.search_for
# match if a span's bbox overlaps that match by at least this fraction of
# the span's own area. Not 100% because search_for's match rect and a
# span's bbox rarely align pixel-perfectly (padding, kerning).
_FIELD_OVERLAP_MIN_RATIO = 0.3

RowKey = tuple[int, int]  # (page, row_index)


class ExtractedField(BaseModel):
    """
    Mirrors the extracted_fields table's row shape (minus columns this
    check doesn't use — see extracted_fields_repository.get_extracted_fields).
    field_value is deliberately Any, not str: the column is JSON-typed, so
    postgrest hands back whatever native type the stored JSON value
    actually is — a str for a text field, but a float for a numeric one
    (observed directly against real data) — and some rows store a further
    JSON-encoded string on top of that (e.g. '"ACME Pte Ltd"' rather than
    'ACME Pte Ltd'). See _field_search_text for how this gets resolved to
    actual searchable text.
    """

    field_name: str
    field_type: str
    field_value: Any
    confidence: float


class FontAnomalyV2(BaseModel):
    """
    One deviation *pattern*, fact-only (no confidence/severity scoring —
    see the font_consistency_v2 plan). `field_name`/`occurrences`/
    `deviating_occurrences` are set only for field-anchored findings; row-
    level findings leave them None and rely on `occurrence_count` /
    `locations` instead, same as v1.
    """

    deviation_type: Literal["font", "size", "emphasis", "combo"]
    detected: dict
    expected: dict
    occurrence_count: int
    locations: list[AnomalyLocation]
    field_name: str | None = None
    occurrences: int | None = None
    deviating_occurrences: int | None = None


class _RawAnomalyV2(BaseModel):
    """Per-span deviation, before grouping — see _score_row_deviations/_check_field_anchored/_group_anomalies_v2."""

    page: int
    bbox: BBox
    text: str
    deviation_type: Literal["font", "size", "emphasis", "combo"]
    detected: dict
    expected: dict
    field_name: str | None = None
    occurrences: int | None = None
    deviating_occurrences: int | None = None


class FontConsistencyResultV2(BaseModel):
    document_id: str
    anomalies: list[FontAnomalyV2]
    all_detected_styles: list[DetectedStyle]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


def _empty_result(document_id: str, reason: str | None = None) -> FontConsistencyResultV2:
    return FontConsistencyResultV2(
        document_id=document_id,
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def check_font_consistency_v2(
    document_id: str,
    file_bytes: bytes,
    extracted_fields: list[ExtractedField] | None = None,
) -> FontConsistencyResultV2:
    """
    v2: row-level comparison (tight, local — replaces v1's line+region dual
    comparison) plus, when extracted_fields is provided, a field-anchored
    comparison that locates each extracted field's value in the PDF text
    layer and checks its rendering against the document's own baseline
    style. Synchronous/CPU-bound — callers should run this via
    asyncio.to_thread.
    """
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        reason = f"Failed to open document as a PDF: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _empty_result(document_id, reason)

    try:
        spans = _extract_spans(doc)
        if not spans:
            has_images = any(page.get_images() for page in doc)
            reason = (
                f"No extractable text spans found ({doc.page_count} page(s), "
                f"has embedded images: {has_images}) -- likely a scanned/"
                "image-only PDF with no text layer, or an encrypted/"
                "corrupted one."
            )
            logger.warning("Font consistency check (v2): document %s: %s", document_id, reason)
            return _empty_result(document_id, reason)

        spans_by_page: dict[int, list[_Span]] = {}
        for span in spans:
            spans_by_page.setdefault(span.page, []).append(span)

        rows = _build_rows(spans)
        row_of_span: dict[int, RowKey] = {
            id(span): row_key for row_key, group in rows.items() for span in group
        }
        row_dominants = {key: _dominant_pattern(group) for key, group in rows.items()}

        raw_anomalies = _score_row_deviations(spans, row_of_span, row_dominants)

        if extracted_fields:
            baseline_pattern = _dominant_pattern(spans)
            raw_anomalies += _check_field_anchored(
                extracted_fields, doc, spans_by_page, baseline_pattern
            )

        anomalies = _group_anomalies_v2(raw_anomalies)

        return FontConsistencyResultV2(
            document_id=document_id,
            anomalies=anomalies,
            all_detected_styles=_compile_detected_styles(spans),
            unable_to_assess=False,
        )
    except Exception as exc:
        logger.exception("Font consistency check (v2) failed for document %s", document_id)
        reason = f"Unexpected error during font consistency check: {type(exc).__name__}: {exc}"
        return _empty_result(document_id, reason)
    finally:
        doc.close()


def _build_rows(spans: list[_Span]) -> dict[RowKey, list[_Span]]:
    by_page: dict[int, list[_Span]] = {}
    for span in spans:
        by_page.setdefault(span.page, []).append(span)

    rows: dict[RowKey, list[_Span]] = {}
    for page, page_spans in by_page.items():
        page_spans.sort(key=lambda s: (s.bbox[3], s.bbox[0]))
        row_index = -1
        row_baseline: float | None = None
        for span in page_spans:
            baseline = span.bbox[3]
            if row_baseline is None or abs(baseline - row_baseline) > ROW_Y_TOLERANCE_PT:
                row_index += 1
                row_baseline = baseline
            rows.setdefault((page, row_index), []).append(span)
    return rows


def _score_row_deviations(
    spans: list[_Span],
    row_of_span: dict[int, RowKey],
    row_dominants: dict[RowKey, DominantPattern],
) -> list[_RawAnomalyV2]:
    """
    Compares each span only against its own row's dominant pattern — a real
    forgery typically edits a fragment that then clashes with its
    immediate neighbours. No second, broader comparison scope: the
    structural-role/region clustering v1 used to catch legitimate
    conventions (e.g. every bold "Total" row) is dropped in favour of
    _group_anomalies_v2's occurrence_count, which already tells the
    consuming agent whether an identical deviation recurs across many rows
    (a convention) or shows up once (the higher-signal case).
    """
    anomalies: list[_RawAnomalyV2] = []
    for span in spans:
        pattern = row_dominants[row_of_span[id(span)]]
        dev = _dimension_deviations(span, pattern)
        contributing = [dim for dim in ("font", "size", "emphasis") if dev[dim]]
        if not contributing:
            continue

        deviation_type: Literal["font", "size", "emphasis", "combo"]
        deviation_type = "combo" if len(contributing) >= 2 else contributing[0]  # type: ignore[assignment]
        dominant_font, dominant_size, dominant_emphasis = pattern

        anomalies.append(
            _RawAnomalyV2(
                page=span.page,
                bbox=span.bbox,
                text=span.text,
                deviation_type=deviation_type,
                detected={
                    "font": span.font,
                    "size": span.size,
                    "bold": span.bold,
                    "italic": span.italic,
                    "underline": span.underline,
                },
                expected={
                    "font": dominant_font,
                    "size": dominant_size,
                    "bold": dominant_emphasis[0] if dominant_emphasis else None,
                    "italic": dominant_emphasis[1] if dominant_emphasis else None,
                    "underline": dominant_emphasis[2] if dominant_emphasis else None,
                },
            )
        )
    return anomalies


def _match_field_occurrences(
    doc: pymupdf.Document,
    spans_by_page: dict[int, list[_Span]],
    field_value: str,
) -> list[list[_Span]]:
    """
    Finds every occurrence of field_value in the document's text layer
    (page.search_for) and, for each occurrence, the span(s) that render it
    — usually one, but a tampered field could legitimately be split across
    spans of different styles, which is exactly the signal this check
    wants to catch.
    """
    occurrences: list[list[_Span]] = []
    for page_index, page in enumerate(doc):
        page_spans = spans_by_page.get(page_index)
        if not page_spans:
            continue
        try:
            rects = page.search_for(field_value)
        except ValueError:
            continue
        for rect in rects:
            matched = [span for span in page_spans if _overlaps(span.bbox, rect)]
            if matched:
                occurrences.append(matched)
    return occurrences


def _overlaps(bbox: BBox, rect: pymupdf.Rect) -> bool:
    span_rect = pymupdf.Rect(bbox)
    intersection = span_rect & rect
    if intersection.is_empty:
        return False
    span_area = span_rect.get_area()
    if span_area <= 0:
        return False
    return (intersection.get_area() / span_area) >= _FIELD_OVERLAP_MIN_RATIO


def _field_search_text(field: ExtractedField) -> str | None:
    """
    field_value comes back from the JSON-typed extracted_fields column as
    whatever native type the stored JSON value is — a str for a text
    field, but e.g. a float for a numeric one — and some string values are
    themselves JSON-encoded a second time (e.g. '"ACME Pte Ltd"' instead of
    'ACME Pte Ltd'), so a str value gets one decode attempt, falling back
    to itself when it isn't valid JSON. Only a *string* result (whether
    that's field_value itself or what it decodes to) is anchorable this
    way — a number/bool/list/dict has no single text span to search for,
    so those are skipped here rather than trusting a specific field_type
    label (which varies: the real table uses "string", not the "scalar"
    originally assumed, and there's no schema in this repo enumerating the
    full set of values it can take).
    """
    value = field.field_value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            decoded = value
    else:
        decoded = value

    if not isinstance(decoded, str):
        return None
    decoded = decoded.strip()
    return decoded or None


def _check_field_anchored(
    fields: list[ExtractedField],
    doc: pymupdf.Document,
    spans_by_page: dict[int, list[_Span]],
    baseline_pattern: DominantPattern,
) -> list[_RawAnomalyV2]:
    """
    For each extracted field whose value decodes to plain text, locates
    every occurrence of it in the document and compares the rendering
    span(s) against the document-wide dominant style. Fields whose decoded
    value isn't a string (e.g. a table/list-shaped field) are out of scope
    for this first pass — an honest limitation, not an oversight: there's
    no single "value span" to anchor on for those yet. A field whose value
    can't be found verbatim anywhere (whitespace/format drift, or a
    genuinely image-only document) is skipped rather than failing the
    whole check.
    """
    raw: list[_RawAnomalyV2] = []
    dominant_font, dominant_size, dominant_emphasis = baseline_pattern

    for field in fields:
        search_text = _field_search_text(field)
        if search_text is None:
            continue

        occurrence_groups = _match_field_occurrences(doc, spans_by_page, search_text)
        if not occurrence_groups:
            continue

        occurrences = len(occurrence_groups)
        deviating_occurrences = 0
        deviating: list[tuple[_Span, dict[str, bool]]] = []
        for group in occurrence_groups:
            group_deviates = False
            for span in group:
                dev = _dimension_deviations(span, baseline_pattern)
                if any(dev.values()):
                    group_deviates = True
                    deviating.append((span, dev))
            if group_deviates:
                deviating_occurrences += 1

        for span, dev in deviating:
            contributing = [dim for dim in ("font", "size", "emphasis") if dev[dim]]
            deviation_type: Literal["font", "size", "emphasis", "combo"]
            deviation_type = "combo" if len(contributing) >= 2 else contributing[0]  # type: ignore[assignment]

            raw.append(
                _RawAnomalyV2(
                    page=span.page,
                    bbox=span.bbox,
                    text=span.text,
                    deviation_type=deviation_type,
                    detected={
                        "font": span.font,
                        "size": span.size,
                        "bold": span.bold,
                        "italic": span.italic,
                        "underline": span.underline,
                    },
                    expected={
                        "font": dominant_font,
                        "size": dominant_size,
                        "bold": dominant_emphasis[0] if dominant_emphasis else None,
                        "italic": dominant_emphasis[1] if dominant_emphasis else None,
                        "underline": dominant_emphasis[2] if dominant_emphasis else None,
                    },
                    field_name=field.field_name,
                    occurrences=occurrences,
                    deviating_occurrences=deviating_occurrences,
                )
            )
    return raw


def _group_anomalies_v2(raw_anomalies: list[_RawAnomalyV2]) -> list[FontAnomalyV2]:
    """
    Same collapsing behaviour as v1's _group_anomalies (spans sharing a
    deviation signature become one entry with all their locations), with
    field_name folded into the signature so findings for different fields
    never merge. Sorted by occurrence_count ascending (rarest first) rather
    than by a confidence score — there isn't one in v2.
    """
    groups: dict[tuple, list[_RawAnomalyV2]] = {}
    for raw in raw_anomalies:
        key = (
            raw.deviation_type,
            raw.field_name,
            tuple(sorted(raw.detected.items())),
            tuple(sorted(raw.expected.items())),
        )
        groups.setdefault(key, []).append(raw)

    grouped = [
        FontAnomalyV2(
            deviation_type=items[0].deviation_type,
            detected=items[0].detected,
            expected=items[0].expected,
            occurrence_count=len(items),
            locations=[
                AnomalyLocation(page=item.page, bbox=item.bbox, text=item.text) for item in items
            ],
            field_name=items[0].field_name,
            occurrences=items[0].occurrences,
            deviating_occurrences=items[0].deviating_occurrences,
        )
        for items in groups.values()
    ]
    grouped.sort(key=lambda anomaly: anomaly.occurrence_count)
    return grouped
