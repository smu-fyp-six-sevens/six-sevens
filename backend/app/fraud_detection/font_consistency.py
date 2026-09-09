from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Literal

import pymupdf
from pydantic import BaseModel

logger = logging.getLogger("fraud_detection.font_consistency")

# AC8: a size difference this small or smaller is rendering noise, not a
# tamper signal.
SIZE_TOLERANCE_PT = 0.5

# Coarse vertical bucketing for clustering spans into structural regions —
# coarse enough to keep a paragraph's wrapped lines in one region, fine
# enough not to conflate unrelated regions further down a tall page.
Y_BAND_HEIGHT_PT = 50.0

# Confidence scoring: line-level comparison (does this span match its OWN
# line's dominant style?) is the primary signal — a real forgery typically
# edits a fragment mid-line, so the tampered text clashes with its own
# line-mates. Region-level comparison (the page/y-band/size cluster) is a
# weaker, secondary signal — a whole field styled differently on its own
# line (a signature, a stamp, a bold date column) has nothing on that line
# to disagree with, so it should never score highly on that basis alone.
# This is not a suppression gate: every span with a nonzero score is
# returned (see check_font_consistency) — the downstream agent reasoning
# over this evidence is expected to weigh low-confidence signals itself,
# rather than having them hidden before it ever sees them.
_TYPE_WEIGHT = {"font": 0.6, "size": 0.4, "emphasis": 0.2}
_SOURCE_MULTIPLIER = {"line": 1.0, "region": 0.5}
_CORROBORATION_BONUS = 0.2

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


class FontAnomaly(BaseModel):
    """
    One deviation *pattern* — not one span. Spans sharing the same
    deviation_type/evidence/detected/expected signature (e.g. "12 bold
    subtotal-row cells, all deviating from their region's non-bold
    majority the same way") are grouped into a single entry with all their
    locations, rather than repeated as near-identical entries. Nothing is
    discarded — every span is still listed under `locations` — this only
    changes how repeats of the same pattern are presented, since a real
    document can have dozens of spans sharing one benign styling pattern
    (see `_group_anomalies`).
    """

    deviation_type: Literal["font", "size", "emphasis", "combo"]
    confidence: float
    evidence: list[Literal["line", "region"]]
    detected: dict
    expected: dict
    occurrence_count: int
    locations: list[AnomalyLocation]


class _RawAnomaly(BaseModel):
    """Per-span deviation, before grouping — see _score_deviations/_group_anomalies."""

    page: int
    bbox: BBox
    text: str
    deviation_type: Literal["font", "size", "emphasis", "combo"]
    confidence: float
    evidence: list[Literal["line", "region"]]
    detected: dict
    expected: dict


class FontConsistencyResult(BaseModel):
    document_id: str
    anomalies: list[FontAnomaly]
    all_detected_styles: list[DetectedStyle]
    unable_to_assess: bool


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


def _empty_result(document_id: str) -> FontConsistencyResult:
    return FontConsistencyResult(
        document_id=document_id, anomalies=[], all_detected_styles=[], unable_to_assess=True
    )


def check_font_consistency(document_id: str, file_bytes: bytes) -> FontConsistencyResult:
    """
    Inspects a PDF's own font/size/emphasis usage for internal
    inconsistency (e.g. one line in a different font than the paragraph
    around it) — a classic tamper signal. Text/metadata-based, no LLM
    tokens. Synchronous/CPU-bound — callers should run this via
    asyncio.to_thread.
    """
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception:
        logger.warning("Failed to open document %s as a PDF", document_id, exc_info=True)
        return _empty_result(document_id)

    try:
        spans = _extract_spans(doc)
        if not spans:
            return _empty_result(document_id)

        line_groups: dict[tuple[int, int, int], list[_Span]] = {}
        region_clusters: dict[tuple[int, int, float], list[_Span]] = {}
        for span in spans:
            line_groups.setdefault(span.line_key, []).append(span)
            region_clusters.setdefault(_cluster_key(span), []).append(span)

        line_dominants = {key: _dominant_pattern(group) for key, group in line_groups.items()}
        region_dominants = {
            key: _dominant_pattern(group) for key, group in region_clusters.items()
        }

        raw_anomalies = _score_deviations(spans, line_dominants, region_dominants)
        anomalies = _group_anomalies(raw_anomalies)

        return FontConsistencyResult(
            document_id=document_id,
            anomalies=anomalies,
            all_detected_styles=_compile_detected_styles(spans),
            unable_to_assess=False,
        )
    except Exception:
        logger.exception("Font consistency check failed for document %s", document_id)
        return _empty_result(document_id)
    finally:
        doc.close()


def _extract_spans(doc: pymupdf.Document) -> list[_Span]:
    spans: list[_Span] = []
    for page_index, page in enumerate(doc):
        underline_rects = _underline_rects(page)
        for block_index, block in enumerate(page.get_text("dict").get("blocks", [])):
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


def _cluster_key(span: _Span) -> tuple[int, int, float]:
    y_band = int(span.bbox[1] // Y_BAND_HEIGHT_PT)
    size_bucket = round(span.size * 2) / 2  # nearest 0.5pt
    return (span.page, y_band, size_bucket)


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


def _score_deviations(
    spans: list[_Span],
    line_dominants: dict[tuple[int, int, int], DominantPattern],
    region_dominants: dict[tuple[int, int, float], DominantPattern],
) -> list[_RawAnomaly]:
    """
    Scores each span against both its line's dominant pattern (primary
    signal — a real forgery typically edits a fragment mid-line, clashing
    with its own line-mates) and its region's dominant pattern (secondary —
    a whole field legitimately styled differently, like a signature or
    stamp, has nothing on its own line to disagree with, so line-level
    alone won't catch it, but it shouldn't score as highly as a line-level
    mismatch either). No suppression threshold: every span with a nonzero
    score is returned — the downstream agent weighs confidence itself.
    """
    anomalies: list[_RawAnomaly] = []
    for span in spans:
        line_pattern = line_dominants[span.line_key]
        region_pattern = region_dominants[_cluster_key(span)]
        line_dev = _dimension_deviations(span, line_pattern)
        region_dev = _dimension_deviations(span, region_pattern)

        confidence = 0.0
        contributing_dims: list[str] = []
        evidence: set[Literal["line", "region"]] = set()
        for dim in ("font", "size", "emphasis"):
            flagged_by_line = line_dev[dim]
            flagged_by_region = region_dev[dim]
            if not flagged_by_line and not flagged_by_region:
                continue

            multiplier = max(
                _SOURCE_MULTIPLIER["line"] if flagged_by_line else 0.0,
                _SOURCE_MULTIPLIER["region"] if flagged_by_region else 0.0,
            )
            dim_confidence = _TYPE_WEIGHT[dim] * multiplier
            if flagged_by_line and flagged_by_region:
                dim_confidence += _CORROBORATION_BONUS

            confidence += dim_confidence
            contributing_dims.append(dim)
            if flagged_by_line:
                evidence.add("line")
            if flagged_by_region:
                evidence.add("region")

        if not contributing_dims:
            continue

        deviation_type: Literal["font", "size", "emphasis", "combo"]
        deviation_type = "combo" if len(contributing_dims) >= 2 else contributing_dims[0]  # type: ignore[assignment]

        # For each dimension, "expected" must come from whichever source
        # actually flagged that dimension's deviation — a span can match
        # its own line (line_dev=False) while disagreeing with its region
        # (region_dev=True); showing the line's value there would display
        # detected == expected for a dimension that IS flagged as deviating.
        # For a non-deviating dimension it doesn't matter which source is
        # shown, so default to line (primary) when available.
        line_font, line_size, line_emphasis = line_pattern
        region_font, region_size, region_emphasis = region_pattern

        def _pick(dim: str, line_value, region_value):
            if line_dev[dim]:
                return line_value
            if region_dev[dim]:
                return region_value
            return line_value if line_value is not None else region_value

        expected_font = _pick("font", line_font, region_font)
        expected_size = _pick("size", line_size, region_size)
        expected_emphasis = _pick("emphasis", line_emphasis, region_emphasis)

        anomalies.append(
            _RawAnomaly(
                page=span.page,
                bbox=span.bbox,
                text=span.text,
                deviation_type=deviation_type,
                confidence=min(1.0, confidence),
                evidence=sorted(evidence),
                detected={
                    "font": span.font,
                    "size": span.size,
                    "bold": span.bold,
                    "italic": span.italic,
                    "underline": span.underline,
                },
                expected={
                    "font": expected_font,
                    "size": expected_size,
                    "bold": expected_emphasis[0] if expected_emphasis else None,
                    "italic": expected_emphasis[1] if expected_emphasis else None,
                    "underline": expected_emphasis[2] if expected_emphasis else None,
                },
            )
        )
    return anomalies


def _group_anomalies(raw_anomalies: list[_RawAnomaly]) -> list[FontAnomaly]:
    """
    Groups spans sharing the same deviation signature into one FontAnomaly
    with all their locations, instead of one entry per span — a real
    document can have dozens of spans sharing one benign styling pattern
    (e.g. every bold subtotal-row cell deviating from its region's
    non-bold majority the same way), which would otherwise dominate the
    output with near-identical entries. Every span is still present, under
    `locations` — this only changes how repeats of the same pattern are
    presented, it doesn't discard anything. Sorted by confidence descending
    so the rarest/most-telling patterns (e.g. a genuine font-family
    mismatch) surface first regardless of how much of the list gets read.
    """
    groups: dict[tuple, list[_RawAnomaly]] = {}
    for raw in raw_anomalies:
        key = (
            raw.deviation_type,
            tuple(raw.evidence),
            tuple(sorted(raw.detected.items())),
            tuple(sorted(raw.expected.items())),
        )
        groups.setdefault(key, []).append(raw)

    grouped = [
        FontAnomaly(
            deviation_type=items[0].deviation_type,
            confidence=items[0].confidence,
            evidence=items[0].evidence,
            detected=items[0].detected,
            expected=items[0].expected,
            occurrence_count=len(items),
            locations=[
                AnomalyLocation(page=item.page, bbox=item.bbox, text=item.text)
                for item in items
            ],
        )
        for items in groups.values()
    ]
    grouped.sort(key=lambda anomaly: anomaly.confidence, reverse=True)
    return grouped


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
