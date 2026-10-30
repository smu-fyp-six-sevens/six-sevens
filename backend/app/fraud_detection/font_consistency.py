"""
Font, size and emphasis consistency.

A text run is judged against the peers that play the same part in the
document's structure, never against whatever happens to share its row:
the entries of its column, or the other section headings. A styled row
(a bold total, an underlined subtotal) is legitimate when its own cells
agree with each other, and a signature is never compared at all.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Any, Hashable, Literal, cast

import pymupdf
from pydantic import BaseModel
from .shared.check_result import CheckResult
from .shared.field_values import decode_field_value
from .shared.pdf_pages import page as _page, pages as _pages

logger = logging.getLogger("fraud_detection.font_consistency")



# A size difference this small or smaller is rendering noise, not a tamper signal.
SIZE_TOLERANCE_PT = 0.5

# PyMuPDF span flag bits: bit 1 (2) = italic, bit 4 (16) = bold. Underline is
# not a font flag, so it is detected from thin vector drawings instead.
_ITALIC_FLAG = 2
_BOLD_FLAG = 16

# A drawing is an underline stroke if it is at most this tall, sits within
# this many points of the span's baseline, overlaps this fraction of the
# span's width, is no wider than this fraction of the page, and does not run
# past its text line by more than this (a longer stroke is a cell border).
_UNDERLINE_MAX_HEIGHT_PT = 2.0
_UNDERLINE_BASELINE_TOLERANCE_PT = 3.0
_UNDERLINE_MIN_X_OVERLAP_RATIO = 0.5
_UNDERLINE_MAX_PAGE_WIDTH_RATIO = 0.5
_UNDERLINE_LINE_SLACK_PT = 3.0

BBox = tuple[float, float, float, float]
Emphasis = tuple[bool, bool, bool]  # (bold, italic, underline)
RowKey = tuple[int, int]  # (page, row_index)

# "ABCDEF+Helvetica-Bold" -> "Helvetica". Bold/italic are read from the span
# flags, so comparing raw names would count one bold span as two deviations.
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


class ExtractedField(BaseModel):
    """
    Mirrors the extracted_fields table's row shape. field_value is Any: the
    column is JSON-typed, so it can be a str, a float, or a str that is
    itself JSON-encoded (e.g. '"ACME Pte Ltd"') — see _field_search_text.
    """
    id: str | None = None
    field_name: str
    field_type: str
    field_value: Any
    confidence: float


class FontAnomalyV2(BaseModel):
    """One deviation pattern, fact-only (no confidence scoring)."""

    deviation_type: Literal["font", "size", "emphasis", "combo"]
    detected: dict
    expected: dict
    occurrence_count: int
    locations: list[AnomalyLocation]
    field_name: str | None = None


class _RawAnomaly(BaseModel):
    """Per-run deviation, before grouping."""

    page: int
    bbox: BBox
    text: str
    deviation_type: Literal["font", "size", "emphasis", "combo"]
    detected: dict
    expected: dict
    field_name: str | None = None


class FontConsistencyResult(CheckResult):
    document_id: str
    anomalies: list[FontAnomalyV2]
    all_detected_styles: list[DetectedStyle]


def _empty_result(document_id: str, reason: str | None = None) -> FontConsistencyResult:
    return FontConsistencyResult(
        document_id=document_id,
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


# --- span extraction --------------------------------------------------------


def _extract_spans(doc: pymupdf.Document) -> list[_Span]:
    spans: list[_Span] = []
    for page_index, page in enumerate(_pages(doc)):
        underline_rects = _underline_rects(page)
        page_dict = cast(dict, page.get_text("dict"))
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # not a text block
                continue
            for line in block.get("lines", []):
                line_bbox = line.get("bbox")
                line_extent = None
                if line_bbox and len(line_bbox) == 4:
                    line_extent = (float(line_bbox[0]), float(line_bbox[2]))
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
                            underline=_is_underlined(bbox, underline_rects, line_extent),
                            bbox=bbox,
                            page=page_index,
                        )
                    )
    return spans


def _underline_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Thin horizontal drawings narrower than a page rule."""
    max_width = float(page.rect.width) * _UNDERLINE_MAX_PAGE_WIDTH_RATIO
    rects: list[pymupdf.Rect] = []
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if rect is None:
            continue
        width = rect.x1 - rect.x0
        if (rect.y1 - rect.y0) <= _UNDERLINE_MAX_HEIGHT_PT and 0 < width <= max_width:
            rects.append(rect)
    return rects


def _is_underlined(
    bbox: BBox, underline_rects: list[pymupdf.Rect], line_extent: tuple[float, float] | None = None
) -> bool:
    x0, _, x1, baseline = bbox
    span_width = x1 - x0
    if span_width <= 0:
        return False

    for rect in underline_rects:
        if line_extent is not None and (
            rect.x0 < line_extent[0] - _UNDERLINE_LINE_SLACK_PT
            or rect.x1 > line_extent[1] + _UNDERLINE_LINE_SLACK_PT
        ):
            continue
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


# --- runs: the unit that is compared ----------------------------------------

# Spans on a page whose baselines are within this many points form one row.
ROW_Y_TOLERANCE_PT = 2.0
# Spans in a row closer than this (points, or this fraction of the font
# size) are one flowing run of text, not separate cells.
_RUN_MIN_GAP_PT = 4.0
_RUN_GAP_EM = 0.6
# Fewer letters/digits than this is punctuation or a glyph, not text.
_MIN_ALNUM = 2
# A row-mate needs this many to count as evidence about the row's style.
_MIN_MATE_ALNUM = 3
_LINK_RE = re.compile(r"https?://|www\.|\S+@\S+\.\S+", re.IGNORECASE)


@dataclass(eq=False)
class _Run:
    spans: list[_Span]
    page: int
    row: int
    bbox: BBox
    text: str
    font: str
    size: float
    emphasis: Emphasis
    numeric: bool
    signature: bool = False
    heading: bool = False

    @property
    def family(self) -> str:
        return _font_family(self.font)

    @property
    def row_key(self) -> RowKey:
        return (self.page, self.row)


def _alnum(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def _is_assessable(span: _Span) -> bool:
    # A NUL means a form-filled field in its own encoding, not typeset text.
    return "\x00" not in span.text and _alnum(span.text) >= _MIN_ALNUM


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


def _make_run(spans: list[_Span], row: int) -> _Run:
    lead = max(spans, key=lambda s: len(s.text))
    text = " ".join(s.text for s in spans)
    digits = sum(ch.isdigit() for ch in text)
    underline = lead.underline and not _LINK_RE.search(text)
    return _Run(
        spans=spans,
        page=lead.page,
        row=row,
        bbox=(
            min(s.bbox[0] for s in spans), min(s.bbox[1] for s in spans),
            max(s.bbox[2] for s in spans), max(s.bbox[3] for s in spans),
        ),
        text=text,
        font=lead.font,
        size=lead.size,
        emphasis=(lead.bold, lead.italic, underline),
        numeric=digits * 2 >= max(1, _alnum(text)),
    )


def _build_runs(spans: list[_Span]) -> list[_Run]:
    runs: list[_Run] = []
    for (_, row), group in _build_rows(spans).items():
        group.sort(key=lambda s: s.bbox[0])
        current = [group[0]]
        for span in group[1:]:
            prev = current[-1]
            gap = span.bbox[0] - prev.bbox[2]
            if gap > max(_RUN_MIN_GAP_PT, _RUN_GAP_EM * max(span.size, prev.size)):
                runs.append(_make_run(current, row))
                current = [span]
            else:
                current.append(span)
        runs.append(_make_run(current, row))
    return runs


# --- signatures -------------------------------------------------------------

_SCRIPT_FONT_RE = re.compile(
    r"script|hand|brush|calligr|cursive|signature|dancing|pacifico|vivaldi|mistral|"
    r"chancery|corsiva|allura|sacramento|vibes|satisfy|kaushan|caveat|homemade|reenie",
    re.IGNORECASE,
)
_RULE_TEXT_RE = re.compile(r"^[_\-–—.=\s]{4,}$")
_CAPTION_RE = re.compile(
    r"\b(signature|signed|signatory|director|witness|authori[sz]ed|on behalf|name|date)\b",
    re.IGNORECASE,
)
_RULE_MIN_WIDTH_PT = 30.0
# The rule sits at most this far under the text and is this much wider than
# it (an underline of the text itself is about as wide as the text).
_SIGNATURE_RULE_MAX_GAP_PT = 40.0
_SIGNATURE_RULE_MIN_WIDTH_RATIO = 1.25
_SIGNATURE_CAPTION_MAX_GAP_PT = 30.0
_SIGNATURE_MAX_WORDS = 6


def _rule_boxes(doc: pymupdf.Document, spans: list[_Span]) -> dict[int, list[BBox]]:
    """Horizontal rules per page: thin drawn lines and runs of underscores."""
    rules: dict[int, list[BBox]] = {}
    for page_index, page in enumerate(_pages(doc)):
        for drawing in page.get_drawings():
            rect = drawing.get("rect")
            if rect is None:
                continue
            if (rect.y1 - rect.y0) <= _UNDERLINE_MAX_HEIGHT_PT and (rect.x1 - rect.x0) >= _RULE_MIN_WIDTH_PT:
                rules.setdefault(page_index, []).append((rect.x0, rect.y0, rect.x1, rect.y1))
    for span in spans:
        if _RULE_TEXT_RE.match(span.text):
            rules.setdefault(span.page, []).append(span.bbox)
    return rules


def _x_overlap(a: BBox, b: BBox) -> float:
    return min(a[2], b[2]) - max(a[0], b[0])


def _mark_signatures(runs: list[_Run], rules: dict[int, list[BBox]]) -> None:
    """A signature is a script-font name, or a short line sitting on a signing
    rule that has a caption (Director, Signature, Date...) under it."""
    row_sizes = Counter(run.row_key for run in runs)
    by_page: dict[int, list[_Run]] = {}
    for run in runs:
        by_page.setdefault(run.page, []).append(run)

    for run in runs:
        if _SCRIPT_FONT_RE.search(run.font):
            run.signature = True
            continue
        if row_sizes[run.row_key] != 1 or len(run.text.split()) > _SIGNATURE_MAX_WORDS:
            continue
        width = run.bbox[2] - run.bbox[0]
        for rule in rules.get(run.page, []):
            gap = rule[1] - run.bbox[3]
            if gap > _SIGNATURE_RULE_MAX_GAP_PT or rule[3] < run.bbox[3] - 6:
                continue
            rule_width = rule[2] - rule[0]
            if width <= 0 or rule_width / width < _SIGNATURE_RULE_MIN_WIDTH_RATIO:
                continue
            if _x_overlap(run.bbox, rule) < 0.5 * width:
                continue
            if any(
                0 <= other.bbox[1] - rule[3] <= _SIGNATURE_CAPTION_MAX_GAP_PT
                and _x_overlap(other.bbox, rule) > 0
                and _CAPTION_RE.search(other.text)
                for other in by_page[run.page]
            ):
                run.signature = True
                break


# --- peers ------------------------------------------------------------------

# Runs whose left (or right, for numbers) edges agree within this belong to one column.
_COLUMN_TOLERANCE_PT = 2.0
# A column is broken into separate lists by a vertical gap larger than this.
_CHAIN_GAP_PT = 28.0
# A run is compared with up to this many neighbours on each side...
_WINDOW = 3
# ...and only when at least this many of them, and this share of them, agree.
_MIN_AGREE = 3
_MIN_AGREE_SHARE = 0.6
# Section headings: at least this many in one column, agreeing this strongly.
_HEADING_MIN_PEERS = 4
_HEADING_MIN_SHARE = 0.75
_HEADING_SIZE_MIN_SHARE = 0.8
_HEADING_MAX_WORDS = 14
# A bold line this long ending in a full stop is a sentence ("... Pte. Ltd." is not).
_HEADING_SENTENCE_WORDS = 7
# An underline alone on a line this long is a form's instruction text, not a
# label or heading that was dressed up.
_UNDERLINED_SENTENCE_WORDS = 8
_HEADING_MAX_CHARS = 110

_Dims = dict[str, bool]


def _agreed(values: list[Hashable], min_count: int, min_share: float) -> Hashable | None:
    """The value most of the peers share, or None when there is no clear majority."""
    if not values:
        return None
    ranked = Counter(values).most_common(2)
    value, count = ranked[0]
    if count < min_count or count / len(values) < min_share:
        return None
    if len(ranked) > 1 and ranked[1][1] == count:
        return None
    return value


@dataclass
class _Expectation:
    family: str | None
    font: str | None
    size: float | None
    emphasis: Emphasis | None

    def deviations(self, run: _Run) -> _Dims:
        return {
            "font": self.family is not None and run.family != self.family,
            "size": self.size is not None and abs(run.size - self.size) > SIZE_TOLERANCE_PT,
            "emphasis": self.emphasis is not None and run.emphasis != self.emphasis,
        }


def _expect(
    peers: list[_Run], min_count: int, min_share: float, size_min_count: int, size_min_share: float
) -> _Expectation:
    family = cast("str | None", _agreed([p.family for p in peers], min_count, min_share))
    size = cast("float | None", _agreed([round(p.size, 1) for p in peers], size_min_count, size_min_share))
    emphasis = cast("Emphasis | None", _agreed([p.emphasis for p in peers], min_count, min_share))
    font = next((p.font for p in peers if p.family == family), None) if family is not None else None
    return _Expectation(family=family, font=font, size=size, emphasis=emphasis)


def _clusters(runs: list[_Run], edge: Literal["left", "right"]) -> list[list[_Run]]:
    """Runs whose left (or right) edge falls in the same column."""
    index = 0 if edge == "left" else 2
    clusters: list[list[_Run]] = []
    for run in sorted(runs, key=lambda r: r.bbox[index]):
        if clusters and run.bbox[index] - clusters[-1][0].bbox[index] <= _COLUMN_TOLERANCE_PT:
            clusters[-1].append(run)
        else:
            clusters.append([run])
    return clusters


def _columns(runs: list[_Run], edge: Literal["left", "right"]) -> list[list[_Run]]:
    """Columns across the whole document, text and numbers kept apart."""
    columns: list[list[_Run]] = []
    for numeric in (False, True):
        columns += _clusters([run for run in runs if run.numeric == numeric], edge)
    return columns


def _chains(column: list[_Run]) -> list[list[_Run]]:
    """Vertically contiguous stacks within one column."""
    chains: list[list[_Run]] = []
    current: list[_Run] = []
    for run in sorted(column, key=lambda r: (r.page, r.bbox[1])):
        if current and (run.page != current[-1].page or run.bbox[1] - current[-1].bbox[3] > _CHAIN_GAP_PT):
            chains.append(current)
            current = []
        current.append(run)
    if current:
        chains.append(current)
    return chains


def _same_on(run: _Run, other: _Run, dim: str) -> bool:
    if dim == "font":
        return run.family == other.family
    if dim == "size":
        return abs(run.size - other.size) <= SIZE_TOLERANCE_PT
    return run.emphasis == other.emphasis


def _explained_by_row(run: _Run, dim: str, mates: list[_Run]) -> bool:
    """A styled row (total, subtotal): every other cell in it carries the same style."""
    substantial = [m for m in mates if _alnum(m.text) >= _MIN_MATE_ALNUM]
    return bool(substantial) and all(_same_on(run, m, dim) for m in substantial)


def _is_boundary_bold(run: _Run, index: int, length: int, expected: Emphasis | None) -> bool:
    """A bold column head or bold last row (a total) is a convention, not a deviation."""
    if expected is None or not run.emphasis[0] or expected[0]:
        return False
    return index in (0, length - 1) and run.emphasis[1:] == expected[1:]


def _chain_deviations(
    chain: list[_Run], index: int
) -> tuple[_Dims, _Expectation] | None:
    run = chain[index]
    neighbours = chain[max(0, index - _WINDOW):index] + chain[index + 1:index + 1 + _WINDOW]
    if len(neighbours) < _MIN_AGREE:
        return None
    expected = _expect(neighbours, _MIN_AGREE, _MIN_AGREE_SHARE, _MIN_AGREE, _MIN_AGREE_SHARE)
    dims = expected.deviations(run)
    if _is_boundary_bold(run, index, len(chain), expected.emphasis):
        dims["emphasis"] = dims["size"] = False
    return dims, expected


def _style_dict(font: str | None, size: float | None, emphasis: Emphasis | None) -> dict:
    return {
        "font": font,
        "size": size,
        "bold": emphasis[0] if emphasis else None,
        "italic": emphasis[1] if emphasis else None,
        "underline": emphasis[2] if emphasis else None,
    }


_SINGLE_DEVIATION: dict[str, Literal["font", "size", "emphasis"]] = {
    "font": "font", "size": "size", "emphasis": "emphasis",
}


def _raw_anomaly(
    run: _Run, dims: _Dims, expected: _Expectation, *, fragment: _Span | None = None
) -> _RawAnomaly:
    contributing = [dim for dim in ("font", "size", "emphasis") if dims[dim]]
    deviation_type = "combo" if len(contributing) >= 2 else _SINGLE_DEVIATION[contributing[0]]
    if fragment is not None:
        bbox, text = fragment.bbox, fragment.text
        detected = _style_dict(fragment.font, fragment.size, (fragment.bold, fragment.italic, fragment.underline))
    else:
        bbox, text = run.bbox, run.text
        detected = _style_dict(run.font, run.size, run.emphasis)
    return _RawAnomaly(
        page=run.page,
        bbox=bbox,
        text=text,
        deviation_type=deviation_type,
        detected=detected,
        expected=_style_dict(expected.font, expected.size, expected.emphasis),
    )


def _is_salutation(run: _Run) -> bool:
    """'Yours faithfully,' — a letter's closing line is set apart on purpose."""
    return run.text.rstrip().endswith(",") and len(run.text.split()) <= 4


def _peer_deviations(runs: list[_Run]) -> list[_RawAnomaly]:
    """Each run against the neighbours stacked in its own column.

    A style that recurs in the same column is a tier of the document (a
    bold name above each address), so only a style that stands alone in its
    column counts as a deviation.
    """
    peers = [run for run in runs if not run.signature and not run.heading and not _is_salutation(run)]
    rows: dict[RowKey, list[_Run]] = {}
    for run in runs:
        if not run.signature:
            rows.setdefault(run.row_key, []).append(run)

    # Per run: the verdict from every column it sits in (left edge, right edge).
    verdicts: dict[_Run, list[tuple[_Dims, _Expectation]]] = {}
    for edge in ("left", "right"):
        for column in _columns(peers, edge):
            for chain in _chains(column):
                for index, run in enumerate(chain):
                    verdict = _chain_deviations(chain, index)
                    if verdict is None:
                        continue
                    dims, expected = verdict
                    for dim in ("font", "size", "emphasis"):
                        if dims[dim] and any(_same_on(run, other, dim) for other in column if other is not run):
                            dims[dim] = False
                    verdicts.setdefault(run, []).append((dims, expected))

    raw: list[_RawAnomaly] = []
    for run in peers:
        if run not in verdicts:
            continue
        mates = [m for m in rows[run.row_key] if m is not run]
        dims: _Dims = {}
        for dim in ("font", "size", "emphasis"):
            # Every column that could judge the run has to agree it deviates.
            deviates = all(v[0][dim] for v in verdicts[run])
            dims[dim] = deviates and not _explained_by_row(run, dim, mates)
        expected_emphasis = verdicts[run][0][1].emphasis
        if (
            dims["emphasis"]
            and expected_emphasis is not None
            and run.emphasis[:2] == expected_emphasis[:2]
            and len(run.text.split()) >= _UNDERLINED_SENTENCE_WORDS
        ):
            dims["emphasis"] = False
        if any(dims.values()):
            raw.append(_raw_anomaly(run, dims, verdicts[run][0][1]))
    return raw


def _mark_headings(runs: list[_Run]) -> None:
    """A bold or underlined short line standing alone in its row."""
    row_sizes = Counter(run.row_key for run in runs)
    for run in runs:
        if run.signature or run.numeric or row_sizes[run.row_key] != 1:
            continue
        if len(run.text.split()) > _HEADING_MAX_WORDS or len(run.text) > _HEADING_MAX_CHARS:
            continue
        text = run.text.rstrip()
        if text.endswith((",", ";")):
            continue
        if text.endswith(".") and len(text.split()) >= _HEADING_SENTENCE_WORDS:
            continue
        if run.emphasis[0] or run.emphasis[2]:
            run.heading = True


def _heading_deviations(runs: list[_Run]) -> list[_RawAnomaly]:
    """Section headings in one column are one tier: they share a font, size and emphasis."""
    raw: list[_RawAnomaly] = []
    for column in _clusters([run for run in runs if run.heading], "left"):
        if len(column) < _HEADING_MIN_PEERS:
            continue
        min_count = _HEADING_MIN_PEERS - 1
        expected = _expect(column, min_count, _HEADING_MIN_SHARE, _HEADING_MIN_PEERS, _HEADING_SIZE_MIN_SHARE)
        title = min(column, key=lambda r: (r.page, r.bbox[1]))
        for run in column:
            dims = expected.deviations(run)
            # The first heading, set larger than the rest, is the document title.
            if run is title and expected.size is not None and run.size > expected.size:
                dims["size"] = False
            if any(dims.values()):
                raw.append(_raw_anomaly(run, dims, expected))
    return raw


# Column peers can't judge a lone value in a table cell, and a right-aligned number set wider
# leaves its column. These two checks look at the row and at the whole document instead, and only
# for a style that almost nothing else in the document uses.
_RARE_STYLE_MAX_RUNS = 2
_ROW_MIN_MATES = 2
_DOC_MIN_NUMBERS = 4
_DOC_NUMBER_FONT_SHARE = 0.75


def _row_deviations(runs: list[_Run]) -> list[_RawAnomaly]:
    """A number in a different font or size than every other cell in its row, in a style that recurs nowhere."""
    pool = [run for run in runs if not run.signature]
    rows: dict[RowKey, list[_Run]] = {}
    for run in pool:
        rows.setdefault(run.row_key, []).append(run)
    family_runs = Counter(run.family for run in pool)
    size_runs = Counter(round(run.size, 1) for run in pool)

    raw: list[_RawAnomaly] = []
    for run in pool:
        if not run.numeric or run.heading:
            continue
        mates = [m for m in rows[run.row_key] if m is not run and _alnum(m.text) >= _MIN_MATE_ALNUM]
        if len(mates) < _ROW_MIN_MATES:
            continue
        expected = _expect(mates, len(mates), 1.0, len(mates), 1.0)
        expected.emphasis = None
        dims = expected.deviations(run)
        dims["font"] = dims["font"] and family_runs[run.family] <= _RARE_STYLE_MAX_RUNS
        dims["size"] = dims["size"] and size_runs[round(run.size, 1)] <= _RARE_STYLE_MAX_RUNS
        if any(dims.values()):
            raw.append(_raw_anomaly(run, dims, expected))
    return raw


def _foreign_font_deviations(runs: list[_Run]) -> list[_RawAnomaly]:
    """A number in a typeface used by almost nothing else, where the document's numbers share one."""
    pool = [run for run in runs if not run.signature and not run.heading]
    numbers = [run for run in pool if run.numeric]
    if len(numbers) < _DOC_MIN_NUMBERS:
        return []
    dominant, count = Counter(run.family for run in numbers).most_common(1)[0]
    if count / len(numbers) < _DOC_NUMBER_FONT_SHARE:
        return []
    family_runs = Counter(run.family for run in pool)
    font = next(run.font for run in numbers if run.family == dominant)
    expected = _Expectation(family=dominant, font=font, size=None, emphasis=None)
    dims = {"font": True, "size": False, "emphasis": False}
    return [
        _raw_anomaly(run, dims, expected)
        for run in numbers
        if run.family != dominant and family_runs[run.family] <= _RARE_STYLE_MAX_RUNS
    ]


# A fragment inside one flowing line is checked for font and size only: bold,
# italic and underline inside a sentence are ordinary emphasis.
_FRAGMENT_MIN_RUN_CHARS = 12
_FRAGMENT_MAJORITY_SHARE = 0.6
_FRAGMENT_MIN_ALNUM = 3
_FRAGMENT_BASELINE_TOLERANCE_PT = 1.5


def _fragment_deviations(run: _Run) -> list[_RawAnomaly]:
    """A fragment of a sentence set in another font or size than the rest of it."""
    if len(run.spans) < 2 or run.signature:
        return []
    total = sum(len(s.text) for s in run.spans)
    if total < _FRAGMENT_MIN_RUN_CHARS:
        return []

    family_chars: Counter[str] = Counter()
    size_chars: Counter[float] = Counter()
    for span in run.spans:
        family_chars[_font_family(span.font)] += len(span.text)
        size_chars[round(span.size, 1)] += len(span.text)
    family, family_count = family_chars.most_common(1)[0]
    size, size_count = size_chars.most_common(1)[0]
    family_known = family_count / total >= _FRAGMENT_MAJORITY_SHARE
    size_known = size_count / total >= _FRAGMENT_MAJORITY_SHARE
    if not (family_known or size_known):
        return []

    main = max(
        (s for s in run.spans if _font_family(s.font) == family and round(s.size, 1) == size),
        key=lambda s: len(s.text),
        default=max(run.spans, key=lambda s: len(s.text)),
    )
    baseline = median(s.bbox[3] for s in run.spans)
    expected = _Expectation(
        family=family if family_known else None,
        font=main.font,
        size=size if size_known else None,
        emphasis=(main.bold, main.italic, main.underline),
    )
    raw: list[_RawAnomaly] = []
    for span in run.spans:
        if _alnum(span.text) < _FRAGMENT_MIN_ALNUM:
            continue
        if abs(span.bbox[3] - baseline) > _FRAGMENT_BASELINE_TOLERANCE_PT:
            continue  # a superscript, not a restyled word
        dims = {
            "font": expected.family is not None and _font_family(span.font) != expected.family,
            "size": expected.size is not None and abs(span.size - expected.size) > SIZE_TOLERANCE_PT,
            "emphasis": False,
        }
        if any(dims.values()):
            raw.append(_raw_anomaly(run, dims, expected, fragment=span))
    return raw


# --- extracted-field anchoring ----------------------------------------------


def _field_search_text(field: ExtractedField) -> str | None:
    """
    The text to look for in the PDF for a field: field_value if it is a
    string (decoding one level of JSON encoding if present), else None —
    a number, bool, list or dict has no single text span to search for.
    """
    decoded = decode_field_value(field.field_value)
    if not isinstance(decoded, str):
        return None
    decoded = decoded.strip()
    return decoded or None


# A span counts as part of a search match if it overlaps it by this fraction
# of its own area; match rects and span bboxes rarely align exactly.
_FIELD_OVERLAP_MIN_RATIO = 0.3


def _overlaps(bbox: BBox, rect: pymupdf.Rect) -> bool:
    span_rect = pymupdf.Rect(bbox)
    intersection = span_rect & rect
    if intersection.is_empty:
        return False
    span_area = span_rect.get_area()
    if span_area <= 0:
        return False
    return (intersection.get_area() / span_area) >= _FIELD_OVERLAP_MIN_RATIO


def _annotate_fields(
    raw: list[_RawAnomaly], doc: pymupdf.Document, fields: list[ExtractedField]
) -> None:
    """Names the extracted field a finding sits on, so the report can say which value it is."""
    by_page: dict[int, list[_RawAnomaly]] = {}
    for item in raw:
        by_page.setdefault(item.page, []).append(item)
    for page_index, items in by_page.items():
        page = _page(doc, page_index)
        for field in fields:
            search_text = _field_search_text(field)
            if search_text is None:
                continue
            try:
                rects = page.search_for(search_text)
            except ValueError:
                continue
            for item in items:
                if item.field_name is None and any(_overlaps(item.bbox, rect) for rect in rects):
                    item.field_name = field.field_name


# --- public API -------------------------------------------------------------


def check_font_consistency(
    document_id: str,
    file_bytes: bytes,
    extracted_fields: list[ExtractedField] | None = None,
) -> FontConsistencyResult:
    """
    Flags text styled differently from the peers that share its place in the
    document structure. Synchronous and CPU-bound — run via asyncio.to_thread.
    `extracted_fields` only names the field a finding sits on.
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

        runs = _build_runs([span for span in spans if _is_assessable(span)])
        _mark_signatures(runs, _rule_boxes(doc, spans))
        _mark_headings(runs)

        raw = _peer_deviations(runs) + _heading_deviations(runs)
        seen = {(item.page, item.bbox) for item in raw}
        for extra in _row_deviations(runs) + _foreign_font_deviations(runs):
            if (extra.page, extra.bbox) not in seen:
                seen.add((extra.page, extra.bbox))
                raw.append(extra)
        for run in runs:
            raw += _fragment_deviations(run)
        if extracted_fields:
            _annotate_fields(raw, doc, extracted_fields)

        return FontConsistencyResult(
            document_id=document_id,
            anomalies=_group_anomalies(raw),
            all_detected_styles=_compile_detected_styles(spans),
            unable_to_assess=False,
        )
    except Exception as exc:
        logger.exception("Font consistency check failed for document %s", document_id)
        reason = f"Unexpected error during font consistency check: {type(exc).__name__}: {exc}"
        return _empty_result(document_id, reason)
    finally:
        doc.close()


def _group_anomalies(raw_anomalies: list[_RawAnomaly]) -> list[FontAnomalyV2]:
    """Runs sharing a deviation signature become one entry with all their
    locations, rarest first."""
    groups: dict[tuple, list[_RawAnomaly]] = {}
    for raw in raw_anomalies:
        key = (
            raw.deviation_type,
            raw.field_name,
            tuple(sorted(raw.detected.items(), key=lambda kv: kv[0])),
            tuple(sorted(raw.expected.items(), key=lambda kv: kv[0])),
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
        )
        for items in groups.values()
    ]
    grouped.sort(key=lambda anomaly: anomaly.occurrence_count)
    return grouped
