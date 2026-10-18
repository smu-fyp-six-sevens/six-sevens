"""Lines that no longer share an edge, centre, or baseline with the lines around them."""

from __future__ import annotations

from statistics import median
from typing import Literal, cast

from .common import (
    _anomaly, _Block, _block_for_line, _delta_confidence, _different_size, _edge_anomaly,
    LayoutAnomaly, _Line, _line_baseline, _line_width, _lines_by_page, _opening, _pt,
    _SHORT_LINE_PAGE_RATIO, _signed_pt, _x_overlap,
)
from .extraction import (
    _Rule,
)


# Generated documents place text on exact coordinates, so a designed line
# shares an edge with a neighbour to within a point. A moved line does not.
_ANCHOR_TOLERANCE_PT = 1.0
# Neighbours further up or down than this belong to another part of the page.
_ANCHOR_WINDOW_PT = 60.0
# The column the line left must have a member this close above or below it.
_ANCHOR_COLUMN_REACH_PT = 30.0
_ANCHOR_MIN_SHIFT_PT = 6.0
_ANCHOR_MAX_SHIFT_PT = 60.0
_ANCHOR_SIZE_TOLERANCE_PT = 0.6
# A heading or company name is set larger than the lines under it, so a
# column of any size counts when it starts right beside the line.
_MIXED_SIZE_COLUMN_REACH_PT = 20.0
# OCR boxes of one column wander by a few points, more on larger type.
_OCR_EDGE_TOLERANCE_RATIO = 0.15
# The page width the point thresholds were set on. A photo opens at its pixel
# size (an A4 scan at 150 dpi is 1240 wide), so its neighbours sit further apart.
_A4_WIDTH_PT = 595.0


def _anchor_window(line: _Line) -> float:
    if not line.ocr:
        return _ANCHOR_WINDOW_PT
    return _ANCHOR_WINDOW_PT * max(1.0, line.page_width / _A4_WIDTH_PT)


def _edge_tolerance(line: _Line, other: _Line) -> float:
    if not (line.ocr or other.ocr):
        return _ANCHOR_TOLERANCE_PT
    height = min(line.bbox[3] - line.bbox[1], other.bbox[3] - other.bbox[1])
    return max(_ANCHOR_TOLERANCE_PT, _OCR_EDGE_TOLERANCE_RATIO * height)


def _fits_drawn_cell(line: _Line, rules: list[_Rule]) -> bool:
    """Text that starts, ends, or centres on the cell drawn around it."""
    beside = [x for x, top, bottom in rules if top - 2 <= line.bbox[3] and bottom + 2 >= line.bbox[1]]
    x0, x1 = line.bbox[0], line.bbox[2]
    if any(abs(x - x0) <= _ANCHOR_TOLERANCE_PT or abs(x - x1) <= _ANCHOR_TOLERANCE_PT for x in beside):
        return True
    left = max((x for x in beside if x <= x0 + _ANCHOR_TOLERANCE_PT), default=None)
    right = min((x for x in beside if x >= x1 - _ANCHOR_TOLERANCE_PT), default=None)
    if left is None or right is None:
        return False
    return abs((left + right) / 2 - (x0 + x1) / 2) <= _ANCHOR_TOLERANCE_PT


def _repeated_line_keys(blocks: list[_Block]) -> set[tuple[str, int]]:
    """Text that sits at the same left edge on several pages is template furniture."""
    seen: dict[tuple[str, int], set[int]] = {}
    for block in blocks:
        for line in block.lines:
            key = (_opening(line.text).lower(), round(line.bbox[0]))
            seen.setdefault(key, set()).add(line.page)
    return {key for key, pages in seen.items() if len(pages) > 1}


def _shares_an_edge(
    line: _Line,
    others: list[_Line],
    rules: list[_Rule] | None = None,
    sides: tuple[str, ...] = ("left", "right"),
) -> bool:
    x0, x1 = line.bbox[0], line.bbox[2]
    if rules and _fits_drawn_cell(line, rules):
        return True
    mid = (x0 + x1) / 2
    if abs(mid - line.page_width / 2) <= 2 * _ANCHOR_TOLERANCE_PT:
        return True
    if any(
        ("left" in sides and abs(other.bbox[0] - x0) <= _edge_tolerance(line, other))
        or ("right" in sides and abs(other.bbox[2] - x1) <= _edge_tolerance(line, other))
        for other in others
    ):
        return True
    # Two lines of different length can share a centre by chance. A
    # centred group has at least two others on that axis.
    centred = sum(
        1
        for other in others
        if abs((other.bbox[0] + other.bbox[2]) / 2 - mid) <= _edge_tolerance(line, other)
    )
    return centred >= 2 or _centred_stack(line, others) or _heads_a_column(line, others)


def _centred_stack(line: _Line, others: list[_Line]) -> bool:
    """A two-line heading centred on itself ("PROPOSAL" over "BUDGET").

    Only the line straight above or below, in the same type, counts: a pair that
    close sharing a centre is set that way. A line pushed aside leaves the centre.
    """
    height = line.bbox[3] - line.bbox[1]
    return any(
        _x_overlap(line, other) > 0
        and not _different_size(line, other)
        and max(other.bbox[1] - line.bbox[3], line.bbox[1] - other.bbox[3]) <= 0.5 * height
        and abs(_mid_line(other) - _mid_line(line)) <= _edge_tolerance(line, other)
        for other in others
    )


def _left_column(line: _Line, others: list[_Line]) -> tuple[str, float, list[_Line]] | None:
    """Nearest shared left or right edge, a short shove away.

    The column is the lines in the same type size, or any lines when one
    sits right beside this line.
    """
    best: tuple[str, float, list[_Line]] | None = None
    best_dist = _ANCHOR_MAX_SHIFT_PT + 1
    for side, index in (("left", 0), ("right", 2)):
        mine = line.bbox[index]
        clusters: list[list[_Line]] = []
        for other in sorted(others, key=lambda item: item.bbox[index]):
            if clusters and other.bbox[index] - clusters[-1][0].bbox[index] <= _ANCHOR_TOLERANCE_PT:
                clusters[-1].append(other)
            else:
                clusters.append([other])
        for cluster in clusters:
            near = [
                other
                for other in cluster
                if abs(other.bbox[1] - line.bbox[1]) <= _ANCHOR_COLUMN_REACH_PT
            ]
            if not near:
                continue
            same_size = [
                other
                for other in cluster
                if line.size is None
                or other.size is None
                or abs(other.size - line.size) <= _ANCHOR_SIZE_TOLERANCE_PT
            ]
            if len(same_size) >= 2 and any(other in near for other in same_size):
                members = same_size
            elif len(cluster) >= 2 and any(
                abs(other.bbox[1] - line.bbox[1]) <= _MIXED_SIZE_COLUMN_REACH_PT
                for other in cluster
            ):
                members = cluster
            else:
                continue
            if not any(_x_overlap(line, other) > 0 for other in members):
                continue
            edge = float(median(other.bbox[index] for other in members))
            dist = abs(mine - edge)
            if _ANCHOR_MIN_SHIFT_PT <= dist <= _ANCHOR_MAX_SHIFT_PT and dist < best_dist:
                best, best_dist = (side, edge, members), dist
    return best


def _check_unanchored_lines(
    blocks: list[_Block], rules: dict[int, list[_Rule]] | None = None
) -> list[LayoutAnomaly]:
    """A line that lines up with nothing around it, a short way off a column that does.

    Every designed line shares a left edge, right edge, or centre with a
    neighbour, or sits on the page centre. A field nudged sideways moves
    all three, so it no longer matches anything nearby, while the column
    it came from is still there a few points away. Text repeated at the
    same place on other pages is the template, not an edit.
    """
    repeated = _repeated_line_keys(blocks)
    anomalies: list[LayoutAnomaly] = []
    for entries in _lines_by_page(blocks, min_alnum=2).values():
        for block, line in entries:
            if (_opening(line.text).lower(), round(line.bbox[0])) in repeated:
                continue
            # A paragraph moved as a whole keeps its own lines in step, so
            # only other blocks can show where it belongs.
            others = [
                other
                for owner, other in entries
                if owner is not block
                and 2 < abs(other.bbox[1] - line.bbox[1]) <= _anchor_window(line)
            ]
            if _shares_an_edge(line, others, (rules or {}).get(line.page)):
                continue
            column = _left_column(line, others)
            if column is None:
                continue
            side, edge, members = column
            detected = line.bbox[0 if side == "left" else 2]
            signed = detected - edge
            anomalies.append(
                _edge_anomaly(
                    _block_for_line(block, line),
                    side=cast(Literal["left", "right"], side),
                    kind="indentation",
                    detected=detected,
                    expected=edge,
                    expected_left=edge if side == "left" else edge - _line_width(line),
                    signed=signed,
                    comparable=len(members) + 1,
                    matching=len(members),
                    baseline=f"The {len(members)} lines around it {'start' if side == 'left' else 'end'} at {_pt(edge)}",
                    confidence=_delta_confidence(signed, _ANCHOR_MIN_SHIFT_PT, 1.0),
                    unanchored=True,
                )
            )
    return anomalies


def _mid_line(line: _Line) -> float:
    return (line.bbox[0] + line.bbox[2]) / 2


# A column header is centred over its values by eye, not to the point.
_HEADER_CENTRE_PT = 3.0


def _heads_a_column(line: _Line, others: list[_Line]) -> bool:
    """A table's column header ("ITEM #") over a column of values that share a centre.

    The header sits inside the column's span with its centre near theirs. Its left edge
    lines up with nothing, which reads as a shove without this. A header pushed aside
    sticks out past the column, so it is still caught.
    """
    below = [other for other in others if other.bbox[1] >= line.bbox[3] and _x_overlap(line, other) > 0]
    for anchor in below:
        centre = _mid_line(anchor)
        column = [other for other in below if abs(_mid_line(other) - centre) <= _ANCHOR_TOLERANCE_PT]
        if len(column) < 2:
            continue
        left = min(other.bbox[0] for other in column) - _ANCHOR_TOLERANCE_PT
        right = max(other.bbox[2] for other in column) + _ANCHOR_TOLERANCE_PT
        if left <= line.bbox[0] and line.bbox[2] <= right and abs(_mid_line(line) - centre) <= _HEADER_CENTRE_PT:
            return True
    return False


def _stacked_pairs(entries: list[tuple[_Block, _Line]]) -> list[tuple[_Line, _Line, float]]:
    """Each short line with the short line straight under it, and their left-edge offset.

    On a form a label sits over its value. The offset between the two is
    part of the design, so the same offset repeats on every pair.
    """
    short = [
        line
        for _, line in entries
        if _line_width(line) < _SHORT_LINE_PAGE_RATIO * line.page_width
        and sum(char.isalnum() for char in line.text) >= 2
    ]
    pairs: list[tuple[_Line, _Line, float]] = []
    for upper in short:
        height = upper.bbox[3] - upper.bbox[1]
        below = [
            lower
            for lower in short
            if lower is not upper
            and _x_overlap(upper, lower) > 0
            and -2 < lower.bbox[1] - upper.bbox[3] <= height
        ]
        if below:
            lower = min(below, key=lambda item: item.bbox[1])
            pairs.append((upper, lower, lower.bbox[0] - upper.bbox[0]))
    return pairs


def _check_pair_offsets(
    blocks: list[_Block], rules: dict[int, list[_Rule]] | None = None
) -> list[LayoutAnomaly]:
    """A label and its value that no longer share the page's usual offset.

    The usual offset is the one most pairs on the page share. A pair that
    is a short shove off it, with no other pair sharing its own offset, was
    edited. A designed indent, such as an item under a heading, repeats
    across pairs, so it is not flagged. Of the two lines, the one that also
    lines up with nothing else nearby is the one that moved.
    """
    repeated = _repeated_line_keys(blocks)
    owner = {id(line): block for block in blocks for line in block.lines}
    anomalies: list[LayoutAnomaly] = []
    for page, entries in _lines_by_page(blocks).items():
        pairs = _stacked_pairs(entries)
        offsets = [offset for _, _, offset in pairs]

        def support(value: float) -> int:
            return sum(1 for other in offsets if abs(other - value) <= _ANCHOR_TOLERANCE_PT)

        usual = max(offsets, key=support, default=None)
        if usual is None or support(usual) < 2:
            continue
        for upper, lower, offset in pairs:
            drift = offset - usual
            if not _ANCHOR_MIN_SHIFT_PT <= abs(drift) <= _ANCHOR_MAX_SHIFT_PT:
                continue
            if support(offset) > 1:
                continue
            # Right-aligned amounts or a centred caption differ on the left
            # by design; they still share the other edge or the centre.
            if (
                abs(upper.bbox[2] - lower.bbox[2]) <= _edge_tolerance(upper, lower)
                or abs(_mid_line(upper) - _mid_line(lower)) <= _edge_tolerance(upper, lower)
            ):
                continue
            loose = []
            for line, partner in ((upper, lower), (lower, upper)):
                if (_opening(line.text).lower(), round(line.bbox[0])) in repeated:
                    continue
                others = [
                    other
                    for block, other in entries
                    if block is not owner[id(line)]
                    and other is not partner
                    and 2 < abs(other.bbox[1] - line.bbox[1]) <= _anchor_window(line)
                ]
                if not _shares_an_edge(line, others, (rules or {}).get(page)):
                    loose.append((line, partner))
            if len(loose) != 1:
                continue
            line, partner = loose[0]
            signed = drift if line is lower else -drift
            expected = partner.bbox[0] + (usual if line is lower else -usual)
            anomalies.append(
                _edge_anomaly(
                    _block_for_line(owner[id(line)], line),
                    side="left",
                    detected=line.bbox[0],
                    expected=expected,
                    signed=signed,
                    comparable=support(usual) + 1,
                    matching=support(usual),
                    baseline=(
                        f"The other label and value pairs on this page are offset by {_pt(usual)}"
                    ),
                    confidence=_delta_confidence(signed, _ANCHOR_MIN_SHIFT_PT, 1.0),
                    unanchored=True,
                )
            )
    return anomalies


# A cell this far off its row's baseline has left the row; further is another row.
_ROW_MIN_SHIFT_PT = 3.0
_ROW_MAX_SHIFT_PT = 16.0
# Line pitch that differs from the stack's by this much is not the stack's rhythm.
_PITCH_MIN_SHIFT_PT = 3.0


def _stack_neighbours(line: _Line, others: list[_Line]) -> tuple[_Line | None, _Line | None]:
    base = _line_baseline(line)
    stacked = [other for other in others if _x_overlap(line, other) > 0]
    above = [other for other in stacked if _line_baseline(other) < base - 2]
    below = [other for other in stacked if _line_baseline(other) > base + 2]
    return (
        max(above, key=_line_baseline, default=None),
        min(below, key=_line_baseline, default=None),
    )


def _row_baselines(others: list[_Line]) -> dict[float, list[_Line]]:
    """Baselines shared by two or more lines, each with the cells on it."""
    rows: list[list[_Line]] = []
    for other in sorted(others, key=_line_baseline):
        if rows and _line_baseline(other) - _line_baseline(rows[-1][0]) <= _ANCHOR_TOLERANCE_PT:
            rows[-1].append(other)
        else:
            rows.append([other])
    return {_line_baseline(row[0]): row for row in rows if len(row) >= 2}


def _row_slot_left_empty(line: _Line, row_baseline: float, others: list[_Line]) -> bool:
    """The row has cells on it but none under this line, and the line's
    column fills that slot on another row. A wrapped cell still has its
    first line in the row above, so its slot is not empty."""
    rows = _row_baselines(others)
    row = next(
        (cells for base, cells in rows.items() if abs(base - row_baseline) <= _ANCHOR_TOLERANCE_PT),
        None,
    )
    if row is None or any(_x_overlap(line, cell) > 0 for cell in row):
        return False
    return any(
        any(_x_overlap(line, cell) > 0 for cell in cells)
        for base, cells in rows.items()
        if abs(base - row_baseline) > _ANCHOR_TOLERANCE_PT
    )


# Lines of one wrapped cell that are this far apart, repeated in this many
# cells on the page, are the table's own line spacing.
_CELL_PITCH_TOLERANCE_PT = 1.0
_CELL_PITCH_MIN_CELLS = 3


def _cell_rhythm(entries: list[tuple[_Block, _Line]]) -> set[int]:
    """Lines that sit in a wrapped cell at the page's usual spacing for that column.

    A transaction description wraps over several lines and a date cell puts
    its time underneath; the whole cell is centred on its row, so each line
    sits off the row's baseline by design. Every such cell on the page uses
    the same spacing. A nudged line breaks its own cell's spacing, and an
    injected or pasted line usually arrives as a block of its own.
    """
    by_cell: dict[tuple[int, int], list[_Line]] = {}
    for block, line in entries:
        by_cell.setdefault((id(block), round(line.bbox[0])), []).append(line)
    gaps: list[tuple[int, _Line, _Line, float]] = []
    for (_, column), lines in by_cell.items():
        ordered = sorted(lines, key=_line_baseline)
        for upper, lower in zip(ordered, ordered[1:]):
            gap = _line_baseline(lower) - _line_baseline(upper)
            if 0 < gap <= 2 * (upper.bbox[3] - upper.bbox[1]):
                gaps.append((column, upper, lower, gap))
    in_rhythm: set[int] = set()
    for column, upper, lower, gap in gaps:
        cells = sum(
            1
            for other_column, _, _, other_gap in gaps
            if other_column == column and abs(other_gap - gap) <= _CELL_PITCH_TOLERANCE_PT
        )
        if cells >= _CELL_PITCH_MIN_CELLS:
            in_rhythm.update((id(upper), id(lower)))
    return in_rhythm


def _vertical_anchor_delta(
    line: _Line, others: list[_Line]
) -> tuple[float, str] | None:
    """How far a line sits off the row or rhythm it belongs to, if it does.

    A row: the line sits a few points off a neighbour's baseline that two
    other cells still share, and nothing else shares the line's own.
    A stack: the gaps above and below add up to two of the stack's usual
    pitch, but split unevenly, so the line slid inside its slot.
    """
    base = _line_baseline(line)
    if any(abs(_line_baseline(other) - base) <= _ANCHOR_TOLERANCE_PT for other in others):
        return None
    for other in sorted(others, key=lambda item: abs(_line_baseline(item) - base)):
        delta = base - _line_baseline(other)
        if abs(delta) < _ROW_MIN_SHIFT_PT:
            continue
        if abs(delta) > _ROW_MAX_SHIFT_PT:
            break
        # A row's cells share a size: a heading in another size isn't one of them.
        if _different_size(line, other):
            continue
        if _row_slot_left_empty(line, _line_baseline(other), others):
            return delta, "row"
    above, below = _stack_neighbours(line, others)
    if above is None or below is None:
        return None
    # A heading set in another size from the lines above and below it keeps its own
    # spacing; it isn't part of their rhythm.
    if _different_size(line, above) and _different_size(line, below):
        return None
    gap_above = base - _line_baseline(above)
    gap_below = _line_baseline(below) - base
    pitches: list[float] = []
    for member in (above, below):
        up, down = _stack_neighbours(member, [item for item in others if item is not member])
        for neighbour in (up, down):
            if neighbour is not None:
                pitches.append(abs(_line_baseline(member) - _line_baseline(neighbour)))
    pitch_counts = [
        p for p in pitches if sum(abs(q - p) <= _ANCHOR_TOLERANCE_PT for q in pitches) >= 2
    ]
    if not pitch_counts:
        return None
    pitch = float(median(pitch_counts))
    if abs(gap_above + gap_below - 2 * pitch) > 2 * _ANCHOR_TOLERANCE_PT:
        return None
    delta = gap_above - pitch
    if abs(delta) < _PITCH_MIN_SHIFT_PT:
        return None
    return delta, "stack"


# Furniture repeated on this many pages at one spot sets where it belongs.
_FURNITURE_MIN_PAGES = 3
# An issuer's name, address, and legal lines sit in the top and bottom bands;
# a transaction table between them repeats text by coincidence.
_FURNITURE_TOP_BAND = 0.20
_FURNITURE_BOTTOM_BAND = 0.85


def _in_furniture_band(line: _Line) -> bool:
    return (
        line.bbox[3] <= line.page_height * _FURNITURE_TOP_BAND
        or line.bbox[1] >= line.page_height * _FURNITURE_BOTTOM_BAND
    )


def _check_moved_furniture(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """Text repeated at one spot on most pages, but a short way off on one.

    A bank's name and address and a statement's column headings are
    printed in the same place on every page, so the other pages
    show where the odd one belongs: the document is its own reference. Two
    pages cannot say which copy moved, so the spot must be shared by at
    least two other pages.
    """
    # The top header line and bottom footer line of each page have their own
    # position check; the furniture under them (an issuer's address) does not.
    chrome: set[int] = set()
    for role, pick in (("header", min), ("footer", max)):
        by_page: dict[int, list[_Line]] = {}
        for block in blocks:
            if block.role == role:
                for line in block.lines:
                    by_page.setdefault(line.page, []).append(line)
        for lines in by_page.values():
            edge = pick(line.bbox[1] for line in lines)
            chrome.update(id(line) for line in lines if abs(line.bbox[1] - edge) <= _ANCHOR_TOLERANCE_PT)
    copies: dict[str, list[tuple[_Block, _Line]]] = {}
    for block in blocks:
        for line in block.lines:
            if id(line) in chrome or not _in_furniture_band(line):
                continue
            text = " ".join(line.text.split()).lower()
            if sum(char.isalnum() for char in text) >= 4:
                copies.setdefault(text, []).append((block, line))
    anomalies: list[LayoutAnomaly] = []
    for entries in copies.values():
        pages = [line.page for _, line in entries]
        # Text that repeats within a page ("GIRO" on every transaction row) is
        # content, not furniture.
        if len(set(pages)) < _FURNITURE_MIN_PAGES or len(set(pages)) != len(pages):
            continue
        for block, line in entries:
            spots = [(other.bbox[0], _line_baseline(other)) for _, other in entries if other.page != line.page]
            agree = [
                spot
                for spot in spots
                if sum(
                    1
                    for x, y in spots
                    if abs(x - spot[0]) <= _ANCHOR_TOLERANCE_PT and abs(y - spot[1]) <= _ANCHOR_TOLERANCE_PT
                )
                >= _FURNITURE_MIN_PAGES - 1
            ]
            if not agree:
                continue
            x, y = agree[0]
            dx = line.bbox[0] - x
            dy = _line_baseline(line) - y
            if not _ANCHOR_MIN_SHIFT_PT <= max(abs(dx), abs(dy)) <= _ANCHOR_MAX_SHIFT_PT:
                continue
            pages = len(agree)
            if abs(dx) >= abs(dy):
                anomalies.append(
                    _edge_anomaly(
                        _block_for_line(block, line),
                        side="left",
                        detected=line.bbox[0],
                        expected=x,
                        signed=dx,
                        comparable=pages + 1,
                        matching=pages,
                        baseline=f"The same text starts at {_pt(x)} on {pages} other pages",
                        confidence=_delta_confidence(dx, _ANCHOR_MIN_SHIFT_PT, 1.0),
                        unanchored=True,
                    )
                )
            else:
                anomalies.append(
                    _anomaly(
                        "Spacing",
                        _block_for_line(block, line),
                        detected=_pt(_line_baseline(line)),
                        expected=_pt(y),
                        baseline=f"The same text sits at {_pt(y)} on {pages} other pages",
                        deviation=_signed_pt(dy),
                        confidence=_delta_confidence(dy, _ANCHOR_MIN_SHIFT_PT, 1.0),
                        evidence={
                            "kind": "vertical",
                            "vertical_delta_pt": round(dy, 2),
                            "direction": "lower" if dy > 0 else "higher",
                            "unanchored": True,
                        },
                    )
                )
    return anomalies


def _check_vertical_unanchored_lines(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """A line nudged up or down off its table row, or off its stack's line pitch."""
    repeated = _repeated_line_keys(blocks)
    anomalies: list[LayoutAnomaly] = []
    for entries in _lines_by_page(blocks, min_alnum=2).values():
        in_rhythm = _cell_rhythm(entries)
        for block, line in entries:
            if (_opening(line.text).lower(), round(line.bbox[0])) in repeated:
                continue
            if id(line) in in_rhythm:
                continue
            others = [
                other
                for _, other in entries
                if other is not line
                and abs(other.bbox[1] - line.bbox[1]) <= _ANCHOR_WINDOW_PT
            ]
            found = _vertical_anchor_delta(line, others)
            if found is None:
                continue
            delta, source = found
            where = "the row beside it" if source == "row" else "the lines around it"
            anomalies.append(
                _anomaly(
                    "Spacing",
                    _block_for_line(block, line),
                    detected=_pt(_line_baseline(line)),
                    expected=_pt(_line_baseline(line) - delta),
                    baseline=f"Baseline expected in line with {where}",
                    deviation=_signed_pt(delta),
                    confidence=_delta_confidence(delta, _ROW_MIN_SHIFT_PT, 1.0),
                    evidence={
                        "kind": "vertical",
                        "vertical_delta_pt": round(delta, 2),
                        "direction": "lower" if delta > 0 else "higher",
                        "unanchored": True,
                    },
                )
            )
    return anomalies
