"""Turns raw check hits into findings: structural explanations, strength floor, merging."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import cast

from .common import (
    BBox, _bbox_area, _Block, _CLAUSE_STRETCH_MIN_WIDTH_PT, _DEVIATION_KEYS,
    _EVERYWHERE_MIN_REGIONS, _EVERYWHERE_PAGE_FRACTION, _EVERYWHERE_REGION_FRACTION,
    _format_summary, INDENT_THRESHOLD_PT, _intersection_area, LayoutAnomaly, _Line,
    _line_baseline, _line_width, _LIST_RUN_GAP_PT, _MAX_FINDINGS_PER_DOCUMENT, _opening,
    _SHORT_LINE_PAGE_RATIO, _signal_kind, _signal_record, _SIGNAL_STRENGTH_FLOOR,
    _union_bbox, _x_overlap,
)
from .extraction import (
    _Rule,
)
from .structure import (
    _is_list_marker_text, _matches_structural_pattern, repeated_structural_patterns,
)
from .anchors import (
    _ANCHOR_TOLERANCE_PT, _ANCHOR_WINDOW_PT, _ROW_MAX_SHIFT_PT, _shares_an_edge,
)


_PRIMARY_RANK = {
    "centre-shift": 0,
    "row-alignment": 1,
    "alignment": 2,
    "right-edge": 3,
    "indentation": 4,
    "vertical": 5,
    "line-spacing": 6,
    "paragraph-spacing": 7,
}


def _is_page_wide(kind: str) -> bool:
    """Page margins and the running header/footer position describe a whole page.

    Listed by name: a kind such as "reference-position" is about one field.
    """
    return kind.endswith("-margin") or kind in {"header-position", "footer-position"}


def _primary_rank(anomaly: LayoutAnomaly) -> int:
    kind = _signal_kind(anomaly)
    if kind in {"header-position", "footer-position"}:
        return 7
    if kind.endswith("-margin"):
        return 8
    return _PRIMARY_RANK.get(kind, 9)


@dataclass
class LayoutSignal:
    """One check's measurement, before it is accepted as an anomaly.

    `strength` is 0–1. `deviation` is the absolute distance in points.
    Several signals on the same region are combined later; a signal that
    the document structure explains is dropped.
    """

    type: str
    strength: float
    deviation: float
    anomaly: LayoutAnomaly


def _numeric_deviation(anomaly: LayoutAnomaly) -> float:
    for key in _DEVIATION_KEYS:
        value = anomaly.evidence.get(key)
        if isinstance(value, (int, float)):
            return abs(float(value))
    return 0.0


def _signal_family(anomaly: LayoutAnomaly) -> str:
    kind = _signal_kind(anomaly)
    if kind in {"indentation", "right-edge"}:
        return "indentation"
    if kind in {"alignment", "centre-shift"}:
        return "alignment"
    if kind == "row-alignment":
        return "row_alignment"
    if kind in {"line-spacing", "paragraph-spacing", "vertical"}:
        return "spacing"
    if _is_page_wide(kind):
        return "margin"
    return kind


def _layout_signal(anomaly: LayoutAnomaly) -> LayoutSignal:
    return LayoutSignal(
        type=_signal_family(anomaly),
        strength=anomaly.confidence,
        deviation=_numeric_deviation(anomaly),
        anomaly=anomaly,
    )


def _consistent_list_openings(blocks: list[_Block]) -> set[tuple[int, str]]:
    """List lines whose left edge matches the other lines in that list.

    Page numbers in the set are 1-based, matching anomaly.page. A line that
    sits off the list's own indent is left out so it can still be flagged.
    """
    by_page: dict[int, list[_Line]] = {}
    for block in blocks:
        for line in block.lines:
            if _is_list_marker_text(line.text):
                by_page.setdefault(line.page, []).append(line)

    explained: set[tuple[int, str]] = set()
    for lines in by_page.values():
        ordered = sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
        run: list[_Line] = []

        def _flush(items: list[_Line]) -> None:
            if len(items) < 2:
                return
            baseline = float(median(line.bbox[0] for line in items))
            agreeing = [
                line
                for line in items
                if abs(line.bbox[0] - baseline) < INDENT_THRESHOLD_PT
            ]
            if len(agreeing) < 2:
                return
            for line in agreeing:
                explained.add((line.page + 1, _opening(line.text).lower()))

        for line in ordered:
            if not run:
                run = [line]
                continue
            gap = line.bbox[1] - run[-1].bbox[3]
            if gap <= _LIST_RUN_GAP_PT:
                run.append(line)
            else:
                _flush(run)
                run = [line]
        _flush(run)
    return explained


def _text_matches_opening(text: str, opening: str) -> bool:
    sample = _opening(text).lower()
    if not sample or not opening:
        return False
    return sample == opening or sample.startswith(opening) or opening.startswith(sample)


def _block_matches_anomaly(block: _Block, anomaly: LayoutAnomaly) -> bool:
    if anomaly.page != block.page + 1:
        return False
    return all(abs(anomaly.bbox[index] - block.bbox[index]) <= 1.5 for index in range(4))


def _decide_from_signals(
    signals: list[LayoutSignal],
    blocks: list[_Block],
    rules: dict[int, list[_Rule]] | None = None,
) -> list[LayoutAnomaly]:
    """Keep a signal the structure does not explain, and that is strong enough.

    A list whose members share an indent, and any run of three or more
    neighbours with one role and one left edge, is a structural pattern.
    An indent signal on a member of that pattern is the pattern. An item
    that leaves the pattern stays.

    Strength and deviation then decide. A hit under the strength floor is
    noise. A detector that flags most of the document is describing the
    layout. Of what remains, the strongest regions are kept.
    """
    explained = _consistent_list_openings(blocks)
    patterns = repeated_structural_patterns(blocks)
    anchored = _anchored_lines(blocks, rules)
    kept: list[LayoutSignal] = []
    for signal in signals:
        anomaly = signal.anomaly
        indent_signal = signal.type == "indentation" and _signal_kind(anomaly) == "indentation"
        if indent_signal and any(
            page == anomaly.page and _text_matches_opening(anomaly.text, opening)
            for page, opening in explained
        ):
            continue
        if indent_signal and any(
            _block_matches_anomaly(member, anomaly)
            and _matches_structural_pattern(member, patterns)
            for pattern in patterns
            for member in pattern.members
        ):
            continue
        if signal.strength < _SIGNAL_STRENGTH_FLOOR:
            continue
        if _explained_by_anchors(signal, anchored):
            continue
        kept.append(signal)

    suppressed = _types_firing_everywhere(kept, blocks)
    kept = [signal for signal in kept if signal.type not in suppressed]
    kept = _cap_signals(kept)
    return [signal.anomaly for signal in kept]


# A nudged field moves the gap around it by the size of the nudge. A gap far
# beyond that is a section break the template drew on purpose.
_SECTION_BREAK_GAP_PT = 30.0
_POSITION_KINDS = {"indentation", "right-edge", "centre-shift", "alignment"}


_Anchors = dict[int, list[tuple[_Line, dict[str, bool]]]]


def _anchored_lines(
    blocks: list[_Block], rules: dict[int, list[_Rule]] | None = None
) -> _Anchors:
    """Each line on a page, and whether its left or right side lines up with a neighbour."""
    by_page: dict[int, list[tuple[_Block, _Line]]] = {}
    for block in blocks:
        for line in block.lines:
            by_page.setdefault(line.page + 1, []).append((block, line))
    result: _Anchors = {}
    for page, entries in by_page.items():
        page_rules = (rules or {}).get(page - 1)
        result[page] = []
        for block, line in entries:
            others = [
                other
                for owner, other in entries
                if owner is not block
                and 2 < abs(other.bbox[1] - line.bbox[1]) <= _ANCHOR_WINDOW_PT
            ]
            result[page].append(
                (
                    line,
                    {
                        side: _shares_an_edge(line, others, page_rules, (side,))
                        for side in ("left", "right")
                    },
                )
            )
    return result


def _explained_by_anchors(signal: LayoutSignal, anchored: _Anchors) -> bool:
    """A position finding on text that lines up with its neighbours is the design.

    A centred title or a right-aligned date does not start where the body
    starts, but it shares a centre or an edge with the lines near it. A
    moved field shares nothing. A paragraph gap far larger than a nudge is
    a section break.
    """
    anomaly = signal.anomaly
    kind = _signal_kind(anomaly)
    if anomaly.evidence.get("unanchored"):
        return False
    if kind == "paragraph-spacing":
        return signal.deviation > _SECTION_BREAK_GAP_PT
    page_lines = anchored.get(anomaly.page, [])
    if kind == "vertical":
        return signal.deviation > _ROW_MAX_SHIFT_PT or _keeps_a_row(anomaly, page_lines)
    if kind not in _POSITION_KINDS:
        return False
    inside = [
        _side_explains(kind, anomaly.evidence, line, is_anchored)
        for line, is_anchored in page_lines
        if all(abs(a - b) <= 2 or inner_side for a, b, inner_side in zip(
            anomaly.bbox,
            line.bbox,
            (
                line.bbox[0] >= anomaly.bbox[0],
                line.bbox[1] >= anomaly.bbox[1],
                line.bbox[2] <= anomaly.bbox[2],
                line.bbox[3] <= anomaly.bbox[3],
            ),
        ))
    ]
    return bool(inside) and all(inside)


def _side_explains(kind: str, evidence: dict, line: _Line, anchored: dict[str, bool]) -> bool:
    """Only the edge a finding is about can explain it.

    A left indent is explained by a shared left edge; a short right-aligned
    line also by its right edge. A paragraph called left-, right-aligned,
    or justified is explained by the edges that alignment uses. A centred
    paragraph shares the column centre with every full line, so it never is.
    """
    if kind == "centre-shift":
        return anchored["left"] or anchored["right"]
    if kind == "alignment":
        alignment = evidence.get("alignment")
        if alignment == "left-aligned":
            return anchored["left"]
        if alignment == "right-aligned":
            return anchored["right"]
        if alignment == "justified":
            return anchored["left"] and anchored["right"]
        return False
    if kind == "right-edge" or evidence.get("side") == "right":
        return anchored["right"]
    short = _line_width(line) < _SHORT_LINE_PAGE_RATIO * line.page_width
    return anchored["left"] or (short and anchored["right"])


def _keeps_a_row(anomaly: LayoutAnomaly, page_lines: list[tuple[_Line, dict[str, bool]]]) -> bool:
    """The moved-looking line still shares its baseline with another cell."""
    lines = [line for line, _ in page_lines]
    own = [
        line
        for line in lines
        if all(abs(a - b) <= 2 for a, b in zip(line.bbox, anomaly.bbox))
    ]
    return any(
        other is not line
        and _x_overlap(line, other) <= 0
        and abs(_line_baseline(other) - _line_baseline(line)) <= _ANCHOR_TOLERANCE_PT
        for line in own
        for other in lines
    )


# Checks that compare a paragraph's style with other paragraphs. A template
# mixes styles on purpose, so on their own they describe the design; they
# count when a position check flags the same text too. Alignment of a
# paragraph of two or more lines is measurable and stands alone (AC2); the
# "alignment" of one short line is mostly its width.
_NEEDS_CORROBORATION = {"alignment", "centre-shift", "line-spacing", "paragraph-spacing"}


def _corroborated(anomaly: LayoutAnomaly, blocks: list[_Block]) -> bool:
    """Keep a finding unless every signal on it is a style comparison."""
    signals = anomaly.evidence.get("signals")
    kinds = (
        {str(signal.get("kind")) for signal in signals if isinstance(signal, dict)}
        if isinstance(signals, list) and signals
        else {_signal_kind(anomaly)}
    )
    if not kinds <= _NEEDS_CORROBORATION:
        return True
    if "alignment" in kinds:
        return any(
            len(block.lines) >= 2 and _block_matches_anomaly(block, anomaly)
            for block in blocks
        )
    return False


def _types_firing_everywhere(
    signals: list[LayoutSignal], blocks: list[_Block]
) -> set[str]:
    """A check that flags most pages, or most paragraphs, is the layout."""
    if not signals:
        return set()
    pages = {block.page + 1 for block in blocks} or {signal.anomaly.page for signal in signals}
    body_count = sum(
        1 for block in blocks if block.role not in {"header", "footer", "signature"}
    )
    body_count = max(body_count, 1)
    by_type: dict[str, list[LayoutSignal]] = {}
    for signal in signals:
        by_type.setdefault(signal.type, []).append(signal)
    suppressed: set[str] = set()
    for kind, items in by_type.items():
        regions = _region_groups([item.anomaly for item in items])
        if len(regions) < _EVERYWHERE_MIN_REGIONS:
            continue
        hit_pages = {item.anomaly.page for item in items}
        covers_pages = (
            len(pages) >= _EVERYWHERE_MIN_REGIONS
            and len(hit_pages) / len(pages) >= _EVERYWHERE_PAGE_FRACTION
        )
        covers_body = len(regions) / body_count >= _EVERYWHERE_REGION_FRACTION
        if covers_pages or covers_body:
            suppressed.add(kind)
    return suppressed


def _cap_signals(signals: list[LayoutSignal]) -> list[LayoutSignal]:
    """Keep the strongest regions. One paragraph reported five ways counts once."""
    if not signals:
        return []
    regions = _region_groups([signal.anomaly for signal in signals])
    if len(regions) <= _MAX_FINDINGS_PER_DOCUMENT:
        return signals

    def rank(indexes: list[int]) -> tuple[float, float]:
        group = [signals[index] for index in indexes]
        return (
            max(item.strength for item in group),
            max(item.deviation for item in group),
        )

    chosen_regions = sorted(regions, key=rank, reverse=True)[:_MAX_FINDINGS_PER_DOCUMENT]
    chosen = {index for region in chosen_regions for index in region}
    return [signal for index, signal in enumerate(signals) if index in chosen]


_MERGEABLE_KINDS = {
    "indentation",
    "right-edge",
    "alignment",
    "row-alignment",
    "line-spacing",
    "paragraph-spacing",
    "vertical",
}


def _is_mergeable(anomaly: LayoutAnomaly) -> bool:
    return _signal_kind(anomaly) in _MERGEABLE_KINDS


def _bbox_contains(outer: BBox, inner: BBox) -> bool:
    return (
        inner[0] >= outer[0] - 2
        and inner[1] >= outer[1] - 2
        and inner[2] <= outer[2] + 2
        and inner[3] <= outer[3] + 2
        and _bbox_area(inner) < _bbox_area(outer)
    )


def _boxes_same_region(a: BBox, b: BBox) -> bool:
    """A line box inside a paragraph, or two boxes that mostly coincide."""
    if _bbox_contains(a, b) or _bbox_contains(b, a):
        return True
    inter = _intersection_area(a, b)
    if inter <= 0:
        return False
    smaller = min(_bbox_area(a), _bbox_area(b))
    return smaller > 0 and inter / smaller >= 0.5


def _same_finding_region(a: LayoutAnomaly, b: LayoutAnomaly) -> bool:
    if a.page != b.page:
        return False
    if a.bbox == b.bbox:
        return True
    return _is_mergeable(a) and _is_mergeable(b) and _boxes_same_region(a.bbox, b.bbox)


def _region_groups(anomalies: list[LayoutAnomaly]) -> list[list[int]]:
    """Indices that name the same area, in the order they were first seen."""
    count = len(anomalies)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left in range(count):
        for right in range(left):
            if _same_finding_region(anomalies[left], anomalies[right]):
                union(left, right)

    groups: dict[int, list[int]] = {}
    order: list[int] = []
    for index in range(count):
        root = find(index)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(index)
    return [groups[root] for root in order]


def _coalesce_signals(anomalies: list[LayoutAnomaly]) -> list[LayoutAnomaly]:
    """
    Several checks on the same paragraph (indent + right-edge + spacing)
    become one finding with evidence.signals, not three anomaly rows.
    A line box, a cross-block box, and the paragraph box are one area
    when they overlap. Consecutive runs stay as separate findings.
    """
    merged: list[LayoutAnomaly] = []
    for indexes in _region_groups(anomalies):
        items = [anomalies[index] for index in indexes]
        signals = [_signal_record(item) for item in items]
        primary = min(
            items,
            key=lambda a: (_primary_rank(a), -a.confidence),
        )
        primary.evidence["signals"] = signals
        primary.evidence["signal_count"] = len(signals)
        if "kind" not in primary.evidence:
            primary.evidence["kind"] = _signal_kind(primary)
        if len(items) == 1:
            merged.append(primary)
            continue
        kinds = [s["kind"] for s in signals]
        primary.detected = "\n".join(
            f"{s['kind']}: {s['deviation']}" for s in signals
        )
        primary.expected = "\n".join(
            f"{s['kind']}: {s['expected']}" for s in signals
        )
        primary.deviation = "\n".join(
            f"{s['kind']}: {s['deviation']}" for s in signals
        )
        primary.confidence = round(
            min(1.0, max(item.confidence for item in items) + 0.06 * (len(items) - 1)),
            2,
        )
        raw_bbox = _union_bbox(item.bbox for item in items)
        primary.bbox = cast(BBox, tuple(round(value, 2) for value in raw_bbox))
        if _signal_kind(primary) in {
            "indentation",
            "right-edge",
            "alignment",
            "line-spacing",
            "paragraph-spacing",
        }:
            primary.location = f"Paragraph near y={round(primary.bbox[1])}"
        elif all(kind.endswith("-margin") for kind in kinds):
            primary.location = "Page margins"
        primary.baseline = (
            f"{len(signals)} signals on this block (" + ", ".join(kinds) + ")"
        )
        primary.summary = _format_summary(
            primary.anomaly_type,
            primary.evidence,
            deviation=primary.deviation,
        )
        merged.append(primary)
    return merged


# A weaker finding this close to a stronger one is usually the same edit seen
# from a neighbour: the line beside a moved field, or the sentence it left.
_NEIGHBOUR_PT = 24.0


def _box_gap(a: BBox, b: BBox) -> float:
    """Distance between two boxes, 0 when they touch or overlap."""
    return max(
        max(0.0, max(a[0], b[0]) - min(a[2], b[2])),
        max(0.0, max(a[1], b[1]) - min(a[3], b[3])),
    )


def _merges_with_neighbours(anomaly: LayoutAnomaly) -> bool:
    """Page-wide findings (margins, running headers) and text overlay stay on their own."""
    if anomaly.anomaly_type == "Text Overlay" or not anomaly.text:
        return False
    kind = _signal_kind(anomaly)
    return not _is_page_wide(kind)


def _merge_neighbours(anomalies: list[LayoutAnomaly]) -> list[LayoutAnomaly]:
    """Fold a finding into a stronger one beside it, so one edit is one row.

    The strongest finding absorbs the weaker findings within _NEIGHBOUR_PT
    of its own box; absorbed findings do not absorb others, so a dense table
    does not chain into one finding. Confidence is not raised: two views of
    one edit are not two pieces of evidence. The absorbed checks stay in
    evidence.signals.
    """
    order = sorted(
        range(len(anomalies)),
        key=lambda i: (-anomalies[i].confidence, _primary_rank(anomalies[i]), i),
    )
    absorbed: set[int] = set()
    for i in order:
        if i in absorbed or not _merges_with_neighbours(anomalies[i]):
            continue
        primary = anomalies[i]
        nearby = [
            j
            for j in order
            if j != i
            and j not in absorbed
            and _merges_with_neighbours(anomalies[j])
            and anomalies[j].page == primary.page
            and _box_gap(anomalies[j].bbox, primary.bbox) <= _NEIGHBOUR_PT
            and anomalies[j].confidence <= primary.confidence
        ]
        if not nearby:
            continue
        absorbed.update(nearby)
        items = [primary] + [anomalies[j] for j in nearby]
        signals = [
            signal
            for item in items
            for signal in (item.evidence.get("signals") or [_signal_record(item)])
        ]
        primary.evidence["signals"] = signals
        primary.evidence["signal_count"] = len(signals)
        primary.evidence["merged_nearby"] = len(nearby)
        raw_bbox = _union_bbox(item.bbox for item in items)
        primary.bbox = cast(BBox, tuple(round(value, 2) for value in raw_bbox))
        primary.summary = _format_summary(
            primary.anomaly_type, primary.evidence, deviation=primary.deviation
        )
    return [anomaly for index, anomaly in enumerate(anomalies) if index not in absorbed]


def _mark_local_consecutive(anomalies: list[LayoutAnomaly]) -> None:
    """
    Adjacent flagged full-width clauses on a page are noted on the finding.
    The note is context. It does not raise confidence: two noisy neighbours
    would otherwise confirm each other. Short titles and cover lines do not
    count. Do not emit a new type.
    """
    by_page: dict[int, list[LayoutAnomaly]] = {}
    for anomaly in anomalies:
        if not _is_clause_stretch_finding(anomaly):
            continue
        by_page.setdefault(anomaly.page, []).append(anomaly)

    for group in by_page.values():
        ordered = sorted(group, key=lambda a: a.bbox[1])
        run: list[LayoutAnomaly] = []

        def _flush(items: list[LayoutAnomaly]) -> None:
            if len(items) < 2:
                return
            n = len(items)
            note = f"{n} consecutive anomalous paragraphs"
            for item in items:
                item.evidence["local_pattern"] = True
                item.evidence["consecutive_run"] = n
                if note not in item.location:
                    item.location = f"{item.location} ({note})"
                item.summary = _format_summary(
                    item.anomaly_type,
                    item.evidence,
                    deviation=item.deviation,
                )

        for anomaly in ordered:
            if not run:
                run = [anomaly]
                continue
            prev_bottom = run[-1].bbox[3]
            gap = anomaly.bbox[1] - prev_bottom
            if gap <= 80:
                run.append(anomaly)
            else:
                _flush(run)
                run = [anomaly]
        _flush(run)


def _is_paragraph_finding(anomaly: LayoutAnomaly) -> bool:
    kind = _signal_kind(anomaly)
    if _is_page_wide(kind):
        return False
    if kind == "centre-shift":
        return False
    return bool(anomaly.text)


def _is_clause_stretch_finding(anomaly: LayoutAnomaly) -> bool:
    if not _is_paragraph_finding(anomaly):
        return False
    width = anomaly.bbox[2] - anomaly.bbox[0]
    return width >= _CLAUSE_STRETCH_MIN_WIDTH_PT
