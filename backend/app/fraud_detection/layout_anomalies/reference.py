"""Compare a document with a curated reference layout of the same form.

A standard form (an NOA, a bank's FATCA/CRS form) puts every field in the
same place. The generic checks compare a line with its neighbours, so a
designed indent and a tampered shift of the same size look alike. A
reference knows where each element belongs.

A reference holds the form's fixed labels (text and position) and the slots
its values fill (position only, so no applicant's data is stored). It is
built from two or more genuine copies of one form version by
scripts/build_layout_reference.py and stored under
references/<document_type>/<name>.json.

A reference applies only when it fits: at least FIT_MIN of its labels must
sit where it has them. Each line is measured against the offset of the
labels around it, so a scan that moves the whole page, or a longer table
that pushes a section down, is not a finding. Another issuer's or another year's form
does not fit, and the document gets only the generic checks.

Only values are reported, and only one that moved on its own: every line
around it sits where the reference has it. Labels place the document on the
reference but are never findings themselves, since a reference built from a
few copies can't know every version of a form's wording and spacing. A value
whose neighbours moved too (a longer name wrapping, an optional line pushing
the rest down) reflowed; it wasn't edited.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from statistics import median

from pydantic import BaseModel

from .common import (
    _Block, _block_for_line, _delta_confidence, LayoutAnomaly, _anomaly, _line_baseline,
    _Line, _pt, _signed_pt,
)

REFERENCES_DIR = Path(__file__).parent / "references"

# A label this far from its reference position, after the page offset, has moved.
SHIFT_PT = 6.0
# A value whose baseline and left edge, right edge, or centre are this close
# to a reference slot fills it. Born-digital forms place text to the point.
SLOT_PT = 2.0
# Labels and slots further than this from a line are not its counterpart.
REACH_PT = 60.0
# Share of the reference's labels that must be in place for it to apply.
FIT_MIN = 0.5
# Tokens shorter than this ("1", "a)") repeat across a form and are not labels.
_MIN_LABEL_CHARS = 3
# A line is measured against the offset of this many labels nearest to it.
_LOCAL_LABELS = 6
# Labels closer than this give a noisy zoom estimate, so they are not used.
_PIN_MIN_DISTANCE_PT = 30.0
_PIN_MIN_GAP_PT = 8.0
# A zoom this close to 1 is the same size (a born-digital copy).
_SAME_SIZE = 0.02
_SCALE_RANGE = (0.2, 8.0)
# This many lines off by the same amount are a reflowed section, not an edit.
_REFLOW_MIN_LINES = 3
# Two lines this close with the same offset moved together.
_REFLOW_NEIGHBOUR_PT = 40.0
# OCR boxes wobble by a point or two, so a value's slot is looser on a photo or scan.
_OCR_SLOT_FACTOR = 2.0


class ReferenceLine(BaseModel):
    page: int
    x0: float
    x1: float
    baseline: float
    # Bottom of the text box: what an OCR'd photo or scan can be compared on.
    bottom: float | None = None
    text: str | None = None  # labels only; value slots keep no text


class ReferenceLayout(BaseModel):
    name: str
    document_type: str
    labels: list[ReferenceLine]
    slots: list[ReferenceLine]


def _norm(text: str) -> str:
    text = (text or "").replace("\xa0", " ").replace("\xad", "-").replace("\x00", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _mask_numbers(text: str | None) -> str:
    """A label that carries a figure ("discharge of $40,000.00") is still that label on another document."""
    return re.sub(r"\d[\d,.]*", "#", text or "")


def _is_label_text(text: str) -> bool:
    return sum(ch.isalnum() for ch in text) >= _MIN_LABEL_CHARS


def _is_machine_readable(text: str) -> bool:
    """A passport's machine-readable zone: capitals and digits padded with '<'.

    It is data for passport readers, protected by its own check digits, not a
    printed field. OCR also boxes its monospaced type differently from the PDF.
    """
    compact = text.replace(" ", "")
    return "<<" in compact and len(compact) >= 20


_PLACEHOLDER_RE = re.compile(r"(photo|fingerprint|signature)?\s*placeholder|photo|fingerprint")


def _is_placeholder(text: str) -> bool:
    """A caption in an empty box ("PHOTO PLACEHOLDER") marks where artwork goes; it is not a field."""
    return bool(_PLACEHOLDER_RE.fullmatch(_norm(text)))


def _doc_lines(blocks: list[_Block]) -> list[tuple[_Block, _Line]]:
    return [
        (block, line)
        for block in blocks
        for line in block.lines
        if _norm(line.text) and not _is_machine_readable(line.text) and not _is_placeholder(line.text)
    ]


def _near(a_x0: float, a_base: float, b_x0: float, b_base: float) -> bool:
    return abs(a_x0 - b_x0) <= REACH_PT and abs(a_base - b_base) <= REACH_PT


def build_reference(name: str, document_type: str, copies: list[list[_Block]]) -> ReferenceLayout:
    """Labels are the text every copy has at the same place; the rest are value slots.

    Slots come from every copy, not just the first, so a value that sits differently
    in genuine copies (a name long enough to wrap, the other company type, a clause
    that runs a line longer) has each of its positions on record. Copies that cover
    those variants make a reference that doesn't flag them."""
    if len(copies) < 2:
        raise ValueError("A reference needs at least two genuine copies of the form")
    first, others = _doc_lines(copies[0]), [_doc_lines(blocks) for blocks in copies[1:]]

    def in_every_copy(line: _Line) -> bool:
        text = _norm(line.text)
        return all(
            any(
                _norm(other.text) == text
                and other.page == line.page
                and abs(other.bbox[0] - line.bbox[0]) <= SLOT_PT
                and abs(_line_baseline(other) - _line_baseline(line)) <= SLOT_PT
                for _, other in lines
            )
            for lines in others
        )

    # Text repeated on a page ("Single" in every row of a table) is a value
    # that happened to line up in these copies, not a fixed label.
    per_page = Counter((line.page, _norm(line.text)) for _, line in first)
    labels: list[ReferenceLine] = []
    slots: list[ReferenceLine] = []

    def entry(line: _Line, text: str | None) -> ReferenceLine:
        return ReferenceLine(
            page=line.page,
            x0=line.bbox[0],
            x1=line.bbox[2],
            baseline=_line_baseline(line),
            bottom=line.bbox[3],
            text=text,
        )

    def same_place(a: ReferenceLine, b: ReferenceLine) -> bool:
        # The rule a value fills a slot by: same baseline, and the same left edge, right edge, or centre.
        return (
            a.page == b.page
            and abs(a.baseline - b.baseline) <= SLOT_PT
            and min(abs(a.x0 - b.x0), abs(a.x1 - b.x1), abs((a.x0 + a.x1) / 2 - (b.x0 + b.x1) / 2)) <= SLOT_PT
        )

    def add_slot(slot: ReferenceLine) -> None:
        if not any(same_place(slot, known) for known in slots):
            slots.append(slot)

    for _, line in first:
        text = _norm(line.text)
        if _is_label_text(text) and per_page[(line.page, text)] == 1 and in_every_copy(line):
            labels.append(entry(line, text))
        else:
            add_slot(entry(line, None))

    def is_label(line: _Line) -> bool:
        text = _norm(line.text)
        return any(
            label.text == text
            and label.page == line.page
            and abs(label.x0 - line.bbox[0]) <= SLOT_PT
            and abs(label.baseline - _line_baseline(line)) <= SLOT_PT
            for label in labels
        )

    for lines in others:
        for _, line in lines:
            if not is_label(line):
                add_slot(entry(line, None))
    return ReferenceLayout(name=name, document_type=document_type, labels=labels, slots=slots)


@lru_cache(maxsize=None)
def load_references(document_type: str) -> tuple[ReferenceLayout, ...]:
    folder = REFERENCES_DIR / document_type
    if not folder.is_dir():
        return ()
    return tuple(
        ReferenceLayout.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(folder.glob("*.json"))
    )


@dataclass
class ReferenceComparison:
    reference: ReferenceLayout
    fit: float
    # Every text line on the document, and the ones that sit where the reference has them.
    lines: list[_Line]
    in_place: list[_Line] = field(default_factory=list)
    # The fixed labels among them: a value can sit on a slot by chance, a label hardly ever.
    labels_in_place: list[_Line] = field(default_factory=list)
    findings: list[LayoutAnomaly] = field(default_factory=list)

    def explains(self, anomaly: LayoutAnomaly, labels_only: bool = False) -> bool:
        """A generic finding whose lines all sit where the reference has them is the design."""

        def inside(line: _Line) -> bool:
            x0, y0, x1, y1 = anomaly.bbox
            return (
                line.page == anomaly.page - 1
                and line.bbox[0] >= x0 - 1 and line.bbox[1] >= y0 - 1
                and line.bbox[2] <= x1 + 1 and line.bbox[3] <= y1 + 1
            )

        covered = [line for line in self.lines if inside(line)]
        placed = {id(line) for line in (self.labels_in_place if labels_only else self.in_place)}
        return bool(covered) and all(id(line) in placed for line in covered)


@dataclass
class _Placed:
    """A document line in the reference's coordinates."""

    block: _Block
    line: _Line
    x0: float
    x1: float
    y: float
    text: str


def _doc_y(line: _Line) -> float:
    # OCR gives a box, not a text baseline; its bottom edge is the stable line.
    return line.baseline if line.baseline is not None else line.bbox[3]


def _ref_y(entry: ReferenceLine, ocr: bool) -> float:
    return entry.bottom if ocr and entry.bottom is not None else entry.baseline


def _scale_and_shift(
    lines: list[tuple[_Block, _Line]], reference: ReferenceLayout, ocr: bool
) -> tuple[float, float, float]:
    """Zoom and offset that map the document onto the reference.

    A photo or scan of a card is a different size from the PDF the
    reference came from. Labels whose text appears once on both pages pin
    it down: the ratio of their spacing is the zoom. A born-digital copy
    comes out at 1.0 and is left untouched.
    """
    ref_by_text: dict[tuple[int, str], ReferenceLine] = {}
    for label in reference.labels:
        ref_by_text.setdefault((label.page, label.text or ""), label)
    seen = Counter((line.page, _norm(line.text)) for _, line in lines)
    pins = [
        (line, ref_by_text[(line.page, _norm(line.text))])
        for _, line in lines
        if seen[(line.page, _norm(line.text))] == 1 and (line.page, _norm(line.text)) in ref_by_text
    ]
    # Horizontal spacing, and the gap between vertical neighbours. A longer
    # table pushes a section down and stretches one vertical gap, not these.
    ratios = []
    for i, (a, ra) in enumerate(pins):
        for b, rb in pins[i + 1 :]:
            ref_dx = abs(rb.x0 - ra.x0)
            if a.page == b.page and ref_dx >= _PIN_MIN_DISTANCE_PT:
                ratios.append(abs(b.bbox[0] - a.bbox[0]) / ref_dx)
    for page in {line.page for line, _ in pins}:
        column = sorted((p for p in pins if p[0].page == page), key=lambda p: _ref_y(p[1], ocr))
        for (a, ra), (b, rb) in zip(column, column[1:]):
            ref_dy = _ref_y(rb, ocr) - _ref_y(ra, ocr)
            if ref_dy >= _PIN_MIN_GAP_PT:
                ratios.append(abs(_doc_y(b) - _doc_y(a)) / ref_dy)
    scale = median(ratios) if ratios else 1.0
    if abs(scale - 1.0) < _SAME_SIZE or not _SCALE_RANGE[0] <= scale <= _SCALE_RANGE[1]:
        scale = 1.0
    if scale == 1.0:
        return 1.0, 0.0, 0.0
    shift_x = median(a.bbox[0] / scale - r.x0 for a, r in pins)
    shift_y = median(_doc_y(a) / scale - _ref_y(r, ocr) for a, r in pins)
    return scale, shift_x, shift_y


def compare_with_reference(
    blocks: list[_Block], reference: ReferenceLayout
) -> ReferenceComparison:
    lines = _doc_lines(blocks)
    pages = {label.page for label in reference.labels} | {slot.page for slot in reference.slots}
    ocr = bool(lines) and all(line.baseline is None for _, line in lines)
    scale, shift_x, shift_y = _scale_and_shift(lines, reference, ocr)
    placed = [
        _Placed(
            block=block,
            line=line,
            x0=line.bbox[0] / scale - shift_x,
            x1=line.bbox[2] / scale - shift_x,
            y=_doc_y(line) / scale - shift_y,
            text=_norm(line.text),
        )
        for block, line in lines
    ]

    def ry(entry: ReferenceLine) -> float:
        return _ref_y(entry, ocr)

    # Labels: same text on the same page. Closest pairs are matched first, so
    # a label goes to the occurrence that sits nearest its reference position.
    candidates = sorted(
        (abs(label.x0 - p.x0) + abs(ry(label) - p.y), index, line_index)
        for line_index, p in enumerate(placed)
        if p.line.page in pages and _is_label_text(p.text)
        for index, label in enumerate(reference.labels)
        if label.page == p.line.page
        and _mask_numbers(label.text) == _mask_numbers(p.text)
        and _near(label.x0, ry(label), p.x0, p.y)
    )
    pairs: list[tuple[_Placed, ReferenceLine]] = []
    used_labels: set[int] = set()
    used_lines: set[int] = set()
    for _, index, line_index in candidates:
        if index in used_labels or line_index in used_lines:
            continue
        used_labels.add(index)
        used_lines.add(line_index)
        pairs.append((placed[line_index], reference.labels[index]))

    # How far each matched label sits from its reference position.
    moves = [(p, label, p.x0 - label.x0, p.y - ry(label)) for p, label in pairs]

    def local_offset(page: int, x: float, y: float, skip: _Placed | None = None) -> tuple[float, float]:
        """Offset of the labels around a point. A scan moves the whole page, and a
        longer table moves everything under it; both move the nearby labels too.
        One moved field does not move its neighbours."""
        near = sorted(
            (m for m in moves if m[0].line.page == page and m[0] is not skip),
            key=lambda m: abs(m[1].x0 - x) + abs(ry(m[1]) - y),
        )[:_LOCAL_LABELS]
        if not near:
            return 0.0, 0.0
        return median(m[2] for m in near), median(m[3] for m in near)

    shift_pt = SHIFT_PT
    slot_pt = SLOT_PT * (_OCR_SLOT_FACTOR if ocr else 1.0)
    moved = []
    for p, label in pairs:
        dx0, dy0 = local_offset(p.line.page, label.x0, ry(label), skip=p)
        moved.append((p, label, p.x0 - label.x0 - dx0, p.y - ry(label) - dy0))
    in_place_labels = [p.line for p, _, dx, dy in moved if max(abs(dx), abs(dy)) < shift_pt]
    fit = len(in_place_labels) / len(reference.labels) if reference.labels else 0.0
    comparison = ReferenceComparison(
        reference=reference, fit=fit, lines=[line for _, line in lines]
    )
    if fit < FIT_MIN:
        return comparison

    comparison.in_place.extend(in_place_labels)
    comparison.labels_in_place.extend(in_place_labels)
    off_place: list[tuple[_Placed, float, float, str]] = [
        (p, dx, dy, "label") for p, _, dx, dy in moved if max(abs(dx), abs(dy)) >= shift_pt
    ]

    # Values fill a slot: same baseline, and the same left edge, right edge, or centre.
    labelled = {id(p.line) for p, _ in pairs}

    for p in placed:
        if id(p.line) in labelled or p.line.page not in pages:
            continue
        # A stray full stop or bullet split off by extraction is not a value.
        if not any(ch.isalnum() for ch in p.line.text):
            continue
        dx0, dy0 = local_offset(p.line.page, p.x0, p.y)
        x0, x1, base = p.x0 - dx0, p.x1 - dx0, p.y - dy0
        near = [
            slot
            for slot in reference.slots
            if slot.page == p.line.page and abs(ry(slot) - base) <= REACH_PT
        ]
        if not near:
            continue

        def edge_gap(slot: ReferenceLine) -> float:
            return min(
                abs(slot.x0 - x0),
                abs(slot.x1 - x1),
                abs((slot.x0 + slot.x1) / 2 - (x0 + x1) / 2),
            )

        if any(edge_gap(slot) <= slot_pt and abs(ry(slot) - base) <= slot_pt for slot in near):
            comparison.in_place.append(p.line)
            continue
        overlapping = [slot for slot in near if min(slot.x1, x1) - max(slot.x0, x0) > 0] or near
        slot = min(overlapping, key=lambda s: abs(ry(s) - base) + edge_gap(s))
        dx = min(
            (x0 - slot.x0, x1 - slot.x1, (x0 + x1 - slot.x0 - slot.x1) / 2), key=abs
        )
        off_place.append((p, dx, base - ry(slot), "value"))
    in_place_ids = {id(line) for line in comparison.in_place}
    assessed = [(p, id(p.line) in in_place_ids) for p in placed if id(p.line) in in_place_ids] + [
        (p, False) for p, *_ in off_place
    ]
    comparison.findings.extend(
        _reference_anomaly(p.block, p.line, reference, dx, dy, fit, role)
        for p, dx, dy, role in off_place
        if role == "value"
        and _stands_alone(p, assessed)
        and not _moved_together(p, dx, dy, off_place, slot_pt)
    )
    return comparison


def _stands_alone(placed: _Placed, assessed: list[tuple[_Placed, bool]]) -> bool:
    """The value moved on its own: every line around it that was compared with the reference
    (within _REFLOW_NEIGHBOUR_PT above or below, on its page) sits where the reference has it,
    and there is at least one. If a neighbour moved too, by any amount, the section reflowed:
    a long value wrapped, or an optional line pushed the rest down."""
    neighbours = [
        in_place
        for other, in_place in assessed
        if other is not placed
        and other.line.page == placed.line.page
        and abs(other.y - placed.y) <= _REFLOW_NEIGHBOUR_PT
    ]
    return bool(neighbours) and all(neighbours)


def _moved_together(
    placed: _Placed,
    dx: float,
    dy: float,
    off_place: list[tuple[_Placed, float, float, str]],
    tolerance: float,
) -> bool:
    """Lines that are all off by the same amount reflowed; they were not edited.

    A different company's text runs a line longer, or a table has more rows,
    so a whole section sits a line or two lower. Every line in it shows the
    same offset. An edit moves one field. Two neighbouring lines with the
    same offset, or three anywhere on the page, are a reflow.
    """
    same = [
        other
        for other, other_dx, other_dy, _ in off_place
        if other is not placed
        and other.line.page == placed.line.page
        and abs(other_dx - dx) <= tolerance
        and abs(other_dy - dy) <= tolerance
    ]
    return len(same) >= _REFLOW_MIN_LINES - 1 or any(
        abs(other.y - placed.y) <= _REFLOW_NEIGHBOUR_PT for other in same
    )


# Document-type words that are written as acronyms.
_ACRONYMS = {"nric": "NRIC", "fatca": "FATCA", "crs": "CRS", "noa": "NOA"}


def _layout_name(reference: ReferenceLayout) -> str:
    """How a finding names the reference: by its document type ("standard NRIC"), never by the
    reference file's own name (generated_nric), which means nothing to an officer."""
    words = [_ACRONYMS.get(w, w) for w in reference.document_type.split("_")]
    return "standard " + " ".join(words)


def _reference_anomaly(
    block: _Block,
    line: _Line,
    reference: ReferenceLayout,
    dx: float,
    dy: float,
    fit: float,
    role: str,
) -> LayoutAnomaly:
    horizontal = abs(dx) >= abs(dy)
    delta = dx if horizontal else dy
    evidence = {
        "kind": "reference-position",
        "reference": _layout_name(reference),
        "element": role,
        "dx_pt": round(dx, 2),
        "dy_pt": round(dy, 2),
        "delta_pt": round(delta, 2),
        "reference_fit": round(fit, 2),
    }
    return _anomaly(
        "Indentation" if horizontal else "Spacing",
        _block_for_line(block, line),
        detected=f"{_signed_pt(dx)} across, {_signed_pt(dy)} down from its reference position",
        expected=f"Where the {_layout_name(reference)} layout places this {role}",
        baseline=f"{round(fit * 100)}% of the reference's labels sit in place on this document",
        deviation=_signed_pt(delta),
        confidence=_delta_confidence(abs(delta), SHIFT_PT / 2, fit),
        evidence=evidence,
        location=f"Reference position near y={round(line.bbox[1])} ({_pt(abs(delta))} off)",
    )


def fitting_references(blocks: list[_Block], document_type: str) -> list[ReferenceComparison]:
    """Every reference of this type that fits, best first: highest fit, then fewest findings.

    Of two versions that fit equally, the one the document disagrees with
    least is the version it was printed from.
    """
    comparisons = [compare_with_reference(blocks, ref) for ref in load_references(document_type)]
    fitting = [c for c in comparisons if c.fit >= FIT_MIN]
    return sorted(fitting, key=lambda c: (-c.fit, len(c.findings)))


def best_reference(blocks: list[_Block], document_type: str) -> ReferenceComparison | None:
    """The best-fitting reference of this type, if one fits."""
    fitting = fitting_references(blocks, document_type)
    return fitting[0] if fitting else None
