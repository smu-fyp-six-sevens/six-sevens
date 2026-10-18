"""Layout anomaly detection: indentation, alignment, margin, spacing, and text overlay.

Each check lives in its own module. Every name is re-exported here, so
`app.fraud_detection.layout_anomalies.<name>` works as it did when this was one file.
"""

from __future__ import annotations

from . import text_overlay
from .profiles import confident_type, profile_for
from .reference import fitting_references
from .common import (  # noqa: F401
    _abs_pt_phrase, _ADDRESS_HINT_RE, ALIGN_EDGE_TOLERANCE_PT, Alignment, _ALIGNMENT_LABEL,
    _anomaly, AnomalyType, _as_bbox, _assessed, BBox, _bbox_area, _Block, _block_for_line,
    _bucket_pt, _by_page, _cannot, _CENTER_MID_BUCKET_PT, CENTER_SHIFT_THRESHOLD_PT,
    CENTER_SYMMETRY_TOLERANCE_PT, CheckAssessment, CHROME_POS_THRESHOLD_PT,
    _CLAUSE_HEADING_RE, _CLAUSE_STRETCH_MIN_WIDTH_PT, _clip, _COARSE_ROLES,
    _COLLISION_MIN_OVERLAP_FRACTION, _DATE_LINE_RE, _delta_confidence, _DEVIATION_KEYS,
    _dominant, _edge_anomaly, _empty_result, _EVERYWHERE_MIN_REGIONS,
    _EVERYWHERE_PAGE_FRACTION, _EVERYWHERE_REGION_FRACTION, _evidence_kind, FOOTER_RATIO,
    _format_summary, _FULL_LINE_WIDTH_RATIO, _has_strong_majority, HEADER_RATIO,
    _INDENT_BUCKET_PT, INDENT_THRESHOLD_PT, _intersection_area, JUSTIFY_FILL_RATIO,
    _label_alignment, _LABEL_VALUE_RE, LayoutAnomaly, LayoutAnomalyResult, LayoutElement,
    _letters_in, _Line, _line_baseline, LINE_SPACING_THRESHOLD_PT, _line_width,
    _lines_by_page, _LIST_MARKER_RE, _LIST_RUN_GAP_PT, _LOCAL_WINDOW, _location_label,
    logger, _majority_confidence, MARGIN_THRESHOLD_PT, _MAX_FINDINGS_PER_DOCUMENT,
    MIN_BLOCK_CHARS, _NO_RHYTHM_ROLES, _NUMBERED_ITEM_RE, _opening, PageMargins,
    PARA_SPACING_THRESHOLD_PT, ParagraphType, _pct, _pt, _readable_check, _ROLE_LABEL,
    ROW_ALIGN_THRESHOLD_PT, _SHORT_LINE_PAGE_RATIO, SHORT_WIDTH_RATIO, _signal_kind,
    _signal_record, _SIGNAL_STRENGTH_FLOOR, _SIGNATURE_RE, _SIGNATURE_ZONE_RATIO,
    _signed_pt, _snippet, StructuralRole, _summary_sentence, _T, _TEXT_SNIPPET_CHARS,
    _TITLE_PAGE_SHORT_FRACTION, _TITLE_PAGE_SHORT_RATIO, _union_bbox,
    VERTICAL_SHIFT_THRESHOLD_PT, _x_overlap, REPORT_MIN_CONFIDENCE,
)
from .extraction import (  # noqa: F401
    _apply_heading_and_cover_roles, _apply_signature_roles, _apply_table_roles,
    _block_from_lines, _combine_blocks, _extract_blocks, _horizontally_related,
    _is_clause_heading_text, _is_heading_block, _is_title_page, _line_text_and_style,
    _looks_like_signature, _mark_repeating_chrome, _MAX_INTRA_PARAGRAPH_GAP_PT,
    _merge_adjacent_blocks, _needs_ocr, _ocr_blocks, _open_document, _page, _page_margins,
    _pages, _position_band, _Rule, _split_leading_headings, _table_rects, _vertical_rules,
)
from .structure import (  # noqa: F401
    _alignment_cohorts, _alignment_lines, _assign_alignments, _assign_structural_roles,
    _body_blocks, build_comparable_groups, _classify_block_alignment,
    _classify_line_alignment, classify_structural_role, _cluster_by_left,
    _collapse_sections, _column_groups, _COLUMN_MIN_MEMBERS, _column_of, _compile_elements,
    _content_column, _has_spacing_rhythm, _is_list_marker_text, _is_short_or_heading_line,
    _looks_like_address, _looks_like_date, _looks_like_heading, _looks_like_label_value,
    _looks_like_list_item, _matches_structural_pattern, _merge_checked, _page_columns,
    _page_region, _PATTERN_GAP_PT, _PATTERN_MIN_RUN, _peer_phrase, _reaches_column_right,
    repeated_structural_patterns, _section_ids, _SECTION_ROLES, _sets_from_groups,
    _SHORT_COLUMN_MIN_MEMBERS, _stacked_neighbor, StructuralGroup, StructuralPattern,
    _width_band,
)
from .indentation import (  # noqa: F401
    _check_indentation, _check_protruding_right, _check_right_edge, _COLUMN_SHIFT_MAX_PT,
    _consistent_right_edge, _right_edge_census, _RIGHT_EDGE_ROLES, _SHIFT_MAX_HORIZONTAL_PT,
    _SHIFT_MAX_VERTICAL_REACH_PT, _SHIFT_NEIGHBOR_WINDOW_PT, _short_line_indents,
    _STACK_OVERLAP_PT,
)
from .anchors import (  # noqa: F401
    _ANCHOR_COLUMN_REACH_PT, _ANCHOR_MAX_SHIFT_PT, _ANCHOR_MIN_SHIFT_PT,
    _ANCHOR_SIZE_TOLERANCE_PT, _ANCHOR_TOLERANCE_PT, _ANCHOR_WINDOW_PT, _check_pair_offsets,
    _check_unanchored_lines, _check_vertical_unanchored_lines, _fits_drawn_cell,
    _left_column, _mid_line, _MIXED_SIZE_COLUMN_REACH_PT, _PITCH_MIN_SHIFT_PT,
    _repeated_line_keys, _row_baselines, _ROW_MAX_SHIFT_PT, _ROW_MIN_SHIFT_PT,
    _row_slot_left_empty, _shares_an_edge, _stack_neighbours, _stacked_pairs,
    _vertical_anchor_delta, _check_moved_furniture,
)
from .line_shifts import (  # noqa: F401
    _abutting_pair, _check_column_line_shifts, _check_cross_block_shifts,
    _check_shifted_lines, _check_split_lines, _collision_delta, _horizontal_line_shifts,
    _is_sentence_line, _line_indent_inside_block, _line_left_columns,
    _line_on_nearby_column, _line_past_block_edge, _lopsided_gap_delta,
    _nearby_column_members, _nearest_column, _nearest_stacked_line, _page_line_entries,
    _rigid_left_slide, _same_edge_lines, _same_edge_pocket,
    _shares_right_edge_with_full_line, _similar_width_sentence, _stacked, _stacked_gaps,
    _still_ends_with_column, _vertical_line_shifts, _vertical_overlap,
)
from .spacing import (  # noqa: F401
    _check_line_spacing, _check_paragraph_spacing, _line_leadings,
    _merge_spacing_assessment,
)
from .alignment import (  # noqa: F401
    _alignment_anomaly, _alignment_mismatch_unreliable, _baseline_shift, _both_edges_off,
    _check_alignment, _check_centre_shift, _check_displaced_row_members,
    _check_row_alignment, _compared_baseline_pair, _heading_row_groups, _matches_row_rhythm,
    _mid_x, _mid_y, _mid_y_gap, _nearest_row_pairs, _row_offset_rhythms, _same_visual_row,
    _side_by_side, _vertical_misalignment,
)
from .margins import (  # noqa: F401
    _check_chrome_position, _check_margins, _chrome_anchors, _expected_margins,
    _flag_chrome_shift, _margin_anomalies_for, _median_margins, _pages_with_full_line,
    _title_page_numbers,
)
from .signals import (  # noqa: F401
    _anchored_lines, _Anchors, _bbox_contains, _block_matches_anomaly, _boxes_same_region,
    _cap_signals, _coalesce_signals, _consistent_list_openings, _corroborated,
    _decide_from_signals, _explained_by_anchors, _is_clause_stretch_finding, _is_mergeable,
    _is_paragraph_finding, _keeps_a_row, _layout_signal, LayoutSignal,
    _mark_local_consecutive, _MERGEABLE_KINDS, _NEEDS_CORROBORATION, _numeric_deviation,
    _POSITION_KINDS, _PRIMARY_RANK, _primary_rank, _region_groups, _same_finding_region,
    _SECTION_BREAK_GAP_PT, _side_explains, _signal_family, _text_matches_opening,
    _types_firing_everywhere, _merge_neighbours,
)


def check_layout_anomalies(
    document_id: str,
    file_bytes: bytes,
    document_type: str | None = None,
    classification_confidence: float | None = None,
) -> LayoutAnomalyResult:
    """
    Inspects a PDF's own text positioning for internal inconsistency.

    Classification runs before any measurement:

    1. Assign each block a structural role (heading, body, list item, …).
    2. Group blocks that share a role, page region, and — for body copy —
       the same clause. The baseline is the pattern around the block, not
       every paragraph in the PDF.
    3. Measure indent, alignment, spacing, and the role's right edge on
       those groups. The groups are passed in; a check does not build a
       second set. Margins, chrome, and overlapping boxes stay page-level.
    4. Treat each hit as a signal (type, strength, deviation).
    5. Drop a signal the structure explains, a signal under the strength
       floor, and a detector that fires on most of the document. Keep the
       strongest remaining regions. A list whose indent is consistent, and
       any run of three or more neighbours that share a role and an indent,
       is a structural pattern.
    6. Merge signals that land on the same region into one finding,
       including a line box inside its paragraph.

    When the classifier is confident in `document_type`, the curated reference
    layouts of that form (reference.py) that fit are used, one per version of
    the form: a field is reported as moved only if no fitting version places
    it there, and generic findings on lines any fitting version shows in place
    are dropped. That type's profile
    (profiles.py) then drops signals the type explains by design, before
    step 5.
    Without a type, or below the confidence threshold, every document gets
    the same generic checks.

    Text/geometry based, no LLM tokens. Synchronous/CPU-bound — callers
    should run this via asyncio.to_thread.
    """
    try:
        doc = _open_document(file_bytes)
    except Exception as exc:
        reason = f"Failed to open document: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _empty_result(document_id, reason)

    try:
        blocks = _extract_blocks(doc)
        if not blocks and _needs_ocr(doc, file_bytes):
            blocks = _ocr_blocks(doc)
        if not blocks:
            has_images = any(page.get_images() for page in _pages(doc))
            reason = (
                f"No extractable text layout found ({doc.page_count} page(s), "
                f"has embedded images: {has_images}) -- likely a scanned/"
                "image-only PDF with no text layer, or an encrypted/"
                "corrupted one."
            )
            logger.warning("Layout anomaly check: document %s: %s", document_id, reason)
            return _empty_result(document_id, reason)

        page_margins = _page_margins(blocks)
        _assign_structural_roles(blocks)
        _assign_alignments(blocks)
        text_overlay_anomalies = text_overlay._check_text_overlaps(blocks)
        groups = build_comparable_groups(blocks)
        logger.debug(
            "Document %s structural groups: %s",
            document_id,
            [
                (
                    f"{group.role}/{group.page_region}/"
                    f"s{group.section if group.section is not None else '-'}/"
                    f"{group.alignment or 'mixed'}={len(group.members)}"
                )
                for group in groups
            ],
        )
        indent_anomalies, indent_check = _check_indentation(blocks, groups)
        right_edge_anomalies, right_edge_check = _check_right_edge(blocks, groups)
        align_anomalies, align_check = _check_alignment(blocks, groups)
        row_anomalies = _check_row_alignment(blocks)
        displaced_row_anomalies = _check_displaced_row_members(blocks)
        centre_anomalies = _check_centre_shift(blocks)
        line_anomalies, line_check = _check_line_spacing(blocks, groups)
        para_anomalies, para_check = _check_paragraph_spacing(blocks, groups)
        margin_anomalies, margin_check = _check_margins(page_margins, blocks)
        protrude_anomalies = _check_protruding_right(blocks, groups)
        shifted_line_anomalies = _check_shifted_lines(blocks, groups)
        rules = _vertical_rules(doc)
        unanchored_anomalies = (
            _check_unanchored_lines(blocks, rules)
            + _check_vertical_unanchored_lines(blocks)
            + _check_moved_furniture(blocks)
            + _check_pair_offsets(blocks, rules)
        )
        chrome_anomalies, chrome_check = _check_chrome_position(blocks)
        spacing_check = _merge_spacing_assessment(line_check, para_check)
        if not indent_check.unable_to_assess or not right_edge_check.unable_to_assess:
            indent_check = _assessed()
        if not margin_check.unable_to_assess or not chrome_check.unable_to_assess:
            margin_check = _assessed()

        anomalies = (
            text_overlay_anomalies
            + indent_anomalies
            + right_edge_anomalies
            + align_anomalies
            + row_anomalies
            + displaced_row_anomalies
            + centre_anomalies
            + line_anomalies
            + para_anomalies
            + margin_anomalies
            + chrome_anomalies
            + protrude_anomalies
            + shifted_line_anomalies
            + unanchored_anomalies
        )
        known_type = confident_type(document_type, classification_confidence)
        references = fitting_references(blocks, known_type) if known_type else []
        if references:
            # A line another fitting version prints is that version's design, not an
            # edit, labels and values alike: one version's copies may not have had a
            # value as long as this one (a name with bin or s/o), another's did.
            best, others = references[0], references[1:]

            def by_design(anomaly: LayoutAnomaly) -> bool:
                return any(r.explains(anomaly) for r in others)

            # Text overlay is judged on its own.
            anomalies = [
                a for a in anomalies
                if a.anomaly_type == "Text Overlay" or not (best.explains(a) or by_design(a))
            ]
            anomalies += [f for f in best.findings if not by_design(f)]
        signals = [_layout_signal(item) for item in anomalies]
        profile = profile_for(document_type, classification_confidence)
        if profile is not None:
            signals = [s for s in signals if not profile.explains(s.anomaly)]
        anomalies = _decide_from_signals(signals, blocks, rules)
        anomalies = _coalesce_signals(anomalies)
        anomalies = [anomaly for anomaly in anomalies if _corroborated(anomaly, blocks)]
        anomalies = _merge_neighbours(anomalies)
        _mark_local_consecutive(anomalies)
        # Weak findings are mostly side effects and design quirks; the rest
        # are ranked so the strongest come first.
        anomalies = sorted(
            (a for a in anomalies if a.confidence >= REPORT_MIN_CONFIDENCE),
            key=lambda a: (-a.confidence, a.page, a.bbox[1]),
        )
        elements = _compile_elements(blocks)

        checks = (indent_check, align_check, margin_check, spacing_check)
        # A form compared with its reference layout was assessed even when it has too
        # few lines of its own to compare (an ID card), and so was one with a finding.
        unable = (
            all(check.unable_to_assess for check in checks)
            and not references
            and not anomalies
        )
        reason = None
        if unable:
            reason = (
                "Not enough comparable text to assess layout. "
                "A one-page document can still be checked from its own lines; "
                "margins need a second page."
            )
        return LayoutAnomalyResult(
            document_id=document_id,
            anomalies=anomalies,
            elements=elements,
            page_margins=page_margins,
            elements_analysed=len(elements),
            indentation=indent_check,
            alignment=align_check,
            margin=margin_check,
            spacing=spacing_check,
            unable_to_assess=unable,
            unable_to_assess_reason=reason,
        )
    except Exception as exc:
        logger.exception("Layout anomaly check failed for document %s", document_id)
        reason = f"Unexpected error during layout anomaly check: {type(exc).__name__}: {exc}"
        return _empty_result(document_id, reason)
    finally:
        doc.close()
