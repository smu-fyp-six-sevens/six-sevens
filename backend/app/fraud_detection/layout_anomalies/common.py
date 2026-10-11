"""Shared types, thresholds, result models, and finding helpers for layout anomaly checks."""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Iterable, Literal, TypeVar

from pydantic import BaseModel, Field, model_serializer


logger = logging.getLogger("fraud_detection.layout_anomalies")

_T = TypeVar("_T")

BBox = tuple[float, float, float, float]
Alignment = Literal["left-aligned", "centred", "right-aligned", "justified"]
AnomalyType = Literal[
    "Indentation",
    "Alignment",
    "Margin",
    "Spacing",
    "Text Overlay",
]
# Finer than a single body bucket. A heading, list item, or label is only
# compared with blocks that serve the same role.
StructuralRole = Literal[
    "heading",
    "body",
    "list_item",
    "label_value",
    "table",
    "address",
    "date",
    "signature",
    "header",
    "footer",
    "cover",
]
ParagraphType = StructuralRole

# A left/right indent this far (or further) from comparable neighbours is a
# tamper signal, not typesetting noise. 12pt ≈ 4.2mm — below the 6–12mm
# clause-block shifts the constitution generator injects as "misalignment".
INDENT_THRESHOLD_PT = 12.0

# Page margins this far from the document's established pattern are
# treated as a different layout, not printer variance.
MARGIN_THRESHOLD_PT = 18.0
# The right inset is only a margin when a body line fills the page.
# Below this, the longest line is the content, not the margin.
_FULL_LINE_WIDTH_RATIO = 0.55

# Running header/footer chrome this far from the document's typical
# y-position is a swapped page template, not a clause title that merely
# sits in the header band.
CHROME_POS_THRESHOLD_PT = 12.0

# Side-by-side items on the same visual row (label left, amount right)
# whose top or baseline differs by this much are treated as vertically
# misaligned — the check the horizontal left/centre/right classifier
# does not cover.
ROW_ALIGN_THRESHOLD_PT = 6.0

# On a title/cover page (most lines are short, stacked, and share a
# centre axis) a line whose midpoint is this far off that axis is a
# shoved heading — e.g. a company name sitting ~47pt right of the
# other cover titles. Not applied to full-width clause pages.
CENTER_SHIFT_THRESHOLD_PT = 18.0
_TITLE_PAGE_SHORT_RATIO = 0.55
_TITLE_PAGE_SHORT_FRACTION = 0.70
_CENTER_MID_BUCKET_PT = 2.0

# Intra-paragraph line leading this far from the page's typical leading
# is a typesetting break (inserted line or a restyled pasted clause).
LINE_SPACING_THRESHOLD_PT = 4.0

# Gap between consecutive body paragraphs this far from the local
# pattern is a paragraph-spacing break. A gap of five times the typical
# rhythm, and at least 40pt, is a section break, not an insertion.
PARA_SPACING_THRESHOLD_PT = 8.0
# A table's row gaps and an identity card's field gaps are set by the form,
# not by a typesetting rhythm, so a gap that differs from its neighbours
# there is the layout. Indent and alignment still apply to these roles.
_NO_RHYTHM_ROLES = {"table", "cover"}
# A line that overlaps another by less than this fraction of the shorter
# box is ascent meeting descent, not a collision.
_COLLISION_MIN_OVERLAP_FRACTION = 0.5
# Vertical shove this far from the surrounding rhythm. Collisions are
# scored from how much of the box actually overlaps, not from this floor.
VERTICAL_SHIFT_THRESHOLD_PT = 8.0

# How close a line's edge must sit to the content column to count as flush.
ALIGN_EDGE_TOLERANCE_PT = 8.0
# Leftover left/right within this of each other → centred rather than a
# slightly-indented left-aligned line.
CENTER_SYMMETRY_TOLERANCE_PT = 12.0
# A line filling this much of the content column is full-width (justified
# or a long left-aligned line that happens to wrap flush).
JUSTIFY_FILL_RATIO = 0.90
# Blocks narrower than this fraction of the content column are "short"
# (titles, dates, labels) and are only compared to other short blocks.
SHORT_WIDTH_RATIO = 0.55

# Header/footer bands as a fraction of page height — running titles and
# page numbers live here and are not comparable to body copy.
HEADER_RATIO = 0.10
FOOTER_RATIO = 0.08

MIN_BLOCK_CHARS = 12
_TEXT_SNIPPET_CHARS = 200
_INDENT_BUCKET_PT = 1.0
_LOCAL_WINDOW = 2
# Lower page band (but above the footer strip) where a short "Signed by…"
# block is a signature, not body copy to compare against paragraphs.
_SIGNATURE_ZONE_RATIO = 0.65
_SIGNATURE_RE = re.compile(
    r"\b(signed|signature|signatory|witnesseth|witness|director|"
    r"authorised signatory|authorized signatory|for and on behalf)\b",
    re.IGNORECASE,
)
# "2. REGISTERED OFFICE" / "1. NAME" — not "5.1 The issued share capital…".
_CLAUSE_HEADING_RE = re.compile(
    r"^\d+(?:\.\d+)*\.?\s+[A-Z0-9][A-Z0-9 ,/&'().:—–-]{1,}$"
)
# "(a) NRIC", "b)", "• item", or a dash bullet. Numbered clause titles are
# headings, handled before this pattern.
_LIST_MARKER_RE = re.compile(
    r"^(?:"
    r"\([A-Za-z0-9]{1,4}\)"
    r"|[A-Za-z]\)"
    r"|[•●▪◦]\s*"
    r"|[-–—]\s+"
    r")\s*\S"
)
_NUMBERED_ITEM_RE = re.compile(r"^\d{1,2}[.)]\s+\S")
# "Name: John Tan" / "NRIC: S1234567D" — a short label, not a sentence
# that happens to contain a colon.
_LABEL_VALUE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9 /().'#-]{0,32}:\s*\S")
_ADDRESS_HINT_RE = re.compile(
    r"(?:"
    r"\b(?:road|street|avenue|drive|lane|boulevard|crescent|close|walk|way|park)\b"
    r"|#\d{1,4}-\d{1,4}"
    r"|\b\d{6}\b"
    r")",
    re.IGNORECASE,
)
_DATE_LINE_RE = re.compile(
    r"^(?:"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{2,4}"
    r")(?:\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)?$",
    re.IGNORECASE,
)
# Consecutive list markers further apart than this belong to another list.
_LIST_RUN_GAP_PT = 36.0
_COARSE_ROLES = {"header", "footer", "table", "signature", "heading", "cover"}
# Neighbouring flags only count as a local insertion stretch when both
# boxes are full-width clause columns, not short titles or cover lines.
_CLAUSE_STRETCH_MIN_WIDTH_PT = 330.0
# A measurement that only just clears its threshold scores near zero.
# Anything weaker than this is noise. The old delta score sat near 0.4
# at the threshold, which made that noise look like a finding.
_SIGNAL_STRENGTH_FLOOR = 0.40
# One check that lands on most of the document is the layout, not a tamper.
_EVERYWHERE_MIN_REGIONS = 3
_EVERYWHERE_PAGE_FRACTION = 0.75
_EVERYWHERE_REGION_FRACTION = 0.50
# The report keeps the strongest regions. Further hits are the same noise.
_MAX_FINDINGS_PER_DOCUMENT = 10
# Findings below this are not reported. Measured on 477 tampered and 45
# untouched documents: 0.70 removes a third of the false positives (most
# side findings score 0.58-0.67) for 3% of caught shifts.
REPORT_MIN_CONFIDENCE = 0.70


class LayoutAnomaly(BaseModel):
    """
    One affected area (same page and bbox), not one check.

    `anomaly_type` is the strongest check: Indentation, Alignment, Margin,
    or Spacing. Extra checks on the same area are `evidence.signals`, not
    extra rows.     Neighbouring full-width flagged clauses set `local_pattern` and
    `consecutive_run` on this finding; they need not share anomaly_type.
    `detected` / `expected` / `deviation` hold the measurements on the
    object. The JSON body keeps `summary` and a `checks` list
    (check, detected, expected, difference) and leaves out the repeated
    evidence dict. `bbox` / `text` are for highlighting.
    """

    anomaly_type: AnomalyType
    page: int
    location: str
    bbox: BBox
    text: str
    detected: str
    expected: str
    baseline: str | None = None
    deviation: str
    confidence: float
    evidence: dict
    summary: str

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """HTTP body is one finding with a short check list.

        The newline-joined detected/expected strings and the raw evidence
        dict repeat the same measurements, so they stay on the object for
        the checkers and drop out of the JSON.
        """
        del handler
        signals = self.evidence.get("signals")
        if not isinstance(signals, list) or not signals:
            signals = [_signal_record(self)]
        return {
            "type": self.anomaly_type,
            "page": self.page,
            "where": self.location,
            "text": self.text,
            "summary": self.summary,
            "confidence": self.confidence,
            "checks": [_readable_check(signal) for signal in signals if isinstance(signal, dict)],
        }


class CheckAssessment(BaseModel):
    """AC5: the relevant check is marked Unable to Assess when its inputs
    cannot be extracted or there isn't enough comparable content."""

    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class PageMargins(BaseModel):
    page: int
    left: float
    right: float
    top: float
    bottom: float
    page_width: float
    page_height: float


class LayoutElement(BaseModel):
    """
    Per-paragraph baseline used to form comparable groups. Headers are
    not compared to body copy, so a centred company name is not an
    Alignment anomaly just because the clauses under it are left-aligned.
    """

    page: int
    bbox: BBox
    left: float
    top: float
    right: float
    bottom: float
    alignment: Alignment | None = None
    paragraph_type: ParagraphType
    font: str | None = None
    size: float | None = None
    text: str


class LayoutAnomalyResult(BaseModel):
    """
    Response for GET /documents/{id}/detection/layout-anomalies.

    Each of indentation / alignment / margin / spacing can be Unable to
    Assess while the other families still return findings. Empty anomalies
    with unable_to_assess=false means the checks ran and nothing exceeded
    a threshold — not that the document is free of fraud.
    """

    document_id: str
    anomalies: list[LayoutAnomaly]
    # Full paragraph inventory and per-page margin tables stay available on
    # the result object. They are omitted from the JSON so a finding is
    # readable without scrolling past every line on the document.
    elements: list[LayoutElement] = Field(default_factory=list, exclude=True)
    page_margins: list[PageMargins] = Field(exclude=True)
    elements_analysed: int = Field(exclude=True)
    indentation: CheckAssessment
    alignment: CheckAssessment
    margin: CheckAssessment
    spacing: CheckAssessment
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class _Line(BaseModel):
    text: str
    bbox: BBox
    page: int  # 0-indexed
    page_width: float
    page_height: float
    font: str | None = None
    size: float | None = None
    # Text baseline from span["origin"][1]. Bbox y0/y1 include ascent and
    # descent, so mixed font sizes on one baseline do not share those edges.
    baseline: float | None = None


class _Block(BaseModel):
    lines: list[_Line]
    bbox: BBox
    page: int
    page_width: float
    page_height: float
    text: str
    role: ParagraphType
    font: str | None = None
    size: float | None = None
    alignment: Alignment | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def char_count(self) -> int:
        return len(self.text.replace(" ", ""))


def _empty_result(document_id: str, reason: str) -> LayoutAnomalyResult:
    unable = CheckAssessment(unable_to_assess=True, unable_to_assess_reason=reason)
    return LayoutAnomalyResult(
        document_id=document_id,
        anomalies=[],
        elements=[],
        page_margins=[],
        elements_analysed=0,
        indentation=unable,
        alignment=unable,
        margin=unable,
        spacing=unable,
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def _assessed() -> CheckAssessment:
    return CheckAssessment(unable_to_assess=False, unable_to_assess_reason=None)


def _cannot(reason: str) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    """No findings, and the check is Unable to Assess for this reason."""
    return [], CheckAssessment(unable_to_assess=True, unable_to_assess_reason=reason)


def _by_page(blocks: Iterable[_Block]) -> dict[int, list[_Block]]:
    """Blocks per 0-indexed page, in the order they were given."""
    pages: dict[int, list[_Block]] = {}
    for block in blocks:
        pages.setdefault(block.page, []).append(block)
    return pages


def _lines_by_page(
    blocks: list[_Block], min_alnum: int = 0
) -> dict[int, list[tuple[_Block, _Line]]]:
    """Every line per 0-indexed page with the block it belongs to."""
    pages: dict[int, list[tuple[_Block, _Line]]] = {}
    for block in blocks:
        for line in block.lines:
            if sum(char.isalnum() for char in line.text) >= min_alnum:
                pages.setdefault(line.page, []).append((block, line))
    return pages


def _clip(text: str, limit: int = _TEXT_SNIPPET_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _edge_anomaly(
    block: _Block,
    *,
    side: Literal["left", "right"],
    detected: float,
    expected: float,
    signed: float,
    comparable: int,
    matching: int,
    baseline: str | None,
    confidence: float,
    location: str | None = None,
    kind: str | None = None,
    expected_left: float | None = None,
    unanchored: bool = False,
) -> LayoutAnomaly:
    """An Indentation finding on a left indent or a right edge.

    The evidence keys match what the indentation and right-edge checks
    have always reported, so summaries and signals read the same.
    """
    if kind is None:
        kind = "indentation" if side == "left" else "right-edge"
    if kind == "indentation":
        evidence: dict = {
            "kind": "indentation",
            "side": side,
            "left_indent_pt": round(block.x0, 2),
            "expected_left_indent_pt": round(expected if expected_left is None else expected_left, 2),
            "left_delta_pt": round(signed, 2),
        }
    else:
        evidence = {
            "kind": "right-edge",
            "side": "right",
            "right_edge_pt": round(detected, 2),
            "expected_right_edge_pt": round(expected, 2),
            "right_delta_pt": round(signed, 2),
        }
    evidence["comparable_count"] = comparable
    evidence["matching_count"] = matching
    if unanchored:
        evidence["unanchored"] = True
    return _anomaly(
        "Indentation",
        block,
        detected=_pt(detected),
        expected=_pt(expected),
        baseline=baseline,
        deviation=_signed_pt(signed),
        confidence=confidence,
        evidence=evidence,
        location=location,
    )


def _as_bbox(raw) -> BBox | None:
    if not raw or len(raw) != 4:
        return None
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def _union_bbox(bboxes) -> BBox:
    xs0, ys0, xs1, ys1 = zip(*bboxes)
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _has_strong_majority(matching: int, comparable: int) -> bool:
    """Enough peers share a pattern that an outlier is not a coin flip."""
    if comparable < 2:
        return False
    return matching >= max(3, (comparable + 1) // 2 + 1)


def _block_for_line(block: _Block, line: _Line) -> _Block:
    return block.model_copy(update={"lines": [line], "bbox": line.bbox, "text": line.text})


def _line_width(line: _Line) -> float:
    return line.bbox[2] - line.bbox[0]


def _line_baseline(line: _Line) -> float:
    return line.baseline if line.baseline is not None else line.bbox[3]


def _x_overlap(a: _Line, b: _Line) -> float:
    return min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0])


def _letters_in(line: _Line) -> int:
    return len(line.text.replace(" ", "").replace("\xa0", ""))


def _signal_kind(anomaly: LayoutAnomaly) -> str:
    return _evidence_kind(anomaly.anomaly_type, anomaly.evidence)


def _signal_record(anomaly: LayoutAnomaly) -> dict:
    ev = {
        k: v
        for k, v in anomaly.evidence.items()
        if k not in {"signals", "signal_count", "local_pattern", "consecutive_run"}
    }
    kind = _signal_kind(anomaly)
    return {
        "kind": kind,
        "anomaly_type": anomaly.anomaly_type,
        "detected": anomaly.detected,
        "expected": anomaly.expected,
        "deviation": anomaly.deviation,
        "confidence": anomaly.confidence,
        **ev,
    }


def _readable_check(signal: dict) -> dict:
    """One check, without the numeric fields that repeat detected/expected."""
    check = {
        "check": str(signal.get("kind") or signal.get("anomaly_type") or "check").replace("-", " "),
        "detected": signal.get("detected"),
        "expected": signal.get("expected"),
        "difference": signal.get("deviation"),
    }
    comparable = signal.get("comparable_count")
    matching = signal.get("matching_count")
    if isinstance(comparable, int) and isinstance(matching, int):
        check["matches"] = f"{matching} of {comparable}"
    pages = signal.get("pages_analysed")
    agreeing = signal.get("pages_matching_baseline")
    if isinstance(pages, int) and isinstance(agreeing, int):
        check["matches"] = f"{agreeing} of {pages} pages"
    return {key: value for key, value in check.items() if value is not None}


def _opening(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())[:60]


_DEVIATION_KEYS = (
    "left_delta_pt",
    "right_delta_pt",
    "leading_delta_pt",
    "gap_delta_pt",
    "vertical_delta_pt",
    "mid_delta_pt",
    "delta_pt",
    "top_delta_pt",
    "baseline_delta_pt",
)
# A short line can be right-aligned by design and still start off the body column.
_SHORT_LINE_PAGE_RATIO = 0.40


def _bbox_area(bbox: BBox) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection_area(a: BBox, b: BBox) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def _snippet(text: str, limit: int) -> str:
    return _clip(text.strip(), limit)


def _anomaly(
    anomaly_type: AnomalyType,
    block: _Block,
    *,
    detected: str,
    expected: str,
    baseline: str | None,
    deviation: str,
    confidence: float,
    evidence: dict,
    location: str | None = None,
) -> LayoutAnomaly:
    return LayoutAnomaly(
        anomaly_type=anomaly_type,
        page=block.page + 1,
        location=location or _location_label(block),
        bbox=tuple(round(v, 2) for v in block.bbox),  # type: ignore[arg-type]
        text=_clip(block.text),
        detected=detected,
        expected=expected,
        baseline=baseline,
        deviation=deviation,
        confidence=confidence,
        evidence=evidence,
        summary=_format_summary(anomaly_type, evidence, deviation=deviation),
    )


_ALIGNMENT_LABEL: dict[Alignment, str] = {
    "left-aligned": "Left-aligned",
    "centred": "Centre-aligned",
    "right-aligned": "Right-aligned",
    "justified": "Justified",
}

_ROLE_LABEL = {
    "body": "Body paragraph",
    "header": "Header",
    "footer": "Footer",
    "table": "Table cell",
    "signature": "Signature block",
    "heading": "Clause heading",
    "cover": "Cover title",
    "list_item": "List item",
    "label_value": "Label",
    "address": "Address",
    "date": "Date",
}


def _location_label(block: _Block) -> str:
    role = _ROLE_LABEL.get(block.role, "Paragraph")
    return f"{role} near y={round(block.y0)}"


def _pt(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded} pt"
    return f"{round(value, 1)} pt"


def _signed_pt(delta: float) -> str:
    rounded = round(delta)
    if abs(delta - rounded) < 0.05:
        return f"{rounded:+d} pt"
    return f"{delta:+.1f} pt"


def _pct(matching: int, comparable: int) -> str:
    if comparable <= 0:
        return "0%"
    return f"{round(100 * matching / comparable)}%"


def _majority_confidence(matching: int, comparable: int) -> float:
    if comparable <= 0:
        return 0.5
    majority_frac = matching / comparable
    rarity = 1.0 - 1.0 / comparable
    return round(min(1.0, max(0.05, 0.45 * majority_frac + 0.35 * rarity + 0.2)), 2)


def _delta_confidence(delta_pt: float, threshold_pt: float, majority_frac: float) -> float:
    """How far past the threshold the measurement is.

    Zero at the threshold. Peer agreement scales that excess. It does not
    add a flat score, so a hit of one point over the line stays near zero
    instead of looking like a 0.4 finding.
    """
    span = max(threshold_pt, 1.0)
    strength = min(1.0, max(0.0, (abs(delta_pt) - threshold_pt) / span))
    agreement = min(1.0, max(0.0, majority_frac))
    return round(min(1.0, strength * (0.45 + 0.55 * agreement)), 2)


def _format_summary(
    anomaly_type: AnomalyType,
    evidence: dict,
    *,
    deviation: str = "",
) -> str:
    """One sentence for a UI. Measurements stay in structured fields."""
    kind = _evidence_kind(anomaly_type, evidence)
    delta = _abs_pt_phrase(evidence, deviation)
    sentence = _summary_sentence(kind, evidence, delta)

    signals = evidence.get("signals") or []
    extra = [item for item in signals if item.get("kind") and item.get("kind") != kind]
    if extra:
        sentence += " Other layout measurements on this same area support the finding."

    run = evidence.get("consecutive_run")
    if evidence.get("local_pattern") and isinstance(run, int) and run >= 2:
        sentence += f" Similar issues appear across {run} consecutive paragraphs."
    return sentence


def _evidence_kind(anomaly_type: AnomalyType, evidence: dict) -> str:
    if evidence.get("kind"):
        return str(evidence["kind"])
    if evidence.get("axis") == "vertical":
        return "row-alignment"
    if anomaly_type == "Indentation":
        return "indentation"
    if anomaly_type == "Alignment" and evidence.get("alignment"):
        return "alignment"
    if anomaly_type == "Margin" and evidence.get("side"):
        return f"{evidence['side']}-margin"
    return anomaly_type.lower()


def _abs_pt_phrase(evidence: dict, deviation: str) -> str:
    for key in _DEVIATION_KEYS:
        value = evidence.get(key)
        if isinstance(value, (int, float)):
            return _pt(abs(float(value)))
    first = (deviation or "").split("\n")[0].strip()
    if ": " in first:
        first = first.split(": ", 1)[1]
    cleaned = first.replace("+", "").replace("-", "").strip()
    return cleaned or "an unusual amount"


def _label_alignment(value: object) -> str:
    if value in _ALIGNMENT_LABEL:
        return _ALIGNMENT_LABEL[value]  # type: ignore[index]
    return str(value).replace("-", " ")


def _summary_sentence(kind: str, evidence: dict, delta: str) -> str:
    if kind == "indentation":
        signed = evidence.get("left_delta_pt")
        if isinstance(signed, (int, float)) and signed < 0:
            return (
                f"This paragraph starts {delta} further left than comparable "
                "body paragraphs."
            )
        return (
            f"This paragraph is indented {delta} further than comparable "
            "body paragraphs."
        )
    if kind == "right-edge":
        signed = evidence.get("right_delta_pt")
        if isinstance(signed, (int, float)) and signed < 0:
            return f"This paragraph ends {delta} short of the comparable column edge."
        return f"This paragraph extends {delta} past the comparable column edge."
    if kind == "alignment":
        detected = _label_alignment(evidence.get("alignment") or "differently aligned")
        expected = evidence.get("expected_alignment")
        if expected:
            return (
                f"This paragraph is {detected.lower()} while comparable body "
                f"paragraphs are {_label_alignment(expected).lower()}."
            )
        return f"This paragraph is {detected.lower()}, unlike comparable body paragraphs."
    if kind == "row-alignment":
        return (
            f"This label and amount on the same row are vertically offset by {delta}."
        )
    if kind == "centre-shift":
        return (
            f"This title is shifted {delta} off the shared centre axis of the "
            "cover titles."
        )
    if kind == "line-spacing":
        return (
            f"Line spacing in this paragraph differs from comparable body "
            f"paragraphs by {delta}."
        )
    if kind == "vertical":
        signed = evidence.get("vertical_delta_pt")
        if isinstance(signed, (int, float)) and signed < 0:
            return f"This line sits {delta} higher than the lines around it."
        return f"This line sits {delta} lower than the lines around it."
    if kind == "paragraph-spacing":
        where = evidence.get("space")
        if where in {"before", "after"}:
            return (
                f"The gap {where} this paragraph differs from the local rhythm "
                f"by {delta}."
            )
        return f"The gap around this paragraph differs from the local rhythm by {delta}."
    if kind.endswith("-margin"):
        side = evidence.get("side") or kind.removesuffix("-margin")
        return f"The {side} page margin differs from the document pattern by {delta}."
    if kind == "reference-position":
        return (
            f"This {evidence.get('element') or 'field'} sits {delta} from where the "
            f"{evidence.get('reference') or 'reference'} layout places it."
        )
    if kind in {"header-position", "footer-position"}:
        band = "header" if kind.startswith("header") else "footer"
        return (
            f"The running {band} sits {delta} away from its position on other pages."
        )
    return f"This {kind.replace('-', ' ')} differs from comparable content by {delta}."


def _bucket_pt(value: float, bucket: float = _INDENT_BUCKET_PT) -> float:
    return round(value / bucket) * bucket


def _dominant(values: Iterable[_T]) -> _T | None:
    """
    Most common value, or None if there's a tie for first place — a tie
    means there's no real majority pattern to call "expected", so nothing
    should be flagged as deviating from one. Same rule as font_consistency.
    """
    counts = Counter(values).most_common()
    if not counts:
        return None
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        return None
    return counts[0][0]
