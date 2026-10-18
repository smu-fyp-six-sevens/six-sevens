"""Single lines shifted left, right, up, or down inside or between blocks."""

from __future__ import annotations

from statistics import median

from .common import (
    _anomaly, _Block, _block_for_line, _COLLISION_MIN_OVERLAP_FRACTION, _delta_confidence,
    _different_size, _edge_anomaly, INDENT_THRESHOLD_PT, LayoutAnomaly, _letters_in, _Line,
    _line_width, MIN_BLOCK_CHARS, _pct, _pt, _signed_pt, VERTICAL_SHIFT_THRESHOLD_PT, _x_overlap,
)
from .structure import (
    _COLUMN_MIN_MEMBERS, _sets_from_groups, StructuralGroup,
)
from .indentation import (
    _COLUMN_SHIFT_MAX_PT, _SHIFT_MAX_HORIZONTAL_PT, _SHIFT_MAX_VERTICAL_REACH_PT,
    _SHIFT_NEIGHBOR_WINDOW_PT, _STACK_OVERLAP_PT,
)


def _check_shifted_lines(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> list[LayoutAnomaly]:
    """Name one line that moved up, down, left, or right.

    A line inside a larger block is compared with the lines in that block.
    A line that extraction kept as its own block is compared with the
    nearest line on the page that shares its horizontal span. A first-line
    indent still ends with the paragraph, so it is not a shift. Several
    lines that share a left edge are a column, not one moved line.
    """
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        if len(sets) > 1:
            return [a for group in sets for a in _check_shifted_lines(group, _scoped=True)]
    anomalies: list[LayoutAnomaly] = []
    for block in blocks:
        if block.role in {"header", "footer"}:
            continue
        lines = [line for line in block.lines if line.text.strip()]
        if len(lines) < 2 or block.width <= 0:
            continue
        anomalies.extend(_line_indent_inside_block(block, lines))
        anomalies.extend(_line_past_block_edge(block, lines))
    anomalies.extend(_check_cross_block_shifts(blocks))
    anomalies.extend(_check_column_line_shifts(blocks))
    return anomalies


def _check_column_line_shifts(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """A value that no longer starts with the column beside it.

    Labels and values on a form share a left edge with the lines beside
    them. One line that starts a short way off that nearby edge, and does
    not still end with the column, has left it. A match further up the
    page does not count. A hanging indent still ends on the column's
    right edge, and the next column over is further away than a shove.
    """
    anomalies: list[LayoutAnomaly] = []
    for entries in _page_line_entries(blocks).values():
        columns = _line_left_columns([line for _, line in entries])
        if not columns:
            continue
        for block, line in entries:
            if _letters_in(line) < MIN_BLOCK_CHARS or _line_width(line) < 80:
                continue
            if _line_on_nearby_column(line, columns):
                continue
            chosen = _nearest_column(line, columns)
            if chosen is None:
                continue
            edge, members = chosen
            signed = line.bbox[0] - edge
            if _still_ends_with_column(block, line, members, entries):
                continue
            anomalies.append(
                _edge_anomaly(
                    _block_for_line(block, line),
                    side="left",
                    detected=line.bbox[0],
                    expected=edge,
                    signed=signed,
                    comparable=len(members) + 1,
                    matching=len(members),
                    baseline=f"The other lines in this column start at {_pt(edge)}",
                    confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, 1.0),
                )
            )
    return anomalies


def _line_left_columns(lines: list[_Line]) -> list[tuple[float, list[_Line]]]:
    """Left edges shared by at least three lines. Each edge is a column."""
    clusters: list[list[_Line]] = []
    for line in sorted(lines, key=lambda item: item.bbox[0]):
        if clusters and line.bbox[0] - clusters[-1][0].bbox[0] < INDENT_THRESHOLD_PT:
            clusters[-1].append(line)
        else:
            clusters.append([line])
    columns: list[tuple[float, list[_Line]]] = []
    for cluster in clusters:
        if len(cluster) < _COLUMN_MIN_MEMBERS:
            continue
        edge = float(median(line.bbox[0] for line in cluster))
        columns.append((edge, cluster))
    return columns


def _nearby_column_members(line: _Line, members: list[_Line]) -> list[_Line]:
    """Column lines within reach of this one. A shared edge far up the page is not this column."""
    return [
        member
        for member in members
        if member is not line
        and 2 < abs(member.bbox[1] - line.bbox[1]) <= _SHIFT_NEIGHBOR_WINDOW_PT
    ]


def _line_on_nearby_column(line: _Line, columns: list[tuple[float, list[_Line]]]) -> bool:
    for edge, members in columns:
        if abs(line.bbox[0] - edge) >= INDENT_THRESHOLD_PT:
            continue
        if len(_nearby_column_members(line, members)) >= _COLUMN_MIN_MEMBERS - 1:
            return True
    return False


def _nearest_column(
    line: _Line, columns: list[tuple[float, list[_Line]]]
) -> tuple[float, list[_Line]] | None:
    chosen: tuple[float, list[_Line]] | None = None
    chosen_dist = _COLUMN_SHIFT_MAX_PT
    for _edge, members in columns:
        nearby = _nearby_column_members(line, members)
        if len(nearby) < _COLUMN_MIN_MEMBERS - 1:
            continue
        edge = float(median(member.bbox[0] for member in nearby))
        dist = abs(line.bbox[0] - edge)
        if dist < INDENT_THRESHOLD_PT or dist > chosen_dist:
            continue
        chosen = (edge, nearby)
        chosen_dist = dist
    return chosen


def _still_ends_with_column(
    block: _Block,
    line: _Line,
    members: list[_Line],
    entries: list[tuple[_Block, _Line]],
) -> bool:
    """A hanging indent still ends where the column ends.

    A value that left its column moved its right edge too. The column
    counts as flush only when its own nearby lines agree on that edge.
    """
    homes = {id(item): owner for owner, item in entries}
    for member in members:
        if homes.get(id(member)) is not block:
            continue
        if abs(line.bbox[2] - member.bbox[2]) < INDENT_THRESHOLD_PT:
            return True
    rights = [member.bbox[2] for member in members]
    if max(rights) - min(rights) >= INDENT_THRESHOLD_PT:
        return False
    return abs(line.bbox[2] - float(median(rights))) < INDENT_THRESHOLD_PT


def _is_sentence_line(block: _Block, line: _Line) -> bool:
    """A slid label is not a sentence. Short tokens fail both gates."""
    letters = len(line.text.replace(" ", "").replace("\xa0", ""))
    if letters < MIN_BLOCK_CHARS:
        return False
    width = _line_width(line)
    return width >= 120 and width >= 0.4 * block.width


def _line_indent_inside_block(block: _Block, lines: list[_Line]) -> list[LayoutAnomaly]:
    anomalies: list[LayoutAnomaly] = []
    for line in lines:
        if not _is_sentence_line(block, line):
            continue
        same_left = [
            other
            for other in lines
            if abs(other.bbox[0] - line.bbox[0]) < INDENT_THRESHOLD_PT
        ]
        if len(same_left) != 1:
            continue
        anchors = [
            other
            for other in lines
            if abs(other.bbox[1] - line.bbox[1]) > 2
            and abs(other.bbox[1] - line.bbox[1]) <= _SHIFT_NEIGHBOR_WINDOW_PT
            and line.bbox[0] - other.bbox[0] >= INDENT_THRESHOLD_PT
        ]
        if not anchors:
            continue
        if _shares_right_edge_with_full_line(block, line, lines):
            continue
        baseline = float(median(other.bbox[0] for other in anchors))
        signed = line.bbox[0] - baseline
        anomalies.append(
            _edge_anomaly(
                _block_for_line(block, line),
                side="left",
                detected=line.bbox[0],
                expected=baseline,
                signed=signed,
                comparable=len(lines),
                matching=len(anchors),
                baseline=f"The neighbouring line in this block starts at {_pt(baseline)}",
                confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, 1.0),
            )
        )
    return anomalies


def _shares_right_edge_with_full_line(
    block: _Block, line: _Line, lines: list[_Line]
) -> bool:
    """A first-line indent still ends with the rest of the paragraph."""
    for other in lines:
        if other is line or not _is_sentence_line(block, other):
            continue
        if line.bbox[0] - other.bbox[0] < INDENT_THRESHOLD_PT:
            continue
        if abs(line.bbox[2] - other.bbox[2]) < INDENT_THRESHOLD_PT:
            return True
    return False


def _line_past_block_edge(block: _Block, lines: list[_Line]) -> list[LayoutAnomaly]:
    # Body paragraphs are already compared as whole blocks. This names a
    # line inside a table or other merged block, where the block edge is
    # the line but the block text is not.
    if block.role == "body":
        return []
    wide = [line for line in lines if _is_sentence_line(block, line)]
    if len(wide) < 3:
        return []
    top = max(wide, key=lambda line: line.bbox[2])
    others = [line for line in wide if line is not top]
    if max(line.bbox[2] for line in others) - min(line.bbox[2] for line in others) > INDENT_THRESHOLD_PT:
        return []
    edge = float(median(line.bbox[2] for line in others))
    signed = top.bbox[2] - edge
    if signed < INDENT_THRESHOLD_PT:
        return []
    matching = sum(1 for line in others if abs(line.bbox[2] - edge) < INDENT_THRESHOLD_PT)
    return [
        _edge_anomaly(
            _block_for_line(block, top),
            side="right",
            detected=top.bbox[2],
            expected=edge,
            signed=signed,
            comparable=len(wide),
            matching=matching,
            baseline=f"{_pct(matching, len(wide))} of lines in this block end at {_pt(edge)}",
            confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, matching / len(wide)),
            location=f"Right edge of body paragraph near y={round(top.bbox[1])}",
        )
    ]


def _stacked(a: _Line, b: _Line) -> bool:
    return _x_overlap(a, b) >= _STACK_OVERLAP_PT


def _vertical_overlap(a: _Line, b: _Line) -> float:
    return min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])


def _page_line_entries(blocks: list[_Block]) -> dict[int, list[tuple[_Block, _Line]]]:
    pages: dict[int, list[tuple[_Block, _Line]]] = {}
    for block in blocks:
        if block.role in {"header", "footer"}:
            continue
        for line in block.lines:
            if line.text.strip():
                pages.setdefault(line.page, []).append((block, line))
    return pages


def _check_cross_block_shifts(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """A line kept as its own block, compared with the lines around it.

    Horizontal: the line is the only wide line at its left edge, and the
    nearest line that shares its span starts further left or right.
    Vertical: the line collides with the line above or below, or it sits
    in a lopsided gap between them. A paragraph break is a large gap
    followed by a normal one, so the smaller gap still matches the page.
    """
    anomalies: list[LayoutAnomaly] = []
    for entries in _page_line_entries(blocks).values():
        sentence = [(block, line) for block, line in entries if _is_sentence_line(block, line)]
        alone = [
            (block, line)
            for block, line in sentence
            if sum(1 for item in block.lines if item.text.strip()) == 1
        ]
        anomalies.extend(_horizontal_line_shifts(entries, sentence, alone))
        anomalies.extend(_vertical_line_shifts(entries, sentence))
        anomalies.extend(_check_split_lines(entries))
    return anomalies


def _horizontal_line_shifts(
    entries: list[tuple[_Block, _Line]],
    sentence: list[tuple[_Block, _Line]],
    alone: list[tuple[_Block, _Line]],
) -> list[LayoutAnomaly]:
    anomalies: list[LayoutAnomaly] = []
    for block, line in alone:
        same_left = [
            other
            for _, other in sentence
            if abs(other.bbox[0] - line.bbox[0]) < INDENT_THRESHOLD_PT / 2
        ]
        if len(same_left) != 1:
            continue
        neighbor = _nearest_stacked_line(line, entries)
        if neighbor is None:
            continue
        signed = line.bbox[0] - neighbor.bbox[0]
        if abs(signed) < INDENT_THRESHOLD_PT or abs(signed) > _SHIFT_MAX_HORIZONTAL_PT:
            continue
        if signed > 0 and _shares_right_edge_with_full_line(block, line, [item for _, item in sentence]):
            continue
        if signed < 0 and not _rigid_left_slide(line, neighbor, sentence):
            continue
        anomalies.append(
            _edge_anomaly(
                _block_for_line(block, line),
                side="left",
                detected=line.bbox[0],
                expected=neighbor.bbox[0],
                signed=signed,
                comparable=len(sentence),
                matching=1,
                baseline=f"The nearest line on this span starts at {_pt(neighbor.bbox[0])}",
                confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, 1.0),
            )
        )
    return anomalies


def _nearest_stacked_line(
    line: _Line, entries: list[tuple[_Block, _Line]]
) -> _Line | None:
    nearest: _Line | None = None
    nearest_dy = _SHIFT_MAX_VERTICAL_REACH_PT
    for _, other in entries:
        if other is line or not _stacked(line, other):
            continue
        dy = abs(other.bbox[1] - line.bbox[1])
        if dy <= 2 or dy > nearest_dy:
            continue
        nearest = other
        nearest_dy = dy
    return nearest


def _rigid_left_slide(
    line: _Line,
    neighbor: _Line,
    sentence: list[tuple[_Block, _Line]],
) -> bool:
    """Both edges moved left together. A hanging indent still ends flush.

    The nearest line can be a short last line of the paragraph. A slide
    is judged against the wide line of a similar length, whose right
    edge moved with the left.
    """
    peer = _similar_width_sentence(line, sentence)
    if peer is None and any(
        other is neighbor and _is_sentence_line(block, other) for block, other in sentence
    ):
        peer = neighbor
    if peer is None:
        return False
    left_delta = peer.bbox[0] - line.bbox[0]
    right_delta = peer.bbox[2] - line.bbox[2]
    if left_delta < INDENT_THRESHOLD_PT or right_delta < INDENT_THRESHOLD_PT:
        return False
    return abs(left_delta - right_delta) <= INDENT_THRESHOLD_PT


def _similar_width_sentence(
    line: _Line, sentence: list[tuple[_Block, _Line]]
) -> _Line | None:
    """Nearest wide line whose length matches this one."""
    width = _line_width(line)
    pool: list[_Line] = []
    for block, other in sentence:
        if other is line or not _is_sentence_line(block, other):
            continue
        dy = abs(other.bbox[1] - line.bbox[1])
        if dy <= 2 or dy > _SHIFT_MAX_VERTICAL_REACH_PT or not _stacked(line, other):
            continue
        pool.append(other)
    if not pool:
        return None
    return min(pool, key=lambda other: (abs(_line_width(other) - width), abs(other.bbox[1] - line.bbox[1])))


def _vertical_line_shifts(
    entries: list[tuple[_Block, _Line]],
    sentence: list[tuple[_Block, _Line]],
) -> list[LayoutAnomaly]:
    gaps = _stacked_gaps(entries)
    typical = float(median(gaps)) if len(gaps) >= 3 else None
    anomalies: list[LayoutAnomaly] = []
    for block, line in sentence:
        collision = _collision_delta(line, entries)
        if collision is not None:
            signed, frac = collision
            confidence = round(min(1.0, 0.45 + 0.55 * frac), 2)
        else:
            signed = _lopsided_gap_delta(line, entries, typical)
            if signed is None:
                signed = _same_edge_pocket(line, entries)
            if signed is None or abs(signed) < VERTICAL_SHIFT_THRESHOLD_PT:
                continue
            confidence = _delta_confidence(signed, VERTICAL_SHIFT_THRESHOLD_PT, 1.0)
        anomalies.append(
            _anomaly(
                "Spacing",
                _block_for_line(block, line),
                detected=_pt(line.bbox[1]),
                expected=_pt(line.bbox[1] - signed),
                baseline="The lines above and below leave a different gap on each side",
                deviation=_signed_pt(signed),
                confidence=confidence,
                evidence={
                    "kind": "vertical",
                    "vertical_delta_pt": round(signed, 2),
                    "comparable_count": len(gaps) if gaps else len(sentence),
                    "matching_count": 1,
                },
                location=f"Vertical position of line near y={round(line.bbox[1])}",
            )
        )
    return anomalies


def _stacked_gaps(entries: list[tuple[_Block, _Line]]) -> list[float]:
    ordered = sorted(entries, key=lambda item: item[1].bbox[1])
    gaps: list[float] = []
    for (_, prev), (_, curr) in zip(ordered, ordered[1:]):
        if not _stacked(prev, curr):
            continue
        gap = curr.bbox[1] - prev.bbox[1]
        if 0 < gap <= 80:
            gaps.append(gap)
    return gaps


def _collision_delta(
    line: _Line, entries: list[tuple[_Block, _Line]]
) -> tuple[float, float] | None:
    """(dy, overlap_fraction) of the closest genuinely colliding line.

    dy is positive when this line sits below the line it overlaps.
    A thin band is ascent meeting descent, which tight leading produces
    on every line, so the overlap has to cover half the shorter box.
    """
    closest: tuple[float, float] | None = None
    for _, other in entries:
        if other is line or not _stacked(line, other):
            continue
        dy = line.bbox[1] - other.bbox[1]
        if abs(dy) < 0.5 or abs(dy) > 24:
            continue
        overlap = _vertical_overlap(line, other)
        height = min(line.bbox[3] - line.bbox[1], other.bbox[3] - other.bbox[1])
        if height <= 0 or overlap / height < _COLLISION_MIN_OVERLAP_FRACTION:
            continue
        if closest is None or abs(dy) < abs(closest[0]):
            closest = (dy, overlap / height)
    return closest


def _lopsided_gap_delta(
    line: _Line,
    entries: list[tuple[_Block, _Line]],
    typical: float | None,
) -> float | None:
    prev = next_line = None
    for _, other in entries:
        if other is line or not _stacked(line, other):
            continue
        if (
            line.bbox[1] - _SHIFT_MAX_VERTICAL_REACH_PT
            < other.bbox[1]
            < line.bbox[1] - 2
            and (prev is None or other.bbox[1] > prev.bbox[1])
        ):
            prev = other
        elif (
            line.bbox[1] + 2
            < other.bbox[1]
            < line.bbox[1] + _SHIFT_MAX_VERTICAL_REACH_PT
            and (next_line is None or other.bbox[1] < next_line.bbox[1])
        ):
            next_line = other
    if prev is None or next_line is None:
        return None
    # A heading in another size above or below sets its own gap: a section break, not leading.
    if _different_size(line, prev) or _different_size(line, next_line):
        return None
    gap_before = line.bbox[1] - prev.bbox[1]
    gap_after = next_line.bbox[1] - line.bbox[1]
    if gap_before <= 0 or gap_after <= 0:
        return None
    if abs(gap_before - gap_after) < INDENT_THRESHOLD_PT:
        return None
    smaller = min(gap_before, gap_after)
    larger = max(gap_before, gap_after)
    if typical is not None and smaller + 1 >= typical:
        return None
    if typical is None and smaller >= 10:
        return None
    # A section break, then a normal line, is not one line shoved off centre.
    if typical is not None and larger > 3 * typical and abs(smaller - typical) <= 4:
        return None
    return line.bbox[1] - (prev.bbox[1] + next_line.bbox[1]) / 2


def _same_edge_lines(line: _Line, entries: list[tuple[_Block, _Line]]) -> list[_Line]:
    """Lines that share this left edge, nearest first in reading order."""
    peers = [
        other
        for _, other in entries
        if other is not line and abs(other.bbox[0] - line.bbox[0]) <= INDENT_THRESHOLD_PT / 2
    ]
    return sorted(peers, key=lambda other: other.bbox[1])


def _same_edge_pocket(line: _Line, entries: list[tuple[_Block, _Line]]) -> float | None:
    """A line shoved into the space between two paragraphs.

    The lines above still share its left edge and a normal leading. Both
    the gap above this line and the gap below it are at least 12pt larger
    than that leading, so the line is sitting low in an opened pocket.
    A new paragraph only opens the gap above it.
    """
    peers = _same_edge_lines(line, entries)
    above = [other for other in peers if other.bbox[1] < line.bbox[1] - 2]
    below = [other for other in peers if other.bbox[1] > line.bbox[1] + 2]
    if len(above) < 2 or not below:
        return None
    prev2, prev = above[-2], above[-1]
    nxt = below[0]
    if nxt.bbox[1] - line.bbox[1] > _SHIFT_MAX_VERTICAL_REACH_PT:
        return None
    if line.bbox[1] - prev2.bbox[1] > _SHIFT_MAX_VERTICAL_REACH_PT:
        return None
    rhythm = prev.bbox[1] - prev2.bbox[1]
    if rhythm < 8 or rhythm > 22:
        return None
    gap_before = line.bbox[1] - prev.bbox[1]
    gap_after = nxt.bbox[1] - line.bbox[1]
    if gap_before < rhythm + INDENT_THRESHOLD_PT:
        return None
    if gap_after < rhythm + INDENT_THRESHOLD_PT:
        return None
    return gap_before - rhythm


def _check_split_lines(entries: list[tuple[_Block, _Line]]) -> list[LayoutAnomaly]:
    """A sentence broken so one piece sits on a different baseline.

    The two pieces still meet end to end. A wrapped line starts again at
    the left margin, so it does not meet the line above at its right edge.
    """
    anomalies: list[LayoutAnomaly] = []
    seen: set[int] = set()
    for index, (block, line) in enumerate(entries):
        if id(line) in seen:
            continue
        for other_block, other in entries[index + 1 :]:
            pair = _abutting_pair(block, line, other_block, other)
            if pair is None:
                continue
            moved_block, moved, signed = pair
            seen.add(id(line))
            seen.add(id(other))
            anomalies.append(
                _anomaly(
                    "Spacing",
                    _block_for_line(moved_block, moved),
                    detected=_pt(moved.bbox[1]),
                    expected=_pt(moved.bbox[1] - signed),
                    baseline="The rest of this line sits on a different baseline",
                    deviation=_signed_pt(signed),
                    confidence=_delta_confidence(signed, VERTICAL_SHIFT_THRESHOLD_PT, 1.0),
                    evidence={
                        "kind": "vertical",
                        "vertical_delta_pt": round(signed, 2),
                        "comparable_count": 2,
                        "matching_count": 1,
                    },
                    location=f"Vertical position of line near y={round(moved.bbox[1])}",
                )
            )
            break
    return anomalies


def _abutting_pair(
    block: _Block,
    line: _Line,
    other_block: _Block,
    other: _Line,
) -> tuple[_Block, _Line, float] | None:
    dy = line.bbox[1] - other.bbox[1]
    if abs(dy) < INDENT_THRESHOLD_PT or abs(dy) > 40:
        return None
    left, left_block, right = (
        (line, block, other) if line.bbox[0] <= other.bbox[0] else (other, other_block, line)
    )
    gap = right.bbox[0] - left.bbox[2]
    if gap < -4 or gap > 8:
        return None
    if _line_width(left) < 120 or _line_width(right) < 60:
        return None
    if _letters_in(left) < MIN_BLOCK_CHARS or _letters_in(right) < MIN_BLOCK_CHARS:
        return None
    # The wider piece is the sentence that left the shared baseline.
    if _line_width(left) >= _line_width(right):
        moved, moved_block, other_y = left, left_block, right.bbox[1]
    else:
        right_block = other_block if right is other else block
        moved, moved_block, other_y = right, right_block, left.bbox[1]
    return moved_block, moved, moved.bbox[1] - other_y
