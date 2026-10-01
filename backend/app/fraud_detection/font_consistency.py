from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any, Literal, cast

import pymupdf
from pydantic import BaseModel

logger = logging.getLogger("fraud_detection.font_consistency")


def _page(doc: pymupdf.Document, index: int) -> pymupdf.Page:
    """
    pymupdf.Document.__getitem__ is declared via inline @typing.overload in
    its own source (no separate .pyi), with a default value on the int
    overload and the slice/tuple overloads gated behind a sys.version_info
    check — pyright can't cleanly resolve this for either direct indexing or
    the old-style __getitem__-based iteration protocol (no __iter__ is
    declared), and infers the element type as the whole overload-return
    union instead of narrowing to Page. One cast, used everywhere a page is
    pulled out of a Document, instead of re-deriving this at each call site.
    """
    return cast(pymupdf.Page, doc[index])


def _pages(doc: pymupdf.Document) -> list[pymupdf.Page]:
    return [_page(doc, i) for i in range(len(doc))]

# --- span extraction & style-comparison building blocks ---------------------
#
# Originally lived in a separate font_consistency.py (v1) module and were
# imported unchanged from there — font-family normalization, dominant-
# pattern/tie handling, per-dimension deviation checks, span extraction
# (incl. underline-via-vector-drawing detection) and the style inventory
# were all still correct in v2, only the comparison *scope* and *scoring*
# changed. v1's own public API (check_font_consistency and its line+region
# dual-scope scoring) was retired outright once v2 superseded it, so these
# building blocks moved here instead, now private to this module.

# AC8: a size difference this small or smaller is rendering noise, not a
# tamper signal.
SIZE_TOLERANCE_PT = 0.5

# PyMuPDF span flag bits (see page.get_text("dict")): bit 1 (2) = italic,
# bit 4 (16) = bold. Underline isn't a font flag — PDFs render it as vector
# graphics, so it's detected separately via page.get_drawings().
_ITALIC_FLAG = 2
_BOLD_FLAG = 16

# A drawing counts as an underline stroke if it's at most this tall...
_UNDERLINE_MAX_HEIGHT_PT = 2.0
# ...sits within this many points of the span's baseline...
_UNDERLINE_BASELINE_TOLERANCE_PT = 3.0
# ...and its x-range overlaps at least this fraction of the span's width.
_UNDERLINE_MIN_X_OVERLAP_RATIO = 0.5

BBox = tuple[float, float, float, float]
Emphasis = tuple[bool, bool, bool]  # (bold, italic, underline)

# Strips a PDF subset tag ("ABCDEF+Helvetica-Bold" -> "Helvetica-Bold") and a
# trailing bold/italic/oblique variant suffix ("Helvetica-Bold" ->
# "Helvetica"). Bold/italic are already captured precisely via the span's
# flags — comparing raw font names for font_dev would otherwise double-count
# "this span is bold" as both a font AND an emphasis deviation whenever the
# bold variant is a differently-named font resource (the common case:
# Helvetica vs Helvetica-Bold are separate font names, not the same name
# with a flag toggled), inflating nearly every real emphasis difference
# into "combo" and making "font"/"combo" indistinguishable from it.
_SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
_EMPHASIS_SUFFIX_RE = re.compile(r"[-,]?(BoldItalic|BoldOblique|Bold|Italic|Oblique)$", re.IGNORECASE)


def _font_family(font_name: str) -> str:
    name = _SUBSET_PREFIX_RE.sub("", font_name)
    while True:
        stripped = _EMPHASIS_SUFFIX_RE.sub("", name)
        if stripped == name:
            return name or font_name
        name = stripped


class DetectedStyle(BaseModel):
    font: str
    size: float
    bold: bool
    italic: bool
    underline: bool
    occurrence_count: int


class AnomalyLocation(BaseModel):
    page: int
    bbox: BBox
    text: str


class _Span(BaseModel):
    text: str
    font: str
    size: float
    bold: bool
    italic: bool
    underline: bool
    bbox: BBox
    page: int
    line_key: tuple[int, int, int]  # (page, block_index, line_index) — PyMuPDF's own line grouping


def _extract_spans(doc: pymupdf.Document) -> list[_Span]:
    spans: list[_Span] = []
    for page_index, page in enumerate(_pages(doc)):
        underline_rects = _underline_rects(page)
        # Same overload-ambiguity as get_text("text") elsewhere in this
        # codebase — "dict" is always a dict at runtime for this mode.
        page_dict = cast(dict, page.get_text("dict"))
        for block_index, block in enumerate(page_dict.get("blocks", [])):
            if block.get("type") != 0:  # not a text block
                continue
            for line_index, line in enumerate(block.get("lines", [])):
                for raw_span in line.get("spans", []):
                    text = (raw_span.get("text") or "").strip()
                    if not text:
                        continue
                    bbox = raw_span.get("bbox")
                    if not bbox or len(bbox) != 4:
                        continue
                    bbox = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
                    flags = int(raw_span.get("flags") or 0)
                    spans.append(
                        _Span(
                            text=text,
                            font=raw_span.get("font") or "",
                            size=float(raw_span.get("size") or 0.0),
                            bold=bool(flags & _BOLD_FLAG),
                            italic=bool(flags & _ITALIC_FLAG),
                            underline=_is_underlined(bbox, underline_rects),
                            bbox=bbox,
                            page=page_index,
                            line_key=(page_index, block_index, line_index),
                        )
                    )
    return spans


def _underline_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Thin, mostly-horizontal vector drawings — underline is rendered as a
    drawn line/rect, never a font attribute, so it needs its own pass."""
    rects: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        if (rect.y1 - rect.y0) <= _UNDERLINE_MAX_HEIGHT_PT and (rect.x1 - rect.x0) > 0:
            rects.append(rect)
    return rects


def _is_underlined(bbox: BBox, underline_rects: list[pymupdf.Rect]) -> bool:
    x0, _, x1, baseline = bbox
    span_width = x1 - x0
    if span_width <= 0:
        return False

    for rect in underline_rects:
        near_baseline = (
            abs(rect.y0 - baseline) <= _UNDERLINE_BASELINE_TOLERANCE_PT
            or abs(rect.y1 - baseline) <= _UNDERLINE_BASELINE_TOLERANCE_PT
        )
        if not near_baseline:
            continue
        overlap = min(rect.x1, x1) - max(rect.x0, x0)
        if overlap / span_width >= _UNDERLINE_MIN_X_OVERLAP_RATIO:
            return True
    return False


DominantPattern = tuple[str | None, float | None, Emphasis | None]


def _dominant_pattern(spans: list[_Span]) -> DominantPattern:
    # A tie for the most common value means there IS no dominant pattern —
    # e.g. a 2-span group split one bold, one not has no majority either
    # way, so picking either arbitrarily as "expected" and flagging the
    # other would be a coin flip, not a real deviation.
    return (
        _dominant(span.font for span in spans),
        _dominant(span.size for span in spans),
        _dominant((span.bold, span.italic, span.underline) for span in spans),
    )


def _dimension_deviations(span: _Span, pattern: DominantPattern) -> dict[str, bool]:
    """Which of font/size/emphasis the span deviates from in `pattern` — a
    dimension with no dominant value (a tie) can't be assessed, so it never
    counts as a deviation."""
    dominant_font, dominant_size, dominant_emphasis = pattern
    dominant_family = _font_family(dominant_font) if dominant_font is not None else None
    return {
        "font": dominant_family is not None and _font_family(span.font) != dominant_family,
        "size": dominant_size is not None and abs(span.size - dominant_size) > SIZE_TOLERANCE_PT,
        "emphasis": (
            dominant_emphasis is not None
            and (span.bold, span.italic, span.underline) != dominant_emphasis
        ),
    }


def _compile_detected_styles(spans: list[_Span]) -> list[DetectedStyle]:
    counts = Counter(
        (span.font, span.size, span.bold, span.italic, span.underline) for span in spans
    )
    return [
        DetectedStyle(
            font=font, size=size, bold=bold, italic=italic, underline=underline,
            occurrence_count=count,
        )
        for (font, size, bold, italic, underline), count in counts.most_common()
    ]


def _dominant(values):
    """
    Most common value, or None if there's a tie for first place (including
    the trivial 1-vs-1 tie in a 2-item cluster) — a tie means there's no
    real majority pattern to call "expected", so nothing in the cluster
    should be flagged as deviating from one.
    """
    counts = Counter(values).most_common()
    if not counts:
        return None
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        return None
    return counts[0][0]


# --- v2-specific ---------------------------------------------------------

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
    id: str | None = None
    field_name: str
    field_type: str
    field_value: Any
    confidence: float


class FontAnomalyV2(BaseModel):
    """
    One deviation *pattern*, fact-only (no confidence/severity scoring —
    see the font_consistency plan). `field_name`/`occurrences`/
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


class FontConsistencyResult(BaseModel):
    document_id: str
    anomalies: list[FontAnomalyV2]
    all_detected_styles: list[DetectedStyle]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


def _empty_result(document_id: str, reason: str | None = None) -> FontConsistencyResult:
    return FontConsistencyResult(
        document_id=document_id,
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def check_font_consistency(
    document_id: str,
    file_bytes: bytes,
    extracted_fields: list[ExtractedField] | None = None,
) -> FontConsistencyResult:
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
            has_images = any(page.get_images() for page in _pages(doc))
            reason = (
                f"No extractable text spans found ({doc.page_count} page(s), "
                f"has embedded images: {has_images}) -- likely a scanned/"
                "image-only PDF with no text layer, or an encrypted/"
                "corrupted one."
            )
            logger.warning("Font consistency check: document %s: %s", document_id, reason)
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

        return FontConsistencyResult(
            document_id=document_id,
            anomalies=anomalies,
            all_detected_styles=_compile_detected_styles(spans),
            unable_to_assess=False,
        )
    except Exception as exc:
        logger.exception("Font consistency check failed for document %s", document_id)
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
    for page_index, page in enumerate(_pages(doc)):
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
