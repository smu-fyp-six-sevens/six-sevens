"""Line spacing and paragraph spacing checks."""

from __future__ import annotations

from statistics import median

from .common import (
    _anomaly, _assessed, _Block, _by_page, _cannot, CheckAssessment, _delta_confidence,
    LayoutAnomaly, LINE_SPACING_THRESHOLD_PT, _LOCAL_WINDOW, PARA_SPACING_THRESHOLD_PT,
    _pct, _pt, _signed_pt, _snippet,
)
from .structure import (
    _body_blocks, _has_spacing_rhythm, _merge_checked, _page_columns, _sets_from_groups,
    StructuralGroup,
)


def _line_leadings(block: _Block) -> list[float]:
    """Baseline-to-baseline distance between consecutive lines in a block."""
    ordered = sorted(block.lines, key=lambda line: line.bbox[1])
    leadings: list[float] = []
    for prev, curr in zip(ordered, ordered[1:]):
        leading = curr.bbox[1] - prev.bbox[1]
        if leading > 0:
            leadings.append(leading)
    return leadings


def _check_line_spacing(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        if len(sets) > 1:
            return _merge_checked(_check_line_spacing(group, _scoped=True) for group in sets)
    body = [b for b in _body_blocks(blocks) if len(b.lines) >= 2]
    if not _has_spacing_rhythm(body):
        return _cannot("Table rows and card fields have no line rhythm to compare")
    if len(body) < 2:
        return _cannot(
            "Not enough multi-line body paragraphs to assess line spacing "
            f"({len(body)} found, need at least 2)"
        )

    anomalies: list[LayoutAnomaly] = []
    assessed_any = False
    for page_blocks in _by_page(body).values():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        # A statement's date and description columns do not share a line
        # rhythm. Spacing on those pages is the table, not a tampered paragraph.
        if len(_page_columns(page_blocks)) >= 2:
            continue
        leadings_by_block = [(b, _line_leadings(b)) for b in page_blocks]
        all_leadings = [lead for _, leads in leadings_by_block for lead in leads]
        if len(all_leadings) < 3:
            continue
        assessed_any = True
        page_expected = float(median(all_leadings))
        for index, (block, leads) in enumerate(leadings_by_block):
            if not leads:
                continue
            window_leads = [
                lead
                for j, (_, other) in enumerate(leadings_by_block)
                if j != index and abs(j - index) <= _LOCAL_WINDOW
                for lead in other
            ]
            expected = float(median(window_leads)) if window_leads else page_expected
            worst = max(leads, key=lambda lead: abs(lead - expected))
            signed = worst - expected
            if abs(signed) < LINE_SPACING_THRESHOLD_PT:
                continue
            matching = sum(
                1 for lead in all_leadings if abs(lead - expected) < LINE_SPACING_THRESHOLD_PT
            )
            anomalies.append(
                _anomaly(
                    "Spacing",
                    block,
                    detected=_pt(worst),
                    expected=_pt(expected),
                    baseline=(
                        f"{_pct(matching, len(all_leadings))} of line gaps on this page "
                        f"are ≈{_pt(expected)}"
                    ),
                    deviation=_signed_pt(signed),
                    confidence=_delta_confidence(
                        abs(signed), LINE_SPACING_THRESHOLD_PT, matching / len(all_leadings)
                    ),
                    evidence={
                        "kind": "line-spacing",
                        "detected_leading_pt": round(worst, 2),
                        "expected_leading_pt": round(expected, 2),
                        "leading_delta_pt": round(signed, 2),
                        "block_median_leading_pt": round(float(median(leads)), 2),
                        "comparable_count": len(all_leadings),
                        "matching_count": matching,
                    },
                    location=f"Line spacing in body paragraph near y={round(block.y0)}",
                )
            )

    if not assessed_any:
        return _cannot("Not enough intra-paragraph line gaps to assess line spacing")
    return anomalies, _assessed()


def _check_paragraph_spacing(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None = None,
    *,
    _scoped: bool = False,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    if not _scoped:
        sets = _sets_from_groups(blocks, groups)
        if len(sets) > 1:
            return _merge_checked(
                _check_paragraph_spacing(group, _scoped=True) for group in sets
            )
    body = _body_blocks(blocks)
    if not _has_spacing_rhythm(body):
        return _cannot("Table rows and card fields have no paragraph rhythm to compare")
    if len(body) < 3:
        return _cannot(
            "Not enough consecutive body paragraphs to assess paragraph spacing "
            f"({len(body)} found, need at least 3)"
        )

    anomalies: list[LayoutAnomaly] = []
    assessed_any = False
    for page_blocks in _by_page(body).values():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        if len(_page_columns(page_blocks)) >= 2 or len(page_blocks) < 3:
            continue
        gaps = [
            (prev, curr, curr.y0 - prev.y1)
            for prev, curr in zip(page_blocks, page_blocks[1:])
            if curr.y0 - prev.y1 >= 0
        ]
        if len(gaps) < 2:
            continue
        assessed_any = True
        typical = float(median(gap for _, _, gap in gaps))
        section_cut = max(typical * 5, 40.0)
        comparable = [(prev, curr, gap) for prev, curr, gap in gaps if gap <= section_cut]
        if len(comparable) < 2:
            continue
        expected_page = float(median(gap for _, _, gap in comparable))
        pending: list[tuple[int, float, LayoutAnomaly]] = []
        for index, (prev, curr, gap) in enumerate(comparable):
            window = [
                g
                for j, (_, _, g) in enumerate(comparable)
                if j != index and abs(j - index) <= _LOCAL_WINDOW
            ]
            expected = float(median(window)) if window else expected_page
            signed = gap - expected
            if abs(signed) < PARA_SPACING_THRESHOLD_PT:
                continue
            # A one-line label, date, or heading is the form, not a moved paragraph.
            if len(curr.lines) < 2 and curr.char_count < 40:
                continue
            matching = sum(
                1 for _, _, g in comparable if abs(g - expected) < PARA_SPACING_THRESHOLD_PT
            )
            space = "before" if signed > 0 else "after"
            pending.append(
                (
                    index,
                    signed,
                    _anomaly(
                        "Spacing",
                        curr,
                        detected=_pt(gap),
                        expected=_pt(expected),
                        baseline=(
                            f"{_pct(matching, len(comparable))} of paragraph gaps on this page "
                            f"are ≈{_pt(expected)}"
                        ),
                        deviation=_signed_pt(signed),
                        confidence=_delta_confidence(
                            abs(signed), PARA_SPACING_THRESHOLD_PT, matching / len(comparable)
                        ),
                        evidence={
                            "kind": "paragraph-spacing",
                            "space": space,
                            "gap_before_pt": round(gap, 2),
                            "gap_after_prev_pt": round(gap, 2),
                            "expected_gap_pt": round(expected, 2),
                            "gap_delta_pt": round(signed, 2),
                            "comparable_count": len(comparable),
                            "matching_count": matching,
                            "previous_text": _snippet(prev.text, 40),
                        },
                        location=(
                            f"Paragraph spacing near y={round(curr.y0)} "
                            f"(space {space} '{_snippet(curr.text, 40)}')"
                        ),
                    ),
                )
            )
        # Block k moving by d grows the gap before it and shrinks the
        # gap after it by the same d. Those two outliers are one move.
        # Keep the block that sits between them.
        signed_by_index = {index: signed for index, signed, _ in pending}
        echoed = set()
        for index, signed, _ in pending:
            other = signed_by_index.get(index + 1)
            if other is None or signed * other >= 0:
                continue
            tolerance = max(
                PARA_SPACING_THRESHOLD_PT * 0.5, 0.35 * max(abs(signed), abs(other))
            )
            if abs(abs(signed) - abs(other)) <= tolerance:
                echoed.add(index + 1)
        anomalies.extend(item for index, _, item in pending if index not in echoed)

    if not assessed_any:
        return _cannot(
            "Not enough consecutive body paragraphs on any page to assess paragraph spacing"
        )
    return anomalies, _assessed()


def _merge_spacing_assessment(
    line_check: CheckAssessment, para_check: CheckAssessment
) -> CheckAssessment:
    if not line_check.unable_to_assess or not para_check.unable_to_assess:
        return _assessed()
    reason = line_check.unable_to_assess_reason or para_check.unable_to_assess_reason
    return CheckAssessment(unable_to_assess=True, unable_to_assess_reason=reason)
