"""Structural roles, alignment classes, comparable groups, and content columns used as baselines."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable, Literal

from .common import (
    _ADDRESS_HINT_RE, ALIGN_EDGE_TOLERANCE_PT, Alignment, _assessed, BBox, _Block,
    _bucket_pt, _by_page, _cannot, CENTER_SYMMETRY_TOLERANCE_PT, CheckAssessment,
    _CLAUSE_HEADING_RE, _CLAUSE_STRETCH_MIN_WIDTH_PT, _clip, _COARSE_ROLES, _DATE_LINE_RE,
    _dominant, INDENT_THRESHOLD_PT, JUSTIFY_FILL_RATIO, _LABEL_VALUE_RE, LayoutAnomaly,
    LayoutElement, _Line, _LIST_MARKER_RE, MIN_BLOCK_CHARS, _NO_RHYTHM_ROLES,
    _NUMBERED_ITEM_RE, _rounded_bbox, SHORT_WIDTH_RATIO, StructuralRole,
)
from .extraction import (
    _is_clause_heading_text, _is_heading_block, _position_band,
)


def _assign_alignments(blocks: list[_Block]) -> None:
    for page_blocks in _by_page(blocks).values():
        body = [b for b in page_blocks if b.role == "body"]
        column = _content_column(body) or _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        for block in page_blocks:
            block.alignment = _classify_block_alignment(block, content_left, content_right)


def _page_region(block: _Block) -> Literal["header", "body", "footer"]:
    """Vertical band of the page. A heading in the body band is not chrome."""
    return _position_band(block.bbox, block.page_height)


def _is_list_marker_text(text: str) -> bool:
    text = " ".join(text.split())
    if not text or _is_clause_heading_text(text):
        return False
    if _LIST_MARKER_RE.match(text):
        return True
    if not _NUMBERED_ITEM_RE.match(text):
        return False
    # "1. The company is registered…" is a numbered paragraph, not a list row.
    return len(text) <= 80


def _looks_like_heading(block: _Block) -> bool:
    return _is_heading_block(block)


def _looks_like_list_item(block: _Block) -> bool:
    first = block.lines[0].text if block.lines else block.text
    return _is_list_marker_text(first)


def _looks_like_label_value(block: _Block) -> bool:
    text = " ".join(block.text.split())
    if not text or len(text) > 160:
        return False
    if len(block.lines) > 3:
        return False
    return bool(_LABEL_VALUE_RE.match(text))


def _looks_like_address(block: _Block) -> bool:
    text = " ".join(block.text.split())
    if not text or len(text) > 120 or len(text.split()) > 16:
        return False
    if text.endswith(".") and len(text.split()) > 8:
        return False
    return bool(_ADDRESS_HINT_RE.search(text))


def _looks_like_date(block: _Block) -> bool:
    text = " ".join(block.text.split())
    return bool(_DATE_LINE_RE.match(text))


def classify_structural_role(block: _Block) -> StructuralRole:
    """Role used to choose a baseline. Runs before anomaly checks.

    Header, footer, table, signature, heading, and cover are already set
    during extraction. Body copy is refined into list, label, address, or
    date when the text says so. Everything else stays body.
    """
    if block.role in _COARSE_ROLES:
        return block.role
    if _looks_like_heading(block):
        return "heading"
    if _looks_like_list_item(block):
        return "list_item"
    if _looks_like_label_value(block):
        return "label_value"
    if _looks_like_address(block):
        return "address"
    if _looks_like_date(block):
        return "date"
    return "body"


def _assign_structural_roles(blocks: list[_Block]) -> None:
    for block in blocks:
        block.role = classify_structural_role(block)


@dataclass
class StructuralGroup:
    """Blocks that share a role and page region, measured against one baseline.

    `section` is the clause for body, list, label, address, and date blocks.
    A heading starts a new section, so a paragraph is compared with the
    paragraphs under that heading, not with every paragraph in the PDF.
    Headings, signatures, and running chrome stay in one group (`section`
    is None) because their peers are the other headings or signatures.

    `alignment` is the alignment most members use. A single block with a
    different alignment stays in the group so the alignment check can see
    it. Two real populations — many left-aligned paragraphs and many
    justified ones — become two groups, each with its own baseline.
    """

    role: str
    page_region: str
    alignment: str | None
    members: list[_Block]
    section: int | None = None


# Body-like roles are compared inside the clause they sit in. A heading's
# peers are the other headings, so headings are not split by section.
_SECTION_ROLES = {"body", "list_item", "label_value", "address", "date"}
_PATTERN_MIN_RUN = 3
# Neighbouring blocks further apart than this are not one repeated pattern.
_PATTERN_GAP_PT = 72.0


def _section_ids(blocks: list[_Block]) -> dict[int, int]:
    """Clause index for each block. A heading opens the next clause.

    Text before the first heading shares a section. Headers and footers do
    not open one.
    """
    ordered = sorted(blocks, key=lambda block: (block.page, block.y0, block.x0))
    section = 0
    opened = False
    ids: dict[int, int] = {}
    for block in ordered:
        if block.role == "heading" and opened:
            section += 1
        ids[id(block)] = section
        if block.role not in {"header", "footer"}:
            opened = True
    return ids


def _stacked_neighbor(prev: _Block, curr: _Block) -> bool:
    """True when curr sits under prev, not in another column or section."""
    if curr.page != prev.page:
        return False
    overlap = min(prev.y1, curr.y1) - max(prev.y0, curr.y0)
    if overlap > 2 and abs(curr.x0 - prev.x0) >= INDENT_THRESHOLD_PT:
        return False
    gap = curr.y0 - prev.y1
    return -4 <= gap <= _PATTERN_GAP_PT


@dataclass
class StructuralPattern:
    """Three or more neighbouring blocks that share a role and a left edge.

    That repeat is how the document is built (a list, a run of headings).
    A member of the pattern is not an indentation anomaly.
    """

    role: str
    indentation: float
    members: list[_Block]


def repeated_structural_patterns(blocks: list[_Block]) -> list[StructuralPattern]:
    """Runs of neighbouring blocks with one role and one indent."""
    ordered = sorted(blocks, key=lambda block: (block.page, block.y0, block.x0))
    patterns: list[StructuralPattern] = []
    run: list[_Block] = []

    def _flush(items: list[_Block]) -> None:
        if len(items) < _PATTERN_MIN_RUN:
            return
        indent = float(median(block.x0 for block in items))
        members = [block for block in items if abs(block.x0 - indent) < INDENT_THRESHOLD_PT]
        if len(members) < _PATTERN_MIN_RUN:
            return
        patterns.append(
            StructuralPattern(role=members[0].role, indentation=indent, members=members)
        )

    for block in ordered:
        if not run:
            run = [block]
            continue
        prev = run[-1]
        if (
            block.role == prev.role
            and _stacked_neighbor(prev, block)
            and abs(block.x0 - run[0].x0) < INDENT_THRESHOLD_PT
        ):
            run.append(block)
        else:
            _flush(run)
            run = [block]
    _flush(run)
    return patterns


def _matches_structural_pattern(block: _Block, patterns: list[StructuralPattern]) -> bool:
    return any(block in pattern.members for pattern in patterns)


def _alignment_cohorts(members: list[_Block]) -> list[list[_Block]]:
    """Split a role when two alignments each have a real population.

    A lone centred paragraph stays with the majority so it can be flagged.
    One alignment, or none yet, is a single cohort.
    """
    by_align: dict[str | None, list[_Block]] = {}
    for member in members:
        by_align.setdefault(member.alignment, []).append(member)
    strong = [list(items) for align, items in by_align.items() if align and len(items) >= 2]
    if len(strong) < 2:
        return [members]
    strong.sort(key=len, reverse=True)
    outliers = [
        member
        for align, items in by_align.items()
        if not align or len(items) < 2
        for member in items
    ]
    strong[0].extend(outliers)
    return strong


def build_comparable_groups(blocks: list[_Block]) -> list[StructuralGroup]:
    """Group blocks that can share a baseline.

    Body, list, label, address, and date blocks are grouped per clause.
    A paragraph under clause 3 is not measured against clause 1.
    """
    sections = _section_ids(blocks)
    by_key: dict[tuple[str, str, int | None], list[_Block]] = {}
    for block in blocks:
        section = sections.get(id(block)) if block.role in _SECTION_ROLES else None
        by_key.setdefault((block.role, _page_region(block), section), []).append(block)
    groups: list[StructuralGroup] = []
    for (role, region, section), members in by_key.items():
        for cohort in _alignment_cohorts(members):
            alignments = [member.alignment for member in cohort if member.alignment]
            groups.append(
                StructuralGroup(
                    role=role,
                    page_region=region,
                    alignment=_dominant(alignments) if alignments else None,
                    members=cohort,
                    section=section,
                )
            )
    return groups


def _collapse_sections(groups: list[StructuralGroup]) -> list[StructuralGroup]:
    """One baseline per role and page region, across clauses.

    A right edge is a property of the role: body paragraphs share an edge,
    and a table, address, or signature keeps its own. Clause groups stay
    separate for indent and spacing, where the neighbours in the clause
    are the baseline.
    """
    buckets: dict[tuple[str, str], list[_Block]] = {}
    for group in groups:
        buckets.setdefault((group.role, group.page_region), []).extend(group.members)
    collapsed: list[StructuralGroup] = []
    for (role, region), members in buckets.items():
        alignments = [member.alignment for member in members if member.alignment]
        collapsed.append(
            StructuralGroup(
                role=role,
                page_region=region,
                alignment=_dominant(alignments) if alignments else None,
                members=members,
                section=None,
            )
        )
    return collapsed


def _sets_from_groups(
    blocks: list[_Block],
    groups: list[StructuralGroup] | None,
    *,
    collapse_sections: bool = False,
) -> list[list[_Block]]:
    """Member lists for one check. Uses the groups the caller already built."""
    if groups is None:
        groups = build_comparable_groups(blocks)
    if collapse_sections:
        groups = _collapse_sections(groups)
    if len(groups) <= 1:
        return [blocks]
    return [group.members for group in groups]


def _merge_checked(
    results: Iterable[tuple[list[LayoutAnomaly], CheckAssessment]],
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    anomalies: list[LayoutAnomaly] = []
    assessed = False
    reason: str | None = None
    for found, check in results:
        anomalies.extend(found)
        if not check.unable_to_assess:
            assessed = True
        elif reason is None:
            reason = check.unable_to_assess_reason
    if assessed:
        return anomalies, _assessed()
    return _cannot(reason or "Not enough comparable lines")


def _compile_elements(blocks: list[_Block]) -> list[LayoutElement]:
    elements: list[LayoutElement] = []
    for block in blocks:
        elements.append(
            LayoutElement(
                page=block.page + 1,
                bbox=_rounded_bbox(block.bbox),
                left=round(block.x0, 2),
                top=round(block.y0, 2),
                right=round(block.x1, 2),
                bottom=round(block.y1, 2),
                alignment=block.alignment,
                paragraph_type=block.role,
                font=block.font,
                size=round(block.size, 2) if block.size is not None else None,
                text=_clip(block.text),
            )
        )
    return elements


def _body_blocks(blocks: list[_Block]) -> list[_Block]:
    """Lines that can be compared with each other.

    Body paragraphs are the usual set. A group that is entirely one other
    role (list items, labels, headings) is compared with itself. A one-page
    form often has only one body line and the rest tagged as headings, so
    those headings join the comparison when body copy alone is not enough.
    Running headers and footers stay out of a mixed set; a header is only
    compared with other headers.
    """
    body = [b for b in blocks if b.role == "body" and b.char_count >= MIN_BLOCK_CHARS]
    if len(body) >= 2:
        return body
    roles = {b.role for b in blocks}
    if len(roles) == 1 and "body" not in roles:
        return [b for b in blocks if b.char_count >= MIN_BLOCK_CHARS]
    others = [
        b
        for b in blocks
        if b.role in {"heading", "cover", "table"} and b.char_count >= MIN_BLOCK_CHARS
    ]
    if len(body) + len(others) >= 2:
        return body + others
    return body


def _has_spacing_rhythm(blocks: list[_Block]) -> bool:
    """False when every comparable block is a table row or a card field."""
    roles = {block.role for block in blocks}
    return bool(roles) and not roles <= _NO_RHYTHM_ROLES


def _peer_phrase(blocks: list[_Block]) -> str:
    role = blocks[0].role if blocks else "body"
    return {
        "body": "comparable body paragraphs",
        "list_item": "comparable list items",
        "label_value": "comparable labels",
        "address": "comparable address lines",
        "date": "comparable dates",
        "heading": "comparable headings",
        "cover": "comparable cover titles",
        "table": "comparable table cells",
        "signature": "comparable signature lines",
        "header": "comparable headers",
        "footer": "comparable footers",
    }.get(role, "comparable lines")


# A left edge shared by this many blocks is a column (dates, descriptions,
# amounts), not one shoved paragraph. Two full-width clauses still count
# as an indent run.
_COLUMN_MIN_MEMBERS = 3
_SHORT_COLUMN_MIN_MEMBERS = 2


def _cluster_by_left(blocks: list[_Block]) -> list[list[_Block]]:
    clusters: list[list[_Block]] = []
    for block in sorted(blocks, key=lambda b: b.x0):
        if clusters and block.x0 - clusters[-1][0].x0 < INDENT_THRESHOLD_PT:
            clusters[-1].append(block)
        else:
            clusters.append([block])
    return clusters


def _page_columns(blocks: list[_Block]) -> list[float]:
    """Repeated left edges on a multi-column page.

    Empty when the page has a single body column, so a one-off indent
    among uniform paragraphs still uses the neighbour comparison.
    A pair of short amount lines joins the columns. A pair of full-width
    clauses does not — that is the consecutive-indent signal.
    """
    clusters = _cluster_by_left(blocks)
    wide = [cluster for cluster in clusters if len(cluster) >= _COLUMN_MIN_MEMBERS]
    if not wide:
        return []
    columns = [float(median(b.x0 for b in cluster)) for cluster in wide]
    for cluster in clusters:
        if len(cluster) < _SHORT_COLUMN_MIN_MEMBERS or len(cluster) >= _COLUMN_MIN_MEMBERS:
            continue
        if median(b.width for b in cluster) >= _CLAUSE_STRETCH_MIN_WIDTH_PT:
            continue
        columns.append(float(median(b.x0 for b in cluster)))
    return columns


def _column_of(block: _Block, columns: list[float]) -> float | None:
    if not columns:
        return None
    nearest = min(columns, key=lambda column: abs(block.x0 - column))
    if abs(block.x0 - nearest) < INDENT_THRESHOLD_PT:
        return nearest
    return None


def _column_groups(blocks: list[_Block], columns: list[float]) -> list[list[_Block]]:
    """Blocks on each column. A line on no column is judged against the nearest one."""
    grouped: dict[float, list[_Block]] = {column: [] for column in columns}
    for block in blocks:
        home = _column_of(block, columns)
        if home is None:
            home = min(columns, key=lambda column: abs(block.x0 - column))
        grouped[home].append(block)
    return [group for group in grouped.values() if group]


def _content_column(blocks: list[_Block]) -> tuple[float, float] | None:
    """Dominant left/right edges of full-ish body copy on this set of blocks."""
    if not blocks:
        return None
    lefts = [_bucket_pt(b.x0) for b in blocks]
    content_left = _dominant(lefts)
    if content_left is None:
        content_left = median(b.x0 for b in blocks)

    widths = sorted(b.width for b in blocks)
    wide_cut = widths[len(widths) // 2]
    wide = [b for b in blocks if b.width >= wide_cut] or list(blocks)
    content_right = median(b.x1 for b in wide)
    if content_right <= content_left:
        return None
    return (float(content_left), float(content_right))


def _width_band(block: _Block, content_left: float, content_right: float) -> Literal["full", "short"]:
    column_width = content_right - content_left
    if column_width <= 0:
        return "short"
    return "full" if block.width >= SHORT_WIDTH_RATIO * column_width else "short"


def _reaches_column_right(block: _Block, content_right: float) -> bool:
    """True when the block still meets the column edge — a reflowed wrap,
    not a short title/label that merely starts further in."""
    return content_right - block.x1 < INDENT_THRESHOLD_PT


def _classify_block_alignment(
    block: _Block, content_left: float, content_right: float
) -> Alignment | None:
    column_width = content_right - content_left
    if column_width <= 0:
        return None

    lines = _alignment_lines(block, content_left, content_right)
    if not lines:
        return None

    if len(lines) == 1:
        return _classify_line_alignment(lines[0].bbox, content_left, content_right)

    lefts = [line.bbox[0] for line in lines]
    rights = [line.bbox[2] for line in lines]
    left_spread = max(lefts) - min(lefts)
    right_spread = max(rights) - min(rights)
    body_lines = lines[:-1]
    union_width = max(rights) - min(lefts)

    left_flush = abs(min(lefts) - content_left) <= ALIGN_EDGE_TOLERANCE_PT
    body_right_flush = all(
        abs(line.bbox[2] - content_right) <= ALIGN_EDGE_TOLERANCE_PT for line in body_lines
    )
    fill_ratio = union_width / column_width

    # Justified is left-AND-right flush to the page's content column —
    # internally-aligned left edges that sit inset from the column are an
    # indented left-aligned paragraph, not justification.
    if (
        left_flush
        and left_spread <= ALIGN_EDGE_TOLERANCE_PT
        and body_right_flush
        and fill_ratio >= JUSTIFY_FILL_RATIO
    ):
        return "justified"
    if left_spread <= ALIGN_EDGE_TOLERANCE_PT:
        return "left-aligned"
    if right_spread <= ALIGN_EDGE_TOLERANCE_PT and all(
        abs(line.bbox[2] - content_right) <= ALIGN_EDGE_TOLERANCE_PT for line in lines
    ):
        return "right-aligned"

    leftovers = [
        (line.bbox[0] - content_left, content_right - line.bbox[2]) for line in lines
    ]
    if leftovers and all(
        abs(left - right) <= CENTER_SYMMETRY_TOLERANCE_PT
        and left > ALIGN_EDGE_TOLERANCE_PT
        and right > ALIGN_EDGE_TOLERANCE_PT
        for left, right in leftovers
    ):
        return "centred"

    # Fall back to the block as a whole (union bbox) when per-line signals
    # disagree — a wrapped left-aligned paragraph with a ragged right edge.
    return _classify_line_alignment(block.bbox, content_left, content_right)


def _classify_line_alignment(
    bbox: BBox, content_left: float, content_right: float
) -> Alignment | None:
    x0, _, x1, _ = bbox
    column_width = content_right - content_left
    if column_width <= 0:
        return None
    leftover_left = x0 - content_left
    leftover_right = content_right - x1
    line_width = x1 - x0
    fill_ratio = line_width / column_width

    if (
        fill_ratio >= JUSTIFY_FILL_RATIO
        and leftover_left <= ALIGN_EDGE_TOLERANCE_PT
        and leftover_right <= ALIGN_EDGE_TOLERANCE_PT
    ):
        return "justified"
    if leftover_left <= ALIGN_EDGE_TOLERANCE_PT and leftover_right > leftover_left + ALIGN_EDGE_TOLERANCE_PT:
        return "left-aligned"
    if leftover_right <= ALIGN_EDGE_TOLERANCE_PT and leftover_left > leftover_right + ALIGN_EDGE_TOLERANCE_PT:
        return "right-aligned"
    if (
        abs(leftover_left - leftover_right) <= CENTER_SYMMETRY_TOLERANCE_PT
        and leftover_left > ALIGN_EDGE_TOLERANCE_PT
        and leftover_right > ALIGN_EDGE_TOLERANCE_PT
    ):
        return "centred"
    if leftover_left <= leftover_right:
        return "left-aligned"
    return "right-aligned"


def _is_short_or_heading_line(line: _Line, column_width: float) -> bool:
    if column_width > 0 and (line.bbox[2] - line.bbox[0]) / column_width < SHORT_WIDTH_RATIO:
        return True
    return bool(_CLAUSE_HEADING_RE.match(line.text.strip()))


def _alignment_lines(
    block: _Block, content_left: float, content_right: float
) -> list[_Line]:
    """Drop a short clause heading sitting on top of the paragraph it labels."""
    lines = list(block.lines)
    column_width = content_right - content_left
    while len(lines) >= 2 and _is_short_or_heading_line(lines[0], column_width):
        lines = lines[1:]
    return lines
