"""Left indent and right edge checks on whole paragraphs."""

from __future__ import annotations

from statistics import median

from .common import (
    ALIGN_EDGE_TOLERANCE_PT, _assessed, _Block, _bucket_pt, _by_page, _cannot,
    CheckAssessment, _delta_confidence, _dominant, _edge_anomaly, _has_strong_majority,
    INDENT_THRESHOLD_PT, LayoutAnomaly, _LOCAL_WINDOW, MIN_BLOCK_CHARS, _pct, _pt,
    SHORT_WIDTH_RATIO,
)
from .structure import (
    _body_blocks, _column_of, _content_column, _merge_checked, _page_columns, _peer_phrase,
    _reaches_column_right, _sets_from_groups, StructuralGroup, _width_band,
)


def _check_indentation(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        if len(sets) > 1:
            return _merge_checked(_check_indentation(group, _scoped=True) for group in sets)
    body = _body_blocks(blocks)
    if len(body) < 2:
        return _cannot(
            "Not enough comparable lines to assess indentation "
            f"({len(body)} found, need at least 2)"
        )

    anomalies: list[LayoutAnomaly] = []
    flagged = False
    for page_blocks in _by_page(body).values():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(page_blocks)
        if len(columns) >= 2:
            # Dates, descriptions, and amounts each keep their own left
            # edge. Only a line that sits on none of those columns is an indent.
            flagged = True
            for block in page_blocks:
                if _column_of(block, columns) is not None:
                    continue
                nearest = min(columns, key=lambda column: abs(block.x0 - column))
                signed_left = block.x0 - nearest
                if abs(signed_left) < INDENT_THRESHOLD_PT:
                    continue
                # Further than a shove is the next column, not one moved line.
                if abs(signed_left) > _COLUMN_SHIFT_MAX_PT:
                    continue
                # A short label or amount sitting between columns is not a
                # shoved paragraph. Only a line that fills the content span is.
                span = max(b.x1 for b in page_blocks) - min(b.x0 for b in page_blocks)
                if span <= 0 or block.width < SHORT_WIDTH_RATIO * span:
                    continue
                matching = sum(
                    1 for peer in page_blocks if _column_of(peer, columns) == nearest
                )
                comparable = len(page_blocks)
                anomalies.append(
                    _edge_anomaly(
                        block,
                        side="left",
                        detected=block.x0,
                        expected=nearest,
                        signed=signed_left,
                        comparable=comparable,
                        matching=matching,
                        baseline=(
                            f"{_pct(matching, comparable)} of {_peer_phrase(page_blocks)} "
                            f"start at {_pt(nearest)}"
                        ),
                        confidence=_delta_confidence(
                            abs(signed_left), INDENT_THRESHOLD_PT, matching / comparable
                        ),
                    )
                )
            continue
        column = _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        full = [b for b in page_blocks if _width_band(b, content_left, content_right) == "full"]
        if len(full) < 2:
            # A one-page form is short labels, not full-width clauses.
            # Compare those labels when they share a left edge.
            short_findings = _short_line_indents(page_blocks)
            if short_findings is not None:
                flagged = True
                anomalies.extend(short_findings)
            continue
        flagged = True
        page_left = _dominant(_bucket_pt(b.x0) for b in full)
        # Half the paragraphs indented leaves no majority edge. The left-most
        # edge they share is then the body column, so a paragraph on it is
        # not pushed left by the indented neighbours around it.
        column_left: float | None = None
        if page_left is None:
            leftmost = min(b.x0 for b in full)
            on_edge = sum(1 for b in full if b.x0 - leftmost < INDENT_THRESHOLD_PT)
            column_left = leftmost if on_edge >= 2 else None

        for index, block in enumerate(full):
            if column_left is not None and block.x0 - column_left < INDENT_THRESHOLD_PT:
                continue
            window = full[max(0, index - _LOCAL_WINDOW) : index] + full[index + 1 : index + 1 + _LOCAL_WINDOW]
            expected_left = _dominant(_bucket_pt(b.x0) for b in window)
            if expected_left is None:
                expected_left = page_left
            # No majority edge leaves nothing to measure against.
            if expected_left is None:
                continue
            signed_left = block.x0 - expected_left
            if not INDENT_THRESHOLD_PT <= abs(signed_left) <= _COLUMN_SHIFT_MAX_PT:
                continue
            if not _reaches_column_right(block, content_right):
                continue

            matching = sum(
                1 for peer in full if abs(peer.x0 - expected_left) < INDENT_THRESHOLD_PT
            )
            anomalies.append(
                _edge_anomaly(
                    block,
                    side="left",
                    detected=block.x0,
                    expected=expected_left,
                    signed=signed_left,
                    comparable=len(full),
                    matching=matching,
                    baseline=(
                        f"{_pct(matching, len(full))} of {_peer_phrase(full)} "
                        f"start at {_pt(expected_left)}"
                    ),
                    confidence=_delta_confidence(
                        abs(signed_left), INDENT_THRESHOLD_PT, matching / len(full)
                    ),
                )
            )

    if not flagged:
        return _cannot(
            "Not enough full-width body paragraphs on any page to assess indentation"
        )
    return anomalies, _assessed()


def _short_line_indents(page_blocks: list[_Block]) -> list[LayoutAnomaly] | None:
    """Left-edge outliers among short lines that share a width.

    None means there is no majority edge, so indentation stays unassessed.
    An empty list means the lines were compared and none is shifted.
    A line much wider or narrower than the majority is a different column
    (a value beside a label), not an indent.
    """
    if len(page_blocks) < 4:
        return None
    expected_left = _dominant(_bucket_pt(block.x0) for block in page_blocks)
    if expected_left is None:
        return None
    matching = [
        block
        for block in page_blocks
        if abs(block.x0 - expected_left) < INDENT_THRESHOLD_PT
    ]
    if not _has_strong_majority(len(matching), len(page_blocks)):
        return None
    typical_width = float(median(block.width for block in matching))
    anomalies: list[LayoutAnomaly] = []
    for block in page_blocks:
        signed = block.x0 - expected_left
        if abs(signed) < INDENT_THRESHOLD_PT or abs(signed) > _COLUMN_SHIFT_MAX_PT:
            continue
        width_gap = abs(block.width - typical_width)
        if width_gap > max(40.0, 0.45 * typical_width):
            continue
        anomalies.append(
            _edge_anomaly(
                block,
                side="left",
                detected=block.x0,
                expected=expected_left,
                signed=signed,
                comparable=len(page_blocks),
                matching=len(matching),
                baseline=(
                    f"{_pct(len(matching), len(page_blocks))} of comparable lines "
                    f"start at {_pt(expected_left)}"
                ),
                confidence=_delta_confidence(
                    abs(signed), INDENT_THRESHOLD_PT, len(matching) / len(page_blocks)
                ),
            )
        )
    return anomalies


def _consistent_right_edge(block: _Block) -> float | None:
    """
    Right edge of a wrapped column, ignoring a short last line. A single
    line that simply does not fill the column is not a reflowed paste.
    Ragged left-aligned copy (intermediate lines end at different x) has
    no stable right indent to compare, so returns None.
    """
    if len(block.lines) < 2:
        return None
    lines = block.lines[:-1]
    rights = [line.bbox[2] for line in lines]
    if max(rights) - min(rights) > ALIGN_EDGE_TOLERANCE_PT:
        return None
    return float(median(rights))


def _right_edge_census(blocks: list[_Block]) -> list[float]:
    """Every stable right edge in the document, across comparable groups.

    A footnote or disclaimer column set wider than the body repeats on
    later pages, two paragraphs at a time, and a clause heading splits
    those pairs into separate groups. Counting the blocks that share an
    edge inside one group never reaches the run that marks it as layout.
    """
    edges: list[float] = []
    for page_blocks in _by_page(blocks).values():
        column = _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        for block in page_blocks:
            if _width_band(block, content_left, content_right) != "full":
                continue
            edge = _consistent_right_edge(block)
            if edge is not None:
                edges.append(edge)
    return edges


def _check_right_edge(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
    _census: list[float] | None = None,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    """
    Left indent can match while the paragraph still ends short or long of
    the column — a pasted clause that was reflowed to a different width.
    Only full-width blocks with a stable right edge (justified / wrapped
    column, not ragged left-aligned copy) are compared.
    """
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        _census = _right_edge_census(blocks)
        if len(sets) > 1:
            return _merge_checked(
                _check_right_edge(group, _scoped=True, _census=_census) for group in sets
            )
    body = _body_blocks(blocks)
    if len(body) < 2:
        return _cannot(
            "Not enough comparable body paragraphs to assess right-edge consistency "
            f"({len(body)} found, need at least 2)"
        )

    anomalies: list[LayoutAnomaly] = []
    flagged = False
    edged_by_page: dict[int, list[tuple[_Block, float]]] = {}
    for page, page_blocks in _by_page(body).items():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        column = _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        full = [b for b in page_blocks if _width_band(b, content_left, content_right) == "full"]
        edged = [(b, edge) for b in full if (edge := _consistent_right_edge(b)) is not None]
        if len(edged) < 2:
            continue
        edged_by_page[page] = edged
        flagged = True

    document_edges = _census
    if document_edges is None:
        document_edges = [
            edge for page_edged in edged_by_page.values() for _, edge in page_edged
        ]

    for edged in edged_by_page.values():
        page_right = _dominant(_bucket_pt(edge) for _, edge in edged)

        for index, (block, right_edge) in enumerate(edged):
            window = edged[max(0, index - _LOCAL_WINDOW) : index] + edged[index + 1 : index + 1 + _LOCAL_WINDOW]
            expected_right = _dominant(_bucket_pt(edge) for _, edge in window)
            if expected_right is None:
                expected_right = page_right
            if expected_right is None:
                continue
            signed = right_edge - float(expected_right)
            if abs(signed) < INDENT_THRESHOLD_PT:
                continue
            # Several blocks that share this edge are a region, not one paste.
            same_edge = sum(
                1 for edge in document_edges if abs(edge - right_edge) < INDENT_THRESHOLD_PT
            )
            if same_edge >= 3:
                continue
            matching = sum(
                1 for _, edge in edged if abs(edge - float(expected_right)) < INDENT_THRESHOLD_PT
            )
            if not _has_strong_majority(matching, len(edged)):
                continue
            anomalies.append(
                _edge_anomaly(
                    block,
                    side="right",
                    detected=right_edge,
                    expected=float(expected_right),
                    signed=signed,
                    comparable=len(edged),
                    matching=matching,
                    baseline=(
                        f"{_pct(matching, len(edged))} of comparable body paragraphs "
                        f"end at {_pt(float(expected_right))}"
                    ),
                    confidence=_delta_confidence(
                        abs(signed), INDENT_THRESHOLD_PT, matching / len(edged)
                    ),
                    location=f"Right edge of body paragraph near y={round(block.y0)}",
                )
            )

    if not flagged:
        return _cannot(
            "Not enough full-width body paragraphs with a stable right edge "
            "to assess right-edge consistency"
        )
    return anomalies, _assessed()


def _check_protruding_right(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> list[LayoutAnomaly]:
    """A body or table block whose right edge sticks past that role's usual edge.

    Addresses and signatures keep their own width, so they are not the
    baseline and they are not flagged here. The edge is compared across
    pages. Fewer than three pages is not a pattern. A whole page that is
    wider is left to the margin check.
    """
    if not _scoped:
        sets = _sets_from_groups(blocks, groups, collapse_sections=True)
        if len(sets) > 1:
            return [a for group in sets for a in _check_protruding_right(group, _scoped=True)]
    peers = [
        block
        for block in blocks
        if block.role in _RIGHT_EDGE_ROLES and block.char_count >= MIN_BLOCK_CHARS
    ]
    by_page = _by_page(peers)
    if len(by_page) < 3:
        return []

    page_max = {
        page: max(block.x1 for block in page_blocks)
        for page, page_blocks in by_page.items()
    }
    established = float(median(page_max.values()))
    protruding_by_page: dict[int, list[_Block]] = {}
    for page, page_blocks in by_page.items():
        protruding = [
            block
            for block in page_blocks
            if block.x1 - established >= INDENT_THRESHOLD_PT
        ]
        if len(protruding) == 0 or len(protruding) > 2:
            continue
        protruding_by_page[page] = protruding

    anomalies: list[LayoutAnomaly] = []
    for protruding in protruding_by_page.values():
        for block in protruding:
            # The same wider edge on more than one page is a second column
            # the document keeps for footnotes and disclaimers, not an edit.
            # The per-page cap above never sees it: such a column runs two
            # paragraphs per page.
            shared_pages = sum(
                1
                for others in protruding_by_page.values()
                if any(abs(other.x1 - block.x1) < INDENT_THRESHOLD_PT for other in others)
            )
            if shared_pages > 1:
                continue
            signed = block.x1 - established
            anomalies.append(
                _edge_anomaly(
                    block,
                    side="right",
                    detected=block.x1,
                    expected=established,
                    signed=signed,
                    comparable=len(page_max),
                    matching=sum(
                        1
                        for edge in page_max.values()
                        if abs(edge - established) < INDENT_THRESHOLD_PT
                    ),
                    baseline=f"Other {_peer_phrase(peers)} end at {_pt(established)}",
                    confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, 1.0),
                    location=f"Right edge of {_peer_phrase([block])} near y={round(block.y0)}",
                )
            )
    return anomalies


# How far a neighbouring line may sit and still be the one this line
# moved away from. A 14pt vertical shove on top of a normal leading
# still lands inside this window.
_SHIFT_NEIGHBOR_WINDOW_PT = 56.0
# A left-edge gap bigger than this is another column, not a shove.
_SHIFT_MAX_HORIZONTAL_PT = 96.0
# A value that leaves its own column by more than this has joined the
# next column, not slid off the one it shares with the lines beside it.
_COLUMN_SHIFT_MAX_PT = 48.0
# Nearest stacked line further than this belongs to another section.
_SHIFT_MAX_VERTICAL_REACH_PT = 120.0
_STACK_OVERLAP_PT = 24.0
_RIGHT_EDGE_ROLES = {"body", "table"}
