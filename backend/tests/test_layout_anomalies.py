"""Layout anomalies: indent/alignment/margin checks plus Unable to Assess."""

from __future__ import annotations

import pymupdf
import pytest

from app.fraud_detection import layout_anomalies as lr
from app.fraud_detection.layout_anomalies import (
    LayoutAnomaly,
    _Block,
    _Line,
    _apply_table_roles,
    _bucket_pt,
    _classify_block_alignment,
    _classify_line_alignment,
    _content_column,
    _dominant,
    _looks_like_signature,
    _page_margins,
    _position_band,
    check_layout_anomalies,
)


def _kinds(anomaly) -> set[str]:
    signals = anomaly.evidence.get("signals") or []
    if signals:
        return {s.get("kind") for s in signals if s.get("kind")}
    kind = anomaly.evidence.get("kind")
    return {kind} if kind else set()


def _signal(anomaly, kind: str) -> dict | None:
    for item in anomaly.evidence.get("signals") or [anomaly.evidence]:
        if item.get("kind") == kind or item.get("side") == kind:
            return item
    return None

BODY = (
    "The registered office of the Company shall be at such place in Singapore "
    "as the directors may from time to time determine, and the Company shall "
    "keep its registers at that office in accordance with the Act."
)


def _pdf_bytes(build) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def _textbox(page, rect, text, *, align=0, fontsize=11) -> None:
    page.insert_textbox(rect, text, fontsize=fontsize, fontname="helv", align=align)


def _consistent_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for _ in range(4):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _indented_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            left = 96 if i == 2 else 72  # ~8.5mm extra indent on the third block
            _textbox(page, pymupdf.Rect(left, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


_CENTRED_INSERTION = (
    "This clause has been digitally inserted after signing. "
    "It is centred on the page unlike the surrounding body copy."
)


def _mixed_alignment_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            if i == 2:
                _textbox(
                    page,
                    pymupdf.Rect(72, y, 540, y + 70),
                    _CENTRED_INSERTION,
                    align=1,
                )
            else:
                _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


_RIGHT_ALIGNED_INSERTION = (
    "This clause has been digitally inserted after signing. "
    "It is right-aligned unlike the surrounding body copy of this constitution."
)


def _right_aligned_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            if i == 2:
                _textbox(
                    page,
                    pymupdf.Rect(72, y, 540, y + 70),
                    _RIGHT_ALIGNED_INSERTION,
                    align=2,
                )
            else:
                _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _header_and_body_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        _textbox(
            page,
            pymupdf.Rect(72, 20, 540, 55),
            "GOLDEN HARVEST TRADING PTE LTD",
            align=1,
            fontsize=16,
        )
        y = 90
        for _ in range(3):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _signature_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for _ in range(3):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80
        _textbox(
            page,
            pymupdf.Rect(320, 680, 540, 720),
            "Signed by Jane Tan, Director",
            align=2,
        )

    return _pdf_bytes(build)


def _margin_mismatch_pdf() -> bytes:
    def build(doc):
        for page_index in range(3):
            page = doc.new_page(width=612, height=792)
            # Third page inset on every side relative to the first two.
            inset = 50 if page_index == 2 else 0
            left, top, right, bottom = 72 + inset, 90 + inset, 540 - inset, 700 - inset
            y = top
            for _ in range(3):
                _textbox(page, pymupdf.Rect(left, y, right, y + 70), BODY, align=0)
                y += 80

    return _pdf_bytes(build)


def _blank_pdf() -> bytes:
    def build(doc):
        doc.new_page(width=200, height=200)

    return _pdf_bytes(build)


def _image_only_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=200, height=200)
        logo = pymupdf.open()
        logo_page = logo.new_page(width=32, height=16)
        logo_page.draw_rect(logo_page.rect, fill=(0.8, 0.1, 0.1))
        pix = logo_page.get_pixmap()
        logo.close()
        page.insert_image(pymupdf.Rect(20, 20, 80, 50), pixmap=pix)

    return _pdf_bytes(build)


def _misaligned_row_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), "Chargeable Income", fontsize=11, fontname="helv")
        page.insert_text((480, 212), "316,800", fontsize=11, fontname="helv")
        y = 300
        for _ in range(3):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _aligned_row_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), "Chargeable Income", fontsize=11, fontname="helv")
        page.insert_text((480, 200), "316,800", fontsize=11, fontname="helv")
        y = 300
        for _ in range(3):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _short_right_edge_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            right = 500 if i == 2 else 540
            _textbox(page, pymupdf.Rect(72, y, right, y + 70), BODY, align=3)
            y += 80

    return _pdf_bytes(build)


def _justified_clauses_with_short_name_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        _textbox(
            page,
            pymupdf.Rect(72, 90, 540, 110),
            "The name of the Company is Falcon Aviation Support Pte Ltd.",
            align=0,
        )
        y = 130
        for _ in range(4):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=3)
            y += 80

    return _pdf_bytes(build)


def _shifted_chrome_pdf() -> bytes:
    def build(doc):
        for i in range(3):
            page = doc.new_page(width=612, height=792)
            hy = 48 if i == 2 else 18
            _textbox(
                page,
                pymupdf.Rect(72, hy, 540, hy + 16),
                "ACME PTE LTD — CONFIDENTIAL",
                align=1,
                fontsize=9,
            )
            y = 90
            for _ in range(3):
                _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
                y += 80
            fy = 734 if i == 2 else 762
            _textbox(
                page,
                pymupdf.Rect(72, fy, 540, fy + 14),
                f"Page {i + 1} of 3",
                align=1,
                fontsize=8,
            )

    return _pdf_bytes(build)


def _consistent_chrome_pdf() -> bytes:
    def build(doc):
        for i in range(3):
            page = doc.new_page(width=612, height=792)
            _textbox(
                page,
                pymupdf.Rect(72, 18, 540, 34),
                "ACME PTE LTD — CONFIDENTIAL",
                align=1,
                fontsize=9,
            )
            y = 90
            for _ in range(3):
                _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=0)
                y += 80
            _textbox(
                page,
                pymupdf.Rect(72, 762, 540, 776),
                f"Page {i + 1} of 3",
                align=1,
                fontsize=8,
            )

    return _pdf_bytes(build)


def _two_indented_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            left = 96 if i in (1, 2) else 72
            _textbox(page, pymupdf.Rect(left, y, 540, y + 70), BODY, align=0)
            y += 80

    return _pdf_bytes(build)


def _wide_paragraph_gap_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(5):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 50), BODY, align=0)
            y += 90 if i == 2 else 62

    return _pdf_bytes(build)


def _single_line_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=400, height=240)
        page.insert_text((48, 80), "Hello world", fontsize=12, fontname="helv")

    return _pdf_bytes(build)


def _line(bbox, **kwargs) -> _Line:
    values = dict(
        text="The registered office of the Company shall be in Singapore.",
        bbox=bbox,
        page=0,
        page_width=612.0,
        page_height=792.0,
    )
    values.update(kwargs)
    return _Line(**values)


def _block(lines: list[_Line], **kwargs) -> _Block:
    bbox = (
        min(line.bbox[0] for line in lines),
        min(line.bbox[1] for line in lines),
        max(line.bbox[2] for line in lines),
        max(line.bbox[3] for line in lines),
    )
    values = dict(
        lines=lines,
        bbox=bbox,
        page=lines[0].page,
        page_width=lines[0].page_width,
        page_height=lines[0].page_height,
        text=" ".join(line.text for line in lines),
        role="body",
    )
    values.update(kwargs)
    return _Block(**values)


def _ml_block(y0: float, leadings: list[float], *, x0: float = 72.0, x1: float = 540.0) -> _Block:
    y = y0
    lines = []
    for i in range(len(leadings) + 1):
        lines.append(
            _line(
                (x0, y, x1, y + 12),
                text="The registered office of the Company shall be in Singapore.",
            )
        )
        if i < len(leadings):
            y += leadings[i]
    return _block(lines)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_dominant_tie_and_empty() -> None:
    assert _dominant([]) is None
    assert _dominant(["a", "b"]) is None
    assert _dominant(["a", "a", "b"]) == "a"
    assert _bucket_pt(72.4) == 72.0
    assert _bucket_pt(72.6) == 73.0


def test_block_role_header_body_footer() -> None:
    assert _position_band((72, 10, 200, 24), 792.0) == "header"
    assert _position_band((72, 400, 200, 420), 792.0) == "body"
    assert _position_band((72, 760, 200, 774), 792.0) == "footer"


def test_merge_adjacent_blocks_joins_split_centred_lines() -> None:
    lines = [
        _line((200, 100, 400, 114), text="Centred line one of an inserted clause."),
        _line((180, 116, 420, 130), text="Centred line two of an inserted clause."),
        _line((72, 200, 540, 260), text=BODY),
    ]
    split = [_block([lines[0]]), _block([lines[1]]), _block([lines[2]])]
    merged = lr._merge_adjacent_blocks(split)
    assert len(merged) == 2
    assert len(merged[0].lines) == 2
    assert "Centred line two" in merged[0].text


def test_classify_line_alignment_left_centre_right_justified() -> None:
    content_left, content_right = 72.0, 540.0
    assert (
        _classify_line_alignment((72, 100, 300, 114), content_left, content_right)
        == "left-aligned"
    )
    assert (
        _classify_line_alignment((200, 100, 412, 114), content_left, content_right)
        == "centred"
    )
    assert (
        _classify_line_alignment((340, 100, 540, 114), content_left, content_right)
        == "right-aligned"
    )
    assert (
        _classify_line_alignment((72, 100, 540, 114), content_left, content_right)
        == "justified"
    )


def test_classify_block_alignment_justified_ignores_short_last_line() -> None:
    content_left, content_right = 72.0, 540.0
    block = _block(
        [
            _line((72, 100, 538, 114)),
            _line((72, 118, 536, 132)),
            _line((72, 136, 240, 150)),  # short last line
        ]
    )
    assert _classify_block_alignment(block, content_left, content_right) == "justified"


def test_clause_heading_line_does_not_make_justified_body_look_left() -> None:
    content_left, content_right = 72.0, 540.0
    block = _block(
        [
            _line((72, 100, 220, 114), text="2. REGISTERED OFFICE"),
            _line((72, 118, 538, 132)),
            _line((72, 136, 536, 150)),
            _line((72, 154, 240, 168)),
        ]
    )
    assert _classify_block_alignment(block, content_left, content_right) == "justified"


def test_content_column_and_page_margins() -> None:
    blocks = [
        _block([_line((72, 90, 540, 160))]),
        _block([_line((72, 180, 500, 250))]),
        _block([_line((80, 700, 200, 720))], role="footer"),
    ]
    column = _content_column(blocks[:2])
    assert column is not None
    left, right = column
    assert left == pytest.approx(72.0, abs=1.0)
    assert right == pytest.approx(520.0, abs=25.0)

    margins = _page_margins(blocks)
    assert len(margins) == 1
    assert margins[0].page == 1
    assert margins[0].left == pytest.approx(72.0, abs=0.1)
    assert margins[0].top == pytest.approx(90.0, abs=0.1)


# ---------------------------------------------------------------------------
# public API — Unable to Assess (AC5)
# ---------------------------------------------------------------------------


def test_check_invalid_empty_and_image_only() -> None:
    bad = check_layout_anomalies("doc-1", b"not-a-pdf")
    assert bad.unable_to_assess is True
    assert bad.unable_to_assess_reason
    assert bad.indentation.unable_to_assess is True
    assert bad.alignment.unable_to_assess is True
    assert bad.margin.unable_to_assess is True
    assert bad.spacing.unable_to_assess is True
    assert bad.anomalies == []

    empty = check_layout_anomalies("doc-1", _blank_pdf())
    assert empty.unable_to_assess is True
    assert "No extractable text layout" in (empty.unable_to_assess_reason or "")

    image_only = check_layout_anomalies("doc-1", _image_only_pdf())
    assert image_only.unable_to_assess is True
    assert "embedded images" in (image_only.unable_to_assess_reason or "")


def test_check_returns_empty_on_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(
        lr, "_extract_blocks", lambda doc: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    assert result.unable_to_assess is True
    assert "Unexpected error" in (result.unable_to_assess_reason or "")


def test_single_page_marks_margin_unable_but_still_analyses_layout() -> None:
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    assert result.unable_to_assess is False
    assert result.elements_analysed >= 2
    assert result.page_margins
    assert result.page_margins[0].page == 1
    assert result.margin.unable_to_assess is True
    assert "at least 2 pages" in (result.margin.unable_to_assess_reason or "")
    assert result.indentation.unable_to_assess is False
    assert result.alignment.unable_to_assess is False


def test_too_little_text_marks_indent_and_alignment_unable() -> None:
    result = check_layout_anomalies("doc-1", _single_line_pdf())
    assert result.unable_to_assess is False
    assert result.indentation.unable_to_assess is True
    assert result.alignment.unable_to_assess is True
    assert result.margin.unable_to_assess is True
    assert result.spacing.unable_to_assess is True


# ---------------------------------------------------------------------------
# public API — detections (AC1–AC4)
# ---------------------------------------------------------------------------


def test_consistent_document_has_no_indent_or_alignment_anomalies() -> None:
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    assert result.unable_to_assess is False
    assert not any(a.anomaly_type == "Indentation" for a in result.anomalies)
    assert not any(a.anomaly_type == "Alignment" for a in result.anomalies)


def test_flags_indented_paragraph() -> None:
    result = check_layout_anomalies("doc-1", _indented_pdf())
    assert result.unable_to_assess is False
    assert result.indentation.unable_to_assess is False
    indents = [a for a in result.anomalies if "indentation" in _kinds(a)]
    assert indents
    finding = indents[0]
    indent = _signal(finding, "indentation") or finding.evidence
    assert finding.page == 1
    assert finding.bbox
    assert finding.text
    assert indent["left_indent_pt"] > indent["expected_left_indent_pt"]
    assert "right_delta_pt" not in indent
    assert finding.location.startswith(("Body paragraph near y=", "Paragraph near y="))
    assert (indent.get("deviation") or finding.deviation).startswith("+")
    assert "pt" in finding.detected
    assert "comparable body paragraphs" in (finding.baseline or "")
    assert 0 < finding.confidence <= 1
    assert "indented" in finding.summary.lower()
    assert "pt" in finding.summary
    assert "Anomaly Type:" not in finding.summary
    assert "Deviation:" not in finding.summary


def test_flags_centred_paragraph_among_left_aligned() -> None:
    result = check_layout_anomalies("doc-1", _mixed_alignment_pdf())
    assert result.unable_to_assess is False
    assert result.alignment.unable_to_assess is False
    alignments = [a for a in result.anomalies if "alignment" in _kinds(a)]
    assert alignments
    finding = alignments[0]
    assert finding.page == 1
    centred = _signal(finding, "alignment") or finding.evidence
    assert centred.get("alignment") == "centred"
    assert "Centre-aligned" in (centred.get("detected") or finding.detected)
    assert 0 < finding.confidence <= 1
    # Same bbox is one finding, even if indent also fired.
    assert len([a for a in result.anomalies if a.bbox == finding.bbox]) == 1


def test_flags_right_aligned_paragraph_among_left_aligned() -> None:
    result = check_layout_anomalies("doc-1", _right_aligned_pdf())
    assert result.unable_to_assess is False
    alignments = [a for a in result.anomalies if "alignment" in _kinds(a)]
    assert alignments
    finding = alignments[0]
    aligned = _signal(finding, "alignment") or finding.evidence
    assert aligned.get("alignment") == "right-aligned"
    assert "Right-aligned" in (aligned.get("detected") or finding.detected)
    assert finding.page == 1


def test_flags_vertically_misaligned_label_and_amount() -> None:
    result = check_layout_anomalies("doc-1", _misaligned_row_pdf())
    assert result.unable_to_assess is False
    vertical = [
        a
        for a in result.anomalies
        if a.anomaly_type == "Alignment" and a.evidence.get("axis") == "vertical"
    ]
    assert vertical
    assert any("316,800" in a.text or "Chargeable Income" in a.text for a in vertical)
    assert all("Same-row pair" in a.location for a in vertical)


def test_aligned_label_and_amount_is_not_a_vertical_finding() -> None:
    result = check_layout_anomalies("doc-1", _aligned_row_pdf())
    vertical = [
        a
        for a in result.anomalies
        if a.anomaly_type == "Alignment" and a.evidence.get("axis") == "vertical"
    ]
    assert vertical == []


def test_flags_page_with_different_margins() -> None:
    result = check_layout_anomalies("doc-1", _margin_mismatch_pdf())
    assert result.unable_to_assess is False
    assert result.margin.unable_to_assess is False
    assert len(result.page_margins) == 3
    margins = [a for a in result.anomalies if a.anomaly_type == "Margin"]
    assert margins
    finding = next(a for a in margins if a.page == 3 and _signal(a, "left-margin"))
    left = _signal(finding, "left-margin")
    assert left is not None
    assert left["detected_pt"] > left["expected_pt"]
    assert (left.get("deviation") or finding.deviation).startswith("+")
    assert finding.bbox
    assert "margin" in finding.summary.lower()
    assert "pt" in finding.summary
    assert "Anomaly Type:" not in finding.summary
    assert "Detected:" not in finding.summary
    assert "Expected:" not in finding.summary
    assert "Deviation:" not in finding.summary
    assert 0 < finding.confidence <= 1


def test_elements_capture_baseline_fields() -> None:
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    assert result.elements
    assert result.elements_analysed == len(result.elements)
    element = result.elements[0]
    assert element.page == 1
    assert len(element.bbox) == 4
    assert element.left == element.bbox[0]
    assert element.top == element.bbox[1]
    assert element.right == element.bbox[2]
    assert element.bottom == element.bbox[3]
    assert element.paragraph_type == "body"
    assert element.alignment in {"left-aligned", "justified", None}
    assert element.font
    assert element.size and element.size > 0
    assert element.text


def test_centred_header_is_not_compared_to_body() -> None:
    result = check_layout_anomalies("doc-1", _header_and_body_pdf())
    assert result.unable_to_assess is False
    types = {e.paragraph_type for e in result.elements}
    assert "header" in types
    assert "body" in types
    header = next(e for e in result.elements if e.paragraph_type == "header")
    assert header.alignment == "centred"
    alignments = [a for a in result.anomalies if a.anomaly_type == "Alignment"]
    assert all("GOLDEN HARVEST" not in a.text for a in alignments)


def test_signature_is_grouped_separately_from_body() -> None:
    result = check_layout_anomalies("doc-1", _signature_pdf())
    types = {e.paragraph_type for e in result.elements}
    assert "signature" in types
    assert "body" in types
    sig = next(e for e in result.elements if e.paragraph_type == "signature")
    assert "Signed" in sig.text
    alignments = [a for a in result.anomalies if a.anomaly_type == "Alignment"]
    assert all(a.text != sig.text for a in alignments)


def test_looks_like_signature_requires_zone_and_keyword() -> None:
    sig = _block(
        [_line((320, 680, 540, 700), text="Signed by Jane Tan, Director")],
        role="body",
    )
    assert _looks_like_signature(sig) is True
    mid_page = _block(
        [_line((72, 400, 300, 420), text="Signed by Jane Tan, Director")],
        role="body",
    )
    assert _looks_like_signature(mid_page) is False
    lower_unrelated = _block(
        [_line((320, 680, 540, 700), text="Please retain this copy for your records.")],
        role="body",
    )
    assert _looks_like_signature(lower_unrelated) is False


def test_row_alignment_flags_vertically_shifted_amount() -> None:
    label = _block([_line((72, 200, 280, 214), text="Chargeable Income")])
    amount = _block([_line((460, 212, 540, 226), text="316,800")])
    findings = lr._check_row_alignment([label, amount])
    assert findings
    assert all(a.evidence.get("axis") == "vertical" for a in findings)
    assert any(abs(a.evidence["top_delta_pt"]) >= 6 for a in findings)
    assert any("316,800" in a.text or "Chargeable Income" in a.text for a in findings)
    assert any("Same-row pair" in a.location for a in findings)


def test_row_alignment_includes_table_cells() -> None:
    label = _block(
        [_line((72, 200, 280, 214), text="Chargeable Income")], role="table"
    )
    amount = _block([_line((460, 212, 540, 226), text="316,800")], role="table")
    findings = lr._check_row_alignment([label, amount])
    assert findings
    assert all(a.evidence.get("axis") == "vertical" for a in findings)


def test_row_alignment_ignores_aligned_label_and_amount() -> None:
    label = _block([_line((72, 200, 280, 214), text="Chargeable Income")])
    amount = _block([_line((460, 200, 540, 214), text="316,800")])
    assert lr._check_row_alignment([label, amount]) == []


def test_row_alignment_ignores_stacked_total_above_amount() -> None:
    label = _block([_line((72, 200, 280, 214), text="Chargeable Income")])
    total = _block([_line((460, 200, 540, 214), text="Total ($)")])
    amount = _block([_line((470, 230, 540, 244), text="316,800")])
    findings = lr._check_row_alignment([label, total, amount])
    assert not any("316,800" in a.text for a in findings)


def test_row_alignment_ignores_wrapping_label_sharing_top() -> None:
    label = _block(
        [
            _line((72, 200, 300, 214), text="Chargeable Income after"),
            _line((72, 216, 280, 230), text="donations and reliefs"),
        ]
    )
    amount = _block([_line((460, 200, 540, 214), text="316,800")])
    assert lr._check_row_alignment([label, amount]) == []


def test_row_alignment_pairs_noa_amount_with_label_not_column_header() -> None:
    """C034: 316,800 sits a few pt above its label. Total ($) is the
    column header on the row above; the tax-discharge sentence is the
    paragraph above that. Those four must not collapse into one cluster
    scored against a median."""
    discharge = _block(
        [
            _line(
                (51.02, 377.52, 329.48, 391.29),
                text="There is a tax discharge of $18,215.50 for this assessment.",
            )
        ]
    )
    total = _block([_line((514.76, 400.85, 555.59, 415.31), text="Total ($)")])
    amount = _block([_line((519.45, 420.95, 555.58, 432.11), text="316,800")])
    label = _block(
        [
            _line(
                (51.02, 426.93, 357.19, 440.7),
                text="Estimated Chargeable Income before deducting Exempt Amount",
            )
        ]
    )
    findings = lr._check_row_alignment([discharge, total, amount, label])
    assert findings
    assert all(a.evidence.get("axis") == "vertical" for a in findings)
    assert all("316,800" in a.text for a in findings)
    assert not any("Total ($)" in a.text for a in findings)
    assert not any("tax discharge" in a.text for a in findings)
    assert not any("Chargeable Income" in a.text for a in findings)
    assert findings[0].evidence["baseline_delta_pt"] <= -6
    assert "Chargeable Income" in findings[0].location
    assert findings[0].evidence["row_count"] == 2


def test_centre_shift_flags_off_axis_cover_company_name() -> None:
    pw, ph = 595.28, 841.89

    def cover(bbox, text) -> _Block:
        return _block([_line(bbox, text=text, page_width=pw, page_height=ph)])

    titles = [
        cover((225.21, 107.59, 370.07, 141.35), "THE COMPANIES ACT 1967"),
        cover((187.02, 164.59, 408.26, 179.74), "PRIVATE COMPANY LIMITED BY SHARES"),
        cover((256.39, 214.59, 338.89, 248.35), "CONSTITUTION OF"),
        cover((195.0, 265.88, 495.11, 283.74), "FALCON AVIATION SUPPORT PTE LTD"),
        cover((253.73, 291.61, 341.54, 305.35), "(UEN 201778908C)"),
        cover((232.32, 334.61, 362.95, 348.35), "Incorporated on 30 May 2017"),
        cover((241.77, 353.61, 353.51, 367.35), "Adopted on 30 May 2017"),
        cover((212.6, 462.61, 382.67, 476.35), "Ashworth Corporate Advisory Pte. Ltd."),
        cover(
            (168.41, 481.61, 426.87, 495.35),
            "137 Cecil Street, #09-01 Cecil Building, Singapore 069537",
        ),
    ]
    findings = lr._check_centre_shift(titles)
    assert len(findings) == 1
    finding = findings[0]
    assert "FALCON" in finding.text
    assert finding.evidence["kind"] == "centre-shift"
    assert finding.evidence["axis"] == "horizontal"
    assert finding.evidence["mid_delta_pt"] >= 18
    assert "Cover title" in finding.location


def test_centre_shift_ignores_aligned_cover_titles() -> None:
    pw, ph = 595.28, 841.89

    def cover(bbox, text) -> _Block:
        return _block([_line(bbox, text=text, page_width=pw, page_height=ph)])

    titles = [
        cover((225.21, 107.59, 370.07, 141.35), "THE COMPANIES ACT 1967"),
        cover((187.02, 164.59, 408.26, 179.74), "PRIVATE COMPANY LIMITED BY SHARES"),
        cover((256.39, 214.59, 338.89, 248.35), "CONSTITUTION OF"),
        cover((148.0, 265.88, 448.0, 283.74), "FALCON AVIATION SUPPORT PTE LTD"),
        cover((253.73, 291.61, 341.54, 305.35), "(UEN 201778908C)"),
        cover((232.32, 334.61, 362.95, 348.35), "Incorporated on 30 May 2017"),
    ]
    # Mid of 148–448 is 298, matching the other cover titles.
    assert lr._check_centre_shift(titles) == []


def test_table_overlap_marks_block_as_table() -> None:
    cell = _block([_line((80, 200, 200, 220), text="Amount due 100.00")])
    assert cell.role == "body"
    _apply_table_roles([cell], {0: [pymupdf.Rect(70, 190, 540, 400)]})
    assert cell.role == "table"


def test_anomaly_records_type_page_and_location() -> None:
    result = check_layout_anomalies("doc-1", _indented_pdf())
    for anomaly in result.anomalies:
        assert anomaly.anomaly_type in {"Indentation", "Alignment", "Margin", "Spacing"}
        assert anomaly.page >= 1
        assert len(anomaly.bbox) == 4
        assert anomaly.detected
        assert anomaly.expected
        assert anomaly.deviation
        assert 0 < anomaly.confidence <= 1
        assert anomaly.summary
        assert "Anomaly Type:" not in anomaly.summary
        assert "Detected:" not in anomaly.summary
        assert "Page:" not in anomaly.summary


def test_flags_paragraph_that_ends_short_of_the_column() -> None:
    result = check_layout_anomalies("doc-1", _short_right_edge_pdf())
    rights = [a for a in result.anomalies if "right-edge" in _kinds(a)]
    assert rights
    finding = rights[0]
    edge = _signal(finding, "right-edge") or finding.evidence
    assert edge["right_delta_pt"] <= -12
    assert finding.page == 1


def test_short_one_line_clause_is_not_right_edge_or_left_vs_justified() -> None:
    result = check_layout_anomalies("doc-1", _justified_clauses_with_short_name_pdf())
    hits = [
        a
        for a in result.anomalies
        if "Falcon Aviation" in a.text
        and ("right-edge" in _kinds(a) or "alignment" in _kinds(a))
    ]
    assert hits == []


def test_line_spacing_flags_a_stretched_leading() -> None:
    blocks = [
        _ml_block(90, [14, 14]),
        _ml_block(140, [14, 14]),
        _ml_block(190, [14, 24]),
        _ml_block(260, [14, 14]),
    ]
    findings, check = lr._check_line_spacing(blocks)
    assert check.unable_to_assess is False
    assert findings
    assert all(a.evidence["kind"] == "line-spacing" for a in findings)
    assert any(a.evidence["leading_delta_pt"] >= 4 for a in findings)
    assert any("Line spacing" in a.location for a in findings)


def test_line_spacing_ignores_uniform_leading() -> None:
    blocks = [_ml_block(90 + i * 50, [14, 14]) for i in range(4)]
    findings, check = lr._check_line_spacing(blocks)
    assert check.unable_to_assess is False
    assert findings == []


def test_paragraph_spacing_flags_a_wide_gap() -> None:
    result = check_layout_anomalies("doc-1", _wide_paragraph_gap_pdf())
    assert result.spacing.unable_to_assess is False
    gaps = [a for a in result.anomalies if "paragraph-spacing" in _kinds(a)]
    assert gaps
    assert any(
        abs((_signal(a, "paragraph-spacing") or a.evidence)["gap_delta_pt"]) >= 8
        for a in gaps
    )


def test_paragraph_spacing_records_space_before_and_after_gap() -> None:
    y = 90
    h = 40
    gaps = [10, 10, 28, 10]
    blocks = []
    for i in range(5):
        text = f"Clause {i + 1} of the Company shall be at such place in Singapore."
        blocks.append(_block([_line((72, y, 540, y + h), text=text)]))
        if i < len(gaps):
            y = y + h + gaps[i]
    findings, check = lr._check_paragraph_spacing(blocks)
    assert check.unable_to_assess is False
    assert findings
    assert all(a.evidence["kind"] == "paragraph-spacing" for a in findings)
    assert any(a.evidence.get("space") == "before" for a in findings)


def test_consecutive_indents_are_marked_as_a_local_pattern() -> None:
    result = check_layout_anomalies("doc-1", _two_indented_pdf())
    flagged = [
        a
        for a in result.anomalies
        if "indentation" in _kinds(a) or a.evidence.get("local_pattern")
    ]
    assert len(flagged) >= 2
    assert any(a.evidence.get("consecutive_run", 1) >= 2 for a in flagged)
    assert any(a.evidence.get("local_pattern") is True for a in flagged)
    assert any("consecutive anomalous paragraphs" in a.location for a in flagged)
    assert all(a.evidence.get("kind") != "local-pattern" for a in result.anomalies)


def test_same_paragraph_signals_are_one_finding() -> None:
    bbox = (72.0, 412.0, 540.0, 460.0)

    def _item(*, anomaly_type, kind, detected, expected, deviation, extra) -> LayoutAnomaly:
        return LayoutAnomaly(
            anomaly_type=anomaly_type,
            page=5,
            location="Body paragraph near y=412",
            bbox=bbox,
            text="Paragraph 12 of the constitution.",
            detected=detected,
            expected=expected,
            baseline="comparable body paragraphs",
            deviation=deviation,
            confidence=0.7,
            evidence={"kind": kind, **extra},
            summary=f"Anomaly Type: {anomaly_type}",
        )

    merged = lr._coalesce_signals(
        [
            _item(
                anomaly_type="Indentation",
                kind="indentation",
                detected="96.0 pt",
                expected="72.0 pt",
                deviation="+24.0 pt",
                extra={"left_delta_pt": 24.0},
            ),
            _item(
                anomaly_type="Indentation",
                kind="right-edge",
                detected="519.0 pt",
                expected="540.0 pt",
                deviation="+21.0 pt",
                extra={"right_delta_pt": 21.0},
            ),
            _item(
                anomaly_type="Spacing",
                kind="paragraph-spacing",
                detected="22.0 pt",
                expected="11.0 pt",
                deviation="+11.0 pt",
                extra={"gap_delta_pt": 11.0},
            ),
        ]
    )
    assert len(merged) == 1
    finding = merged[0]
    assert finding.page == 5
    assert {s["kind"] for s in finding.evidence["signals"]} == {
        "indentation",
        "right-edge",
        "paragraph-spacing",
    }
    assert finding.evidence["signal_count"] == 3
    assert "indentation: +24.0 pt" in finding.detected
    assert "right-edge: +21.0 pt" in finding.detected
    assert "paragraph-spacing: +11.0 pt" in finding.detected
    assert finding.location.startswith("Paragraph near y=")
    assert "local_pattern" not in finding.evidence
    assert finding.summary
    assert "other layout measurements" in finding.summary.lower()
    assert "+24.0 pt" not in finding.summary
    assert "Anomaly Type:" not in finding.summary
    assert "Detected:" not in finding.summary


def test_flags_shifted_header_and_footer_chrome() -> None:
    result = check_layout_anomalies("doc-1", _shifted_chrome_pdf())
    chrome = [
        a
        for a in result.anomalies
        if a.evidence.get("kind") in {"header-position", "footer-position"}
    ]
    assert chrome
    assert any(a.evidence["kind"] == "header-position" and a.page == 3 for a in chrome)
    assert any(a.evidence["kind"] == "footer-position" and a.page == 3 for a in chrome)
    header = next(a for a in chrome if a.evidence["kind"] == "header-position")
    assert header.location == "Header vertical position"
    assert abs(header.evidence["delta_pt"]) >= 12
    assert "ACME" in header.text


def test_consistent_chrome_is_not_flagged() -> None:
    result = check_layout_anomalies("doc-1", _consistent_chrome_pdf())
    chrome = [
        a
        for a in result.anomalies
        if a.evidence.get("kind") in {"header-position", "footer-position"}
    ]
    assert chrome == []


def test_clause_titles_in_header_band_at_the_same_y_are_not_shifts() -> None:
    titles = ["1. NAME", "7. TRANSMISSION OF SHARES", "17. WINDING UP"]
    blocks = []
    for page, title in enumerate(titles):
        blocks.append(
            _block(
                [
                    _line(
                        (76.87, 67.7, 218.33, 80.78),
                        text=title,
                        page=page,
                        page_height=841.89,
                    )
                ],
                role="header",
            )
        )
        blocks.append(
            _block(
                [_line((76.87, 90, 518.41, 140), text=BODY, page=page, page_height=841.89)]
            )
        )
        blocks.append(
            _block(
                [
                    _line(
                        (70.87, 794.14, 524.41, 804.45),
                        text=f"Page {page + 1} of 3",
                        page=page,
                        page_height=841.89,
                    )
                ],
                role="footer",
            )
        )
    findings, check = lr._check_chrome_position(blocks)
    assert check.unable_to_assess is False
    assert findings == []
