"""Horizontal alignment, same-row vertical alignment, and cover-title centre checks."""

from __future__ import annotations

from statistics import median
from typing import Literal, cast

from .common import (
    ALIGN_EDGE_TOLERANCE_PT, Alignment, _ALIGNMENT_LABEL, _anomaly, _assessed, BBox, _Block,
    _bucket_pt, _by_page, _cannot, _CENTER_MID_BUCKET_PT, CENTER_SHIFT_THRESHOLD_PT,
    CheckAssessment, _delta_confidence, _dominant, _has_strong_majority,
    INDENT_THRESHOLD_PT, LayoutAnomaly, _majority_confidence, _pct, _pt,
    ROW_ALIGN_THRESHOLD_PT, _signed_pt, _snippet, _union_bbox,
)
from .extraction import (
    _is_title_page,
)
from .structure import (
    _body_blocks, _classify_block_alignment, _column_groups, _content_column,
    _merge_checked, _page_columns, _sets_from_groups, StructuralGroup, _width_band,
)


# A paragraph alignment finding needs this share of its peers to agree on
# the expected alignment. A 5-of-8 split is two styles, not one outlier.
_ALIGNMENT_MIN_AGREEMENT = 0.75


def _reportable_alignment(block: _Block, matching: int, comparable: int) -> bool:
    """Table cells set their own alignment (amounts right, labels left), and a
    split vote is not a pattern. Measured on the misalignment sets: removes
    about a sixth of the new noise and no caught shift."""
    return block.role != "table" and matching >= _ALIGNMENT_MIN_AGREEMENT * comparable


def _check_alignment(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        if len(sets) > 1:
            return _merge_checked(_check_alignment(group, _scoped=True) for group in sets)
    body = _body_blocks(blocks)
    if len(body) < 2:
        return _cannot(
            "Not enough comparable lines to assess alignment "
            f"({len(body)} found, need at least 2)"
        )

    anomalies: list[LayoutAnomaly] = []
    classified_any = False
    for raw_blocks in _by_page(body).values():
        raw_blocks = sorted(raw_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(raw_blocks)
        column_sets = (
            _column_groups(raw_blocks, columns) if len(columns) >= 2 else [raw_blocks]
        )
        for page_blocks in column_sets:
            column = _content_column(page_blocks)
            if column is None:
                continue
            content_left, content_right = column

            labelled: list[tuple[_Block, Alignment, Literal["full", "short"]]] = []
            for block in page_blocks:
                alignment = _classify_block_alignment(block, content_left, content_right)
                if alignment is None:
                    continue
                labelled.append((block, alignment, _width_band(block, content_left, content_right)))

            if not labelled:
                continue

            full_peers: list[tuple[_Block, Alignment]] = [
                (block, alignment) for block, alignment, band in labelled if band == "full"
            ]
            short_peers: list[tuple[_Block, Alignment]] = [
                (block, alignment) for block, alignment, band in labelled if band == "short"
            ]
            full_aligns: list[Alignment] = [alignment for _, alignment in full_peers]
            full_expected = _dominant(full_aligns) if len(full_peers) >= 2 else None

            for peers in (full_peers, short_peers):
                if len(peers) < 2:
                    continue
                classified_any = True
                peer_aligns: list[Alignment] = [alignment for _, alignment in peers]
                expected = _dominant(peer_aligns)
                if expected is None:
                    continue
                matching = sum(1 for _, a in peers if a == expected)
                if not _has_strong_majority(matching, len(peers)):
                    continue
                for block, alignment in peers:
                    if alignment == expected:
                        continue
                    if _alignment_mismatch_unreliable(
                        block, alignment, expected, content_left
                    ):
                        continue
                    if not _reportable_alignment(block, matching, len(peers)):
                        continue
                    anomalies.append(
                        _alignment_anomaly(block, alignment, expected, peers)
                    )

            # A centred/right-aligned insertion among full-width body copy is
            # often itself short (its lines don't fill the column), so it
            # never enters the full-band comparison above. Flag it against
            # the full-band majority when it looks like a paragraph, not a
            # one-line heading or date.
            full_matching = sum(1 for _, a in full_peers if a == full_expected)
            if full_expected is not None and _has_strong_majority(
                full_matching, len(full_peers)
            ):
                classified_any = True
                already = {(a.page, a.bbox) for a in anomalies}
                for block, alignment in short_peers:
                    if alignment == full_expected:
                        continue
                    if _alignment_mismatch_unreliable(
                        block, alignment, full_expected, content_left
                    ):
                        continue
                    if not (len(block.lines) >= 2 or block.char_count >= 80):
                        continue
                    key = (block.page + 1, tuple(round(v, 2) for v in block.bbox))
                    if key in already:
                        continue
                    if not _reportable_alignment(block, full_matching, len(full_peers)):
                        continue
                    anomalies.append(
                        _alignment_anomaly(block, alignment, full_expected, full_peers)
                    )

    if not classified_any:
        return _cannot(
            "Could not reliably classify horizontal alignment of comparable paragraphs"
        )
    return anomalies, _assessed()


def _check_row_alignment(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """
    Horizontal alignment (left/centre/right/justified) does not catch a
    right-hand amount sitting higher or lower than its left-hand label.
    Pair each body/table block with the nearest item to its right on the
    same visual row and compare text baselines (span origin), not the
    bbox edges. A 12pt label and a 9pt value share a baseline and still
    have different tops and bottoms. A short all-caps heading joins that
    row, beside a body line or another heading. A heading has moved when
    its baseline leaves the line beside it. Running headers stay out.
    """
    # Label, address, date, and list rows sit in the body band. A value
    # beside a label is still one row. Table cells stay in their own pool.
    row_roles = {"body", "label_value", "address", "date", "list_item", "table"}
    candidates = [
        b
        for b in blocks
        if b.role in row_roles and b.height >= 2.0 and b.text.strip()
    ]
    by_group: dict[tuple[int, str], list[_Block]] = {}
    for block in candidates:
        pool = "table" if block.role == "table" else "body"
        by_group.setdefault((block.page, pool), []).append(block)

    anomalies: list[LayoutAnomaly] = []
    seen: set[tuple[int, BBox, BBox]] = set()
    groups: list[tuple[list[_Block], bool]] = [
        (group, False) for group in by_group.values()
    ]
    groups.extend((group, True) for group in _heading_row_groups(blocks).values())
    for group_blocks, heading_row in groups:
        measured: list[tuple[_Block, _Block, tuple[float, float]]] = []
        for left, right in _nearest_row_pairs(group_blocks):
            if heading_row and left.role != "heading" and right.role != "heading":
                continue
            # A short heading inside the box of a tall paragraph is not on
            # that paragraph's row. The line beside it has its middle here.
            if heading_row and _mid_y_gap(left, right) > (
                max(min(left.height, right.height), ROW_ALIGN_THRESHOLD_PT) * 1.1
            ):
                continue
            deltas = (
                _both_edges_off(left, right)
                if heading_row
                else _vertical_misalignment(left, right)
            )
            if deltas is None:
                continue
            measured.append((left, right, deltas))
        unique_deltas = []
        seen_pairs: set[tuple[int, BBox, BBox]] = set()
        for left, right, deltas in measured:
            pair_key = (right.page, left.bbox, right.bbox)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            unique_deltas.append(deltas)
        rhythms = _row_offset_rhythms(unique_deltas)
        for left, right, (top_delta, baseline_delta) in measured:
            key = (right.page, left.bbox, right.bbox)
            if key in seen:
                continue
            seen.add(key)
            primary = (
                top_delta if abs(top_delta) >= abs(baseline_delta) else baseline_delta
            )
            # The same stagger on many rows is how a statement's columns sit,
            # not one amount shoved off its label.
            if _matches_row_rhythm(primary, rhythms):
                continue
            location = (
                f"Same-row pair near y={round(left.y0)} "
                f"({_snippet(right.text, 40)} vs {_snippet(left.text, 40)})"
            )
            confidence = _delta_confidence(abs(primary), ROW_ALIGN_THRESHOLD_PT, 1.0)
            pair = _compared_baseline_pair(left, right)
            detected_baseline = pair[1] if pair is not None else right.y1
            expected_baseline = pair[0] if pair is not None else left.y1
            evidence = {
                "axis": "vertical",
                "detected_top_pt": round(right.y0, 2),
                "expected_top_pt": round(left.y0, 2),
                "detected_baseline_pt": round(detected_baseline, 2),
                "expected_baseline_pt": round(expected_baseline, 2),
                "top_delta_pt": round(top_delta, 2),
                "baseline_delta_pt": round(baseline_delta, 2),
                "row_spread_top_pt": round(abs(top_delta), 2),
                "row_count": 2,
            }
            if pair is not None:
                detected = f"baseline {_pt(pair[1])}"
                expected = f"baseline {_pt(pair[0])}"
            else:
                detected = f"top {_pt(right.y0)}, baseline {_pt(right.y1)}"
                expected = f"top {_pt(left.y0)}, baseline {_pt(left.y1)}"
            finding = _anomaly(
                "Alignment",
                right,
                detected=detected,
                expected=expected,
                baseline=f"Left-hand '{_snippet(left.text, 40)}' at top {_pt(left.y0)}",
                deviation=_signed_pt(primary),
                confidence=confidence,
                evidence=evidence,
                location=location,
            )
            # Two items that no longer share a baseline do not say which one
            # moved: a label shifts as often as its value. Highlight the pair.
            finding.bbox = cast(BBox, tuple(round(v, 2) for v in _union_bbox([left.bbox, right.bbox])))
            anomalies.append(finding)
    return anomalies


def _heading_row_groups(blocks: list[_Block]) -> dict[int, list[_Block]]:
    """Body, table, and heading lines on a page that has a heading.

    The heading is compared with whichever of those sits beside it. Headers
    and footers stay out.
    """
    pages: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.role not in {"body", "table", "heading"}:
            continue
        if block.height < 2.0 or not block.text.strip():
            continue
        pages.setdefault(block.page, []).append(block)
    return {
        page: items
        for page, items in pages.items()
        if any(block.role == "heading" for block in items)
    }


def _compared_baseline_pair(left: _Block, right: _Block) -> tuple[float, float] | None:
    """Nearest span baselines. A wrapping label shares one line with the value."""
    lefts = [line.baseline for line in left.lines if line.baseline is not None]
    rights = [line.baseline for line in right.lines if line.baseline is not None]
    if not lefts or not rights:
        return None
    return min(
        ((left_base, right_base) for left_base in lefts for right_base in rights),
        key=lambda pair: abs(pair[1] - pair[0]),
    )


def _baseline_shift(left: _Block, right: _Block) -> float | None:
    pair = _compared_baseline_pair(left, right)
    if pair is None:
        return None
    return pair[1] - pair[0]


def _both_edges_off(left: _Block, right: _Block) -> tuple[float, float] | None:
    """A line that left the row takes its baseline with it.

    When the text layer has span origins, those baselines decide. Bbox
    tops and bottoms differ for mixed font sizes on the same baseline.
    """
    shift = _baseline_shift(left, right)
    if shift is not None:
        if abs(shift) < ROW_ALIGN_THRESHOLD_PT:
            return None
        return shift, shift
    top_delta = right.y0 - left.y0
    baseline_delta = right.y1 - left.y1
    if (
        abs(top_delta) >= ROW_ALIGN_THRESHOLD_PT
        and abs(baseline_delta) >= ROW_ALIGN_THRESHOLD_PT
    ):
        return top_delta, baseline_delta
    return None


def _row_offset_rhythms(deltas: list[tuple[float, float]]) -> list[float]:
    """Offsets shared by two or more rows are the column's usual stagger."""
    if len(deltas) < 2:
        return []
    primaries = [
        top if abs(top) >= abs(base) else base
        for top, base in deltas
    ]
    clusters: list[list[float]] = []
    for value in sorted(primaries):
        if clusters and abs(value - clusters[-1][0]) < ROW_ALIGN_THRESHOLD_PT:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [float(median(cluster)) for cluster in clusters if len(cluster) >= 2]


def _matches_row_rhythm(value: float, rhythms: list[float]) -> bool:
    return any(abs(value - rhythm) < ROW_ALIGN_THRESHOLD_PT for rhythm in rhythms)


def _nearest_row_pairs(blocks: list[_Block]) -> list[tuple[_Block, _Block]]:
    """Each block pairs with the closest item to its right on the same row."""
    pairs: list[tuple[_Block, _Block]] = []
    for left in blocks:
        partners = [
            b
            for b in blocks
            if b is not left
            and b.x0 >= left.x0
            and _side_by_side(left, b)
            and _same_visual_row(left, b)
        ]
        if not partners:
            continue
        right = min(partners, key=lambda b: (_mid_y_gap(left, b), b.x0))
        if right.x0 == left.x0:
            continue
        pairs.append((left, right))
    return pairs


def _check_displaced_row_members(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """A label or value sitting one to three lines off its row partner.

    Needs a page that already has several paired rows, then an unpaired
    item in each column whose vertical gap is about a line or three.
    The member that broke the column's rhythm is the one that moved.
    A label that lands exactly on the next row collides with that row,
    which the collision check covers instead.
    """
    roles = {"body", "label_value", "address", "date", "list_item", "table"}
    by_page = _by_page(
        b for b in blocks if b.role in roles and b.height >= 2 and b.text.strip()
    )

    def asymmetry(block: _Block, column: list[_Block]) -> float:
        col = sorted(column, key=lambda item: item.y0)
        index = col.index(block)
        if index == 0 or index == len(col) - 1:
            return 0.0
        return abs((block.y0 - col[index - 1].y0) - (col[index + 1].y0 - block.y0))

    out: list[LayoutAnomaly] = []
    for page_blocks in by_page.values():
        pairs = _nearest_row_pairs(page_blocks)
        if len(pairs) < 3:
            continue
        paired = {id(block) for pair in pairs for block in pair}
        left_x = float(median(left.x0 for left, _ in pairs))
        right_x = float(median(right.x0 for _, right in pairs))
        lefts = [block for block in page_blocks if abs(block.x0 - left_x) < INDENT_THRESHOLD_PT]
        rights = [block for block in page_blocks if abs(block.x0 - right_x) < INDENT_THRESHOLD_PT]
        orphan_left = [block for block in lefts if id(block) not in paired]
        orphan_right = [block for block in rights if id(block) not in paired]
        for left in orphan_left:
            mates = [
                block
                for block in orphan_right
                if 0 < abs(_mid_y(left) - _mid_y(block)) <= 3 * max(left.height, block.height)
            ]
            if not mates:
                continue
            mate = min(mates, key=lambda block: abs(_mid_y(left) - _mid_y(block)))
            if asymmetry(left, lefts) >= asymmetry(mate, rights):
                mover = left
            else:
                mover = mate
            partner = mate if mover is left else left
            dy = _mid_y(mover) - _mid_y(partner)
            evidence = {
                "axis": "vertical",
                "kind": "row-alignment",
                "top_delta_pt": round(dy, 2),
                "baseline_delta_pt": round(dy, 2),
                "row_count": 2,
            }
            out.append(
                _anomaly(
                    "Alignment",
                    mover,
                    detected=f"{_signed_pt(dy)} from its row partner",
                    expected="same baseline as partner",
                    baseline=f"{len(pairs)} other rows on this page are paired",
                    deviation=_signed_pt(dy),
                    confidence=_delta_confidence(abs(dy), ROW_ALIGN_THRESHOLD_PT, 1.0),
                    evidence=evidence,
                    location=f"Displaced row member near y={round(mover.y0)}",
                )
            )
    return out


def _vertical_misalignment(
    left: _Block, right: _Block
) -> tuple[float, float] | None:
    shift = _baseline_shift(left, right)
    if shift is not None:
        if abs(shift) < ROW_ALIGN_THRESHOLD_PT:
            return None
        return shift, shift
    top_delta = right.y0 - left.y0
    baseline_delta = right.y1 - left.y1
    similar_height = abs(right.height - left.height) <= max(
        4.0, 0.25 * max(left.height, right.height)
    )
    if similar_height:
        # Same-size label and amount should share both edges.
        if (
            abs(top_delta) >= ROW_ALIGN_THRESHOLD_PT
            or abs(baseline_delta) >= ROW_ALIGN_THRESHOLD_PT
        ):
            return top_delta, baseline_delta
        return None
    # Wrapping label next to a one-line amount: sharing a top or a
    # baseline is enough; tamper is neither edge lining up.
    if (
        abs(top_delta) >= ROW_ALIGN_THRESHOLD_PT
        and abs(baseline_delta) >= ROW_ALIGN_THRESHOLD_PT
    ):
        return top_delta, baseline_delta
    return None


def _mid_y(block: _Block) -> float:
    return (block.y0 + block.y1) / 2


def _mid_y_gap(a: _Block, b: _Block) -> float:
    return abs(_mid_y(a) - _mid_y(b))


def _same_visual_row(a: _Block, b: _Block) -> bool:
    """Side-by-side items occupying the same horizontal band."""
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    if overlap > 0:
        return True
    # Midpoints within about a line-height: a shoved amount with no
    # box overlap. A column header like "Total ($)" sits a full row
    # above the figure and stays out.
    line = max(min(a.height, b.height), ROW_ALIGN_THRESHOLD_PT)
    return _mid_y_gap(a, b) <= line * 1.1


def _side_by_side(a: _Block, b: _Block) -> bool:
    x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    if x_overlap <= 0:
        return True
    min_w = min(a.width, b.width)
    if min_w > 0 and x_overlap / min_w < 0.4:
        return True
    # Full-width line vs a short amount sitting on its right: the amount
    # sits inside the line's x-range, but they are still a label/value pair.
    left, right = (a, b) if a.x0 <= b.x0 else (b, a)
    return right.x0 >= (left.x0 + left.x1) / 2


def _check_centre_shift(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """
    Cover/title pages stack short centred headings on one vertical axis.
    A heading whose midpoint is off that axis (company name shoved right)
    is not caught by left/centre/right/justified, because the shifted
    line often becomes the content-column edge and is labelled justified.
    """
    anomalies: list[LayoutAnomaly] = []
    for page_blocks in _by_page(
        b for b in blocks if b.role in ("body", "cover") and b.text.strip()
    ).values():
        covers = [b for b in page_blocks if b.role == "cover"]
        group = covers if len(covers) >= 4 else page_blocks
        if len(covers) < 4 and not _is_title_page(group):
            continue
        page_blocks = group
        mids = [_mid_x(b) for b in page_blocks]
        bucketed = [_bucket_pt(mid, _CENTER_MID_BUCKET_PT) for mid in mids]
        expected_mid = _dominant(bucketed)
        if expected_mid is None:
            continue
        matching = sum(
            1 for mid in mids if abs(mid - float(expected_mid)) < CENTER_SHIFT_THRESHOLD_PT
        )
        if matching < 3:
            continue
        expected_mid_f = float(expected_mid)
        for block, mid in zip(page_blocks, mids):
            delta = mid - expected_mid_f
            if abs(delta) < CENTER_SHIFT_THRESHOLD_PT:
                continue
            location = f"Cover title near y={round(block.y0)} ({_snippet(block.text, 40)})"
            confidence = _delta_confidence(
                abs(delta), CENTER_SHIFT_THRESHOLD_PT, matching / len(page_blocks)
            )
            evidence = {
                "axis": "horizontal",
                "kind": "centre-shift",
                "detected_mid_pt": round(mid, 2),
                "expected_mid_pt": round(expected_mid_f, 2),
                "mid_delta_pt": round(delta, 2),
                "comparable_count": len(page_blocks),
                "matching_count": matching,
            }
            anomalies.append(
                _anomaly(
                    "Alignment",
                    block,
                    detected=f"centre {_pt(mid)}",
                    expected=f"centre {_pt(expected_mid_f)}",
                    baseline=(
                        f"{matching} of {len(page_blocks)} cover titles "
                        f"share a centre at {_pt(expected_mid_f)}"
                    ),
                    deviation=_signed_pt(delta),
                    confidence=confidence,
                    evidence=evidence,
                    location=location,
                )
            )
    return anomalies


def _mid_x(block: _Block) -> float:
    return (block.x0 + block.x1) / 2


def _alignment_mismatch_unreliable(
    block: _Block,
    alignment: Alignment,
    expected: Alignment,
    content_left: float,
) -> bool:
    """
    Alignment here means where the box sits, not how far the text fills.

    Left vs justified is a fill-ratio difference (ragged vs flush right)
    when both start on the same column edge. Left vs right-aligned is only
    a placement change when the box itself has moved off that edge.
    """
    pair = {alignment, expected}
    if pair == {"left-aligned", "justified"}:
        return True
    left_flush = abs(block.x0 - content_left) <= ALIGN_EDGE_TOLERANCE_PT
    return pair == {"left-aligned", "right-aligned"} and left_flush


def _alignment_anomaly(
    block: _Block,
    detected: Alignment,
    expected: Alignment,
    peers: list[tuple[_Block, Alignment]],
) -> LayoutAnomaly:
    matching = sum(1 for _, alignment in peers if alignment == expected)
    comparable = len(peers) or 1
    expected_label = _ALIGNMENT_LABEL[expected]
    return _anomaly(
        "Alignment",
        block,
        detected=_ALIGNMENT_LABEL[detected],
        expected=expected_label,
        baseline=(
            f"{_pct(matching, comparable)} of comparable body paragraphs "
            f"are {expected_label.lower()}"
        ),
        deviation="1 paragraph",
        confidence=_majority_confidence(matching, comparable),
        evidence={
            "alignment": detected,
            "expected_alignment": expected,
            "comparable_count": comparable,
            "matching_count": matching,
            "occurrence_count": 1,
        },
    )
