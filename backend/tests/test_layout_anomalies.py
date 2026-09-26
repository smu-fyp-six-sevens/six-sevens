"""Layout anomalies: indent/alignment/margin checks plus Unable to Assess."""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf
import pytest

from app.fraud_detection import layout_anomalies as lr
from app.fraud_detection.layout_anomalies import (
    LayoutAnomaly,
    PageMargins,
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


def _cover_then_clause_pages_pdf(*, inset_last: bool = False) -> bytes:
    def build(doc):
        cover = doc.new_page(width=612, height=792)
        titles = [
            "THE COMPANIES ACT 1967",
            "PRIVATE COMPANY LIMITED BY SHARES",
            "CONSTITUTION OF",
            "ACME TRADING PTE LTD",
            "(UEN 201234567A)",
            "Incorporated on 30 May 2017",
        ]
        y = 80
        for title in titles:
            _textbox(cover, pymupdf.Rect(160, y, 450, y + 28), title, align=1)
            y += 40
        for page_index in range(2):
            page = doc.new_page(width=612, height=792)
            inset = 50 if inset_last and page_index == 1 else 0
            left, top, right = 72 + inset, 90 + inset, 540 - inset
            y = top
            for _ in range(3):
                _textbox(page, pymupdf.Rect(left, y, right, y + 70), BODY, align=0)
                y += 80

    return _pdf_bytes(build)


def _heading_among_justified_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        _textbox(page, pymupdf.Rect(72, 90, 280, 112), "2. REGISTERED OFFICE", align=0)
        y = 130
        for _ in range(4):
            _textbox(page, pymupdf.Rect(72, y, 540, y + 70), BODY, align=3)
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


def test_clause_heading_is_not_compared_to_justified_body() -> None:
    result = check_layout_anomalies("doc-1", _heading_among_justified_pdf())
    types = {e.paragraph_type for e in result.elements}
    assert "heading" in types
    heading = next(e for e in result.elements if e.paragraph_type == "heading")
    assert "REGISTERED OFFICE" in heading.text
    alignments = [a for a in result.anomalies if "alignment" in _kinds(a)]
    assert all("REGISTERED OFFICE" not in a.text for a in alignments)


def test_cover_titles_are_grouped_separately_from_body() -> None:
    result = check_layout_anomalies("doc-1", _cover_then_clause_pages_pdf())
    types = {e.paragraph_type for e in result.elements}
    assert "cover" in types
    assert "body" in types
    covers = [e for e in result.elements if e.paragraph_type == "cover"]
    assert any("CONSTITUTION OF" in e.text for e in covers)
    alignments = [a for a in result.anomalies if "alignment" in _kinds(a)]
    assert all(a.page != 1 or "centre-shift" in _kinds(a) for a in alignments)


def test_single_cover_page_is_not_flagged_against_inner_margins() -> None:
    result = check_layout_anomalies("doc-1", _cover_then_clause_pages_pdf())
    margins = [a for a in result.anomalies if a.anomaly_type == "Margin"]
    assert all(a.page != 1 for a in margins)


def test_inner_page_margin_mismatch_still_flags_when_cover_exists() -> None:
    result = check_layout_anomalies(
        "doc-1", _cover_then_clause_pages_pdf(inset_last=True)
    )
    margins = [a for a in result.anomalies if a.anomaly_type == "Margin"]
    assert any(a.page == 3 for a in margins)
    assert all(a.page != 1 for a in margins)


def test_split_leading_headings_separates_clause_title() -> None:
    block = _block(
        [
            _line((72, 100, 220, 114), text="2. REGISTERED OFFICE"),
            _line((72, 118, 538, 132)),
            _line((72, 136, 536, 150)),
        ]
    )
    split = lr._split_leading_headings([block])
    assert len(split) == 2
    assert split[0].role == "heading"
    assert "REGISTERED OFFICE" in split[0].text
    assert split[1].role == "body"


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


def test_short_cover_titles_are_not_a_consecutive_clause_stretch() -> None:
    bbox = (200.0, 100.0, 380.0, 120.0)
    finding = LayoutAnomaly(
        anomaly_type="Alignment",
        page=1,
        location="Cover title near y=100",
        bbox=bbox,
        text="CONSTITUTION OF",
        detected="Centre-aligned",
        expected="Left-aligned",
        deviation="1 paragraph",
        confidence=0.7,
        evidence={"kind": "centre-shift", "axis": "horizontal"},
        summary="shifted",
    )
    assert lr._is_clause_stretch_finding(finding) is False


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


# ---------------------------------------------------------------------------
# untested branches
# ---------------------------------------------------------------------------


def _finding(**kwargs) -> LayoutAnomaly:
    values = dict(
        anomaly_type="Alignment",
        page=1,
        location="Body paragraph near y=100",
        bbox=(72.0, 100.0, 540.0, 160.0),
        text=BODY,
        detected="72 pt",
        expected="72 pt",
        deviation="+0 pt",
        confidence=0.7,
        evidence={},
        summary="placeholder",
    )
    values.update(kwargs)
    return LayoutAnomaly(**values)


def _empty_line_block(text: str = BODY, **kwargs) -> _Block:
    values = dict(
        lines=[],
        bbox=(72.0, 100.0, 540.0, 160.0),
        page=0,
        page_width=612.0,
        page_height=792.0,
        text=text,
        role="body",
    )
    values.update(kwargs)
    return _Block(**values)


def test_extract_skips_broken_lines_and_table_finder_errors() -> None:
    class Page:
        rect = pymupdf.Rect(0, 0, 612, 792)

        def get_text(self, _mode):
            return {
                "blocks": [
                    {
                        "type": 0,
                        "bbox": (72, 90, 200, 140),
                        "lines": [
                            {"bbox": (1, 2), "spans": [{"text": "bad bbox"}]},
                            {
                                "bbox": (72, 100, 200, 114),
                                "spans": [{"text": "   "}],
                            },
                            {
                                "bbox": (72, 120, 300, 134),
                                "spans": [
                                    {"text": "  "},
                                    {"text": "Visible", "font": "Helv", "size": 11},
                                ],
                            },
                        ],
                    },
                    {
                        "type": 0,
                        "bbox": (72, 200, 200, 220),
                        "lines": [{"bbox": None, "spans": [{"text": "skip me"}]}],
                    },
                ]
            }

        def find_tables(self):
            raise RuntimeError("tables unavailable")

    class Doc:
        def __iter__(self):
            yield Page()

    blocks = lr._extract_blocks(Doc())
    assert len(blocks) == 1
    assert "Visible" in blocks[0].text


def test_table_rects_skips_missing_bbox_and_keeps_valid() -> None:
    class Page:
        def find_tables(self):
            return SimpleNamespace(
                tables=[
                    SimpleNamespace(bbox=None),
                    SimpleNamespace(bbox=(70, 190, 540, 400)),
                ]
            )

    rects = lr._table_rects(Page())
    assert len(rects) == 1
    assert rects[0] == pymupdf.Rect(70, 190, 540, 400)


def test_apply_table_roles_skips_zero_area_block() -> None:
    cell = _block([_line((80, 200, 80, 200), text="Amount")])
    _apply_table_roles([cell], {0: [pymupdf.Rect(70, 190, 540, 400)]})
    assert cell.role == "body"


def test_heading_roles_from_section_titles_not_labels_or_amounts() -> None:
    upper = _block([_line((72, 100, 220, 114), text="CONSTITUTION OF")])
    assert lr._is_heading_block(upper) is True
    clause = _block([_line((72, 90, 280, 112), text="2. REGISTERED OFFICE")])
    assert lr._is_heading_block(clause) is True
    mixed_label = _block(
        [
            _line(
                (51, 426, 357, 440),
                text="Account balance carried forward from the previous period",
            )
        ],
        font="Helvetica-Bold",
    )
    assert lr._is_heading_block(mixed_label) is False
    amount = _block(
        [_line((519, 420, 555, 432), text="316,800")],
        font="LiberationSans-Bold",
    )
    assert lr._is_heading_block(amount) is False
    numbered_line = _block(
        [
            _line(
                (51.02, 137.31, 175.3, 180.71),
                text="42 ORCHARD ROAD #01-02 SINGAPORE 238801",
            )
        ]
    )
    assert lr._is_heading_block(numbered_line) is False
    header = _block([_line((72, 10, 200, 24), text="ACME PTE LTD")], role="header")
    assert lr._is_heading_block(header) is False


def test_bold_noa_amount_stays_body_and_flags_vertical_shift() -> None:
    clauses = [
        _block([_line((51, 90 + i * 50, 540, 130 + i * 50), text=BODY)])
        for i in range(3)
    ]
    label = _block(
        [
            _line(
                (51.02, 426.93, 357.19, 440.7),
                text="Estimated Chargeable Income before deducting Exempt Amount",
            )
        ],
        font="Helvetica-Bold",
    )
    amount = _block(
        [_line((519.45, 420.95, 555.58, 432.11), text="316,800")],
        font="LiberationSans-Bold",
    )
    blocks = clauses + [label, amount]
    lr._apply_heading_and_cover_roles(blocks)
    assert label.role == "body"
    assert amount.role == "body"
    findings = lr._check_row_alignment(blocks)
    assert any("316,800" in a.text for a in findings)


def test_short_line_that_does_not_reach_the_column_is_not_an_indent() -> None:
    blocks = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block([_line((72, 180, 540, 250), text=BODY)]),
        _block([_line((86, 260, 340, 278), text="Document title sitting inset from the column")]),
        _block([_line((72, 310, 540, 380), text=BODY)]),
    ]
    findings, check = lr._check_indentation(blocks)
    assert check.unable_to_assess is False
    assert not any("Document title" in a.text for a in findings)


def test_shared_left_edge_is_fill_not_an_alignment_change() -> None:
    ragged = [
        _block([_line((72, 90 + i * 40, 400, 120 + i * 40), text=BODY)])
        for i in range(6)
    ]
    filled = [
        _block([_line((72, 340 + i * 30, 540, 365 + i * 30), text=BODY)])
        for i in range(2)
    ]
    findings, check = lr._check_alignment(ragged + filled)
    assert check.unable_to_assess is False
    assert findings == []


def test_assign_alignments_skips_page_without_a_content_column() -> None:
    collapsed = _block([_line((200, 100, 200, 160), text=BODY)])
    lr._assign_alignments([collapsed])
    assert collapsed.alignment is None


def test_horizontally_related_falls_back_to_midpoint() -> None:
    left = _block([_line((72, 100, 72, 114), text="A")])
    near = _block([_line((70, 116, 74, 130), text="B")])
    assert lr._horizontally_related(left, near) is True
    far = _block([_line((400, 116, 420, 130), text="C")])
    assert lr._horizontally_related(left, far) is False


def test_as_bbox_and_width_band_guards() -> None:
    assert lr._as_bbox(None) is None
    assert lr._as_bbox((1, 2, 3)) is None
    block = _block([_line((72, 100, 200, 114))])
    assert lr._width_band(block, 100.0, 100.0) == "short"
    assert _content_column(
        [
            _block([_line((200, 100, 200, 160), text=BODY)]),
            _block([_line((200, 180, 200, 240), text=BODY)]),
        ]
    ) is None


def test_indentation_unable_when_no_full_width_column() -> None:
    wide = _block([_line((72, 90, 540, 160), text=BODY)])
    short = _block([_line((72, 180, 160, 250), text=BODY)])
    findings, check = lr._check_indentation([wide, short])
    assert findings == []
    assert check.unable_to_assess is True
    assert "full-width" in (check.unable_to_assess_reason or "")

    collapsed_a = _block([_line((72, 90, 72, 160), text=BODY)])
    collapsed_b = _block([_line((72, 180, 72, 250), text=BODY)])
    findings, check = lr._check_indentation([collapsed_a, collapsed_b])
    assert findings == []
    assert check.unable_to_assess is True


def test_right_edge_skips_tied_expected_and_missing_column() -> None:
    def edged(x1: float, y0: float) -> _Block:
        return _block(
            [
                _line((72, y0, x1, y0 + 12), text=BODY),
                _line((72, y0 + 14, x1, y0 + 26), text=BODY),
                _line((72, y0 + 28, 200, y0 + 40), text="Last line of the clause."),
            ]
        )

    findings, check = lr._check_right_edge(
        [edged(540, 80), edged(500, 160), edged(540, 240), edged(500, 320)]
    )
    assert check.unable_to_assess is False

    collapsed = [
        _block(
            [
                _line((72, 80, 72, 92), text=BODY),
                _line((72, 94, 72, 106), text=BODY),
            ]
        ),
        _block(
            [
                _line((72, 160, 72, 172), text=BODY),
                _line((72, 174, 72, 186), text=BODY),
            ]
        ),
    ]
    findings, check = lr._check_right_edge(collapsed)
    assert findings == []
    assert check.unable_to_assess is True


def test_line_spacing_unable_when_too_few_gaps() -> None:
    findings, check = lr._check_line_spacing([_ml_block(90, [14]), _ml_block(200, [14])])
    assert findings == []
    assert check.unable_to_assess is True
    assert "intra-paragraph" in (check.unable_to_assess_reason or "")


def test_paragraph_spacing_skips_thin_or_overlapping_pages() -> None:
    split_pages = [
        _block([_line((72, 90, 540, 160), text=BODY, page=0)]),
        _block([_line((72, 90, 540, 160), text=BODY, page=1)]),
        _block([_line((72, 90, 540, 160), text=BODY, page=2)]),
    ]
    findings, check = lr._check_paragraph_spacing(split_pages)
    assert findings == []
    assert check.unable_to_assess is True

    overlapping = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block([_line((72, 100, 540, 170), text=BODY)]),
        _block([_line((72, 110, 540, 180), text=BODY)]),
    ]
    findings, check = lr._check_paragraph_spacing(overlapping)
    assert findings == []
    assert check.unable_to_assess is True


def test_paragraph_spacing_ignores_section_break_gaps(monkeypatch) -> None:
    monkeypatch.setattr(lr, "_PARA_SECTION_BREAK_MULT", 0.5)
    blocks = [
        _block([_line((72, 90, 540, 130), text=BODY)]),
        _block([_line((72, 140, 540, 180), text=BODY)]),
        _block([_line((72, 260, 540, 300), text=BODY)]),
    ]
    findings, check = lr._check_paragraph_spacing(blocks)
    assert check.unable_to_assess is False
    assert findings == []


def test_signal_kind_and_summary_fallbacks() -> None:
    assert lr._signal_kind(_finding(anomaly_type="Indentation", evidence={})) == "indentation"
    assert lr._signal_kind(_finding(anomaly_type="Spacing", evidence={})) == "spacing"
    assert lr._evidence_kind("Indentation", {}) == "indentation"
    assert lr._evidence_kind("Spacing", {}) == "spacing"
    assert lr._label_alignment("mixed-odd") == "mixed odd"
    assert "unlike comparable" in lr._format_summary(
        "Alignment", {"alignment": "centred"}, deviation="1 paragraph"
    )
    assert "gap around this paragraph" in lr._format_summary(
        "Spacing", {"kind": "paragraph-spacing"}, deviation="+8 pt"
    )
    assert "unknown check" in lr._format_summary(
        "Spacing", {"kind": "unknown-check"}, deviation="+3 pt"
    )
    assert lr._pct(1, 0) == "0%"
    assert lr._majority_confidence(1, 0) == 0.5


def test_alignment_unable_and_short_band_majority_guard() -> None:
    empty = [_empty_line_block(), _empty_line_block(bbox=(72.0, 180.0, 540.0, 240.0))]
    findings, check = lr._check_alignment(empty)
    assert findings == []
    assert check.unable_to_assess is True

    collapsed = [
        _block([_line((72, 90, 72, 160), text=BODY)]),
        _block([_line((72, 180, 72, 250), text=BODY)]),
    ]
    findings, check = lr._check_alignment(collapsed)
    assert findings == []
    assert check.unable_to_assess is True

    tied = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block(
            [
                _line((200, 180, 540, 194), text=BODY),
                _line((250, 196, 540, 210), text=BODY),
            ]
        ),
    ]
    findings, check = lr._check_alignment(tied)
    assert check.unable_to_assess is False
    assert findings == []

    shorts = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block([_line((72, 170, 540, 240), text=BODY)]),
        _block([_line((72, 260, 180, 274), text="Date: 1 January 2020")]),
        _block([_line((72, 280, 180, 294), text="Date: 2 January 2020")]),
        _block([_line((200, 300, 412, 314), text="ACME HOLDINGS")]),
    ]
    findings, check = lr._check_alignment(shorts)
    assert check.unable_to_assess is False
    assert all(a.text != "ACME HOLDINGS" for a in findings)


def test_alignment_flags_short_centred_paragraph_against_full_column() -> None:
    full = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block([_line((72, 170, 540, 240), text=BODY)]),
        _block([_line((72, 250, 540, 320), text=BODY)]),
    ]
    inserted = _block(
        [
            _line(
                (190, 340, 410, 354),
                text="Inserted centred clause line one is long enough.",
            ),
            _line(
                (180, 356, 420, 370),
                text="Inserted centred clause line two is long enough.",
            ),
        ]
    )
    findings, check = lr._check_alignment(full + [inserted])
    assert check.unable_to_assess is False
    assert findings
    assert any(a.evidence.get("alignment") == "centred" for a in findings)


def test_alignment_skips_unreliable_or_already_flagged_short_inserts() -> None:
    justified = [
        _block(
            [
                _line((72, 90, 538, 104), text=BODY),
                _line((72, 106, 536, 120), text=BODY),
                _line((72, 122, 240, 136), text="Short last line."),
            ]
        ),
        _block(
            [
                _line((72, 160, 538, 174), text=BODY),
                _line((72, 176, 536, 190), text=BODY),
                _line((72, 192, 240, 206), text="Short last line."),
            ]
        ),
    ]
    one_liner = _block([_line((72, 230, 220, 244), text="The name of the Company is Acme.")])
    findings, _ = lr._check_alignment(justified + [one_liner])
    assert all("The name of the Company" not in a.text for a in findings)

    full = [
        _block([_line((72, 90, 540, 160), text=BODY)]),
        _block([_line((72, 170, 540, 240), text=BODY)]),
        _block([_line((72, 250, 540, 320), text=BODY)]),
    ]
    short_left = [
        _block([_line((72, 340, 200, 354), text="Date: 1 January 2020")]),
        _block([_line((72, 360, 200, 374), text="Date: 2 January 2020")]),
        _block([_line((72, 380, 200, 394), text="Date: 3 January 2020")]),
    ]
    centred_short = _block(
        [
            _line((190, 410, 410, 424), text="Inserted centred clause line one is long enough."),
            _line((180, 426, 420, 440), text="Inserted centred clause line two is long enough."),
        ]
    )
    findings, _ = lr._check_alignment(full + short_left + [centred_short])
    centred = [a for a in findings if a.evidence.get("alignment") == "centred"]
    assert len(centred) == 1


def test_row_alignment_skips_duplicate_pair(monkeypatch) -> None:
    label = _block([_line((72, 200, 280, 214), text="Chargeable Income")])
    amount = _block([_line((460, 212, 540, 226), text="316,800")])
    monkeypatch.setattr(
        lr, "_nearest_row_pairs", lambda _blocks: [(label, amount), (label, amount)]
    )
    findings = lr._check_row_alignment([label, amount])
    assert len(findings) == 1


def test_row_alignment_skips_partners_sharing_the_same_left_edge() -> None:
    stacked = [
        _block([_line((72, 200, 72, 214), text="Chargeable Income")]),
        _block([_line((72, 202, 72, 216), text="316,800")]),
    ]
    assert lr._check_row_alignment(stacked) == []


def test_wrapping_label_flags_when_neither_edge_lines_up() -> None:
    label = _block(
        [
            _line((72, 200, 300, 214), text="Chargeable Income after"),
            _line((72, 216, 280, 230), text="donations and reliefs"),
        ]
    )
    amount = _block([_line((460, 222, 540, 236), text="316,800")])
    findings = lr._check_row_alignment([label, amount])
    assert findings
    assert any(abs(a.evidence["top_delta_pt"]) >= 6 for a in findings)
    assert any(abs(a.evidence["baseline_delta_pt"]) >= 6 for a in findings)


def test_side_by_side_allows_partial_horizontal_overlap() -> None:
    left = _block([_line((72, 200, 300, 214), text="Chargeable Income")])
    right = _block([_line((250, 206, 400, 220), text="316,800")])
    assert lr._side_by_side(left, right) is True
    findings = lr._check_row_alignment([left, right])
    assert findings


def test_centre_shift_skips_tied_or_weak_axis() -> None:
    pw, ph = 595.28, 841.89

    def cover(bbox, text) -> _Block:
        return _block(
            [_line(bbox, text=text, page_width=pw, page_height=ph)], role="cover"
        )

    tied = [
        cover((80, 100, 160, 114), "THE COMPANIES ACT 1967"),
        cover((200, 140, 300, 154), "PRIVATE COMPANY LIMITED"),
        cover((320, 180, 430, 194), "CONSTITUTION OF"),
        cover((450, 220, 540, 234), "ACME PTE LTD"),
    ]
    assert lr._check_centre_shift(tied) == []

    weak = [
        cover((220, 100, 360, 114), "THE COMPANIES ACT 1967"),
        cover((220, 140, 360, 154), "PRIVATE COMPANY LIMITED"),
        cover((80, 180, 200, 194), "CONSTITUTION OF"),
        cover((400, 220, 540, 234), "ACME PTE LTD"),
    ]
    assert lr._check_centre_shift(weak) == []

    zero_width_page = [
        _block([_line((72, 90 + i * 20, 200, 104 + i * 20), text=f"Title {i}", page_width=0)])
        for i in range(4)
    ]
    assert lr._is_title_page(zero_width_page) is False


def test_execution_page_with_a_clause_is_not_a_title_page() -> None:
    pw, ph = 595.28, 841.89
    shorts = [
        _block(
            [
                _line(
                    (200, 80 + i * 24, 400, 94 + i * 24),
                    text=f"TITLE LINE {i} HERE",
                    page_width=pw,
                    page_height=ph,
                )
            ]
        )
        for i in range(4)
    ]
    assert lr._is_title_page(shorts) is True
    with_clause = shorts + [
        _block([_line((76, 200, 518, 260), text=BODY, page_width=pw, page_height=ph)])
    ]
    assert lr._is_title_page(with_clause) is False

    cover = [
        _block(
            [
                _line(
                    (200, 80 + i * 30, 400, 94 + i * 30),
                    text=title,
                    page=0,
                    page_width=pw,
                    page_height=ph,
                )
            ]
        )
        for i, title in enumerate(
            [
                "THE COMPANIES ACT 1967",
                "CONSTITUTION OF",
                "ACME PTE LTD HERE",
                "INCORPORATED ON 30 MAY",
            ]
        )
    ]
    execution = [
        _block(
            [_line((76, 67, 180, 80), text="EXECUTION PAGE", page=1, page_width=pw, page_height=ph)]
        ),
        _block([_line((76, 85, 518, 126), text=BODY, page=1, page_width=pw, page_height=ph)]),
        _block(
            [
                _line(
                    (76, 296, 208, 309),
                    text="Dated this 30 day of May 2017.",
                    page=1,
                    page_width=pw,
                    page_height=ph,
                )
            ]
        ),
        _block(
            [
                _line(
                    (76, 332, 214, 345),
                    text="Witness to the above signatures:",
                    page=1,
                    page_width=pw,
                    page_height=ph,
                )
            ]
        ),
    ]
    lr._apply_heading_and_cover_roles(cover + execution)
    pages = [
        PageMargins(
            page=1, left=168, right=100, top=107, bottom=346, page_width=pw, page_height=ph
        ),
        PageMargins(
            page=2, left=70, right=70, top=67, bottom=37, page_width=pw, page_height=ph
        ),
    ]
    findings, _ = lr._check_margins(pages, cover + execution)
    assert findings == []


def test_classify_alignment_guards_and_fallbacks() -> None:
    block = _block([_line((72, 100, 300, 114))])
    assert _classify_block_alignment(block, 100.0, 100.0) is None
    assert _classify_block_alignment(_empty_line_block(), 72.0, 540.0) is None
    assert _classify_line_alignment((72, 100, 300, 114), 100.0, 100.0) is None
    assert (
        _classify_line_alignment((92, 100, 500, 114), 72.0, 540.0) == "left-aligned"
    )

    right_wrap = _block(
        [
            _line((200, 100, 540, 114)),
            _line((300, 118, 538, 132)),
            _line((250, 136, 540, 150)),
        ]
    )
    assert _classify_block_alignment(right_wrap, 72.0, 540.0) == "right-aligned"


def test_margin_check_compares_cover_pages_to_each_other() -> None:
    def titles(page: int, x0: float) -> list[_Block]:
        labels = [
            "THE COMPANIES ACT 1967",
            "PRIVATE COMPANY LIMITED BY SHARES",
            "CONSTITUTION OF",
            "ACME HOLDINGS PTE LTD",
        ]
        return [
            _block(
                [
                    _line(
                        (x0, 80 + i * 24, x0 + 180, 94 + i * 24),
                        text=label,
                        page=page,
                    )
                ],
                role="cover",
            )
            for i, label in enumerate(labels)
        ]

    pages = [
        PageMargins(
            page=1, left=120, right=72, top=80, bottom=80, page_width=612, page_height=792
        ),
        PageMargins(
            page=2, left=72, right=72, top=80, bottom=80, page_width=612, page_height=792
        ),
    ]
    findings, check = lr._check_margins(pages, titles(0, 200) + titles(1, 180))
    assert check.unable_to_assess is False
    assert any(a.page == 1 and a.evidence.get("side") == "left" for a in findings)


def test_expected_margins_use_facing_page_pattern() -> None:
    pages = [
        PageMargins(
            page=page,
            left=72 if page % 2 else 96,
            right=96 if page % 2 else 72,
            top=72,
            bottom=72,
            page_width=612,
            page_height=792,
        )
        for page in (1, 2, 3, 4)
    ]
    expected = lr._expected_margins(pages)
    assert expected[1]["left"] == expected[3]["left"]
    assert expected[2]["left"] == expected[4]["left"]
    assert expected[1]["left"] != expected[2]["left"]


def test_chrome_tie_is_assessed_without_a_false_shift() -> None:
    headers = [
        _block([_line((72, 18, 540, 32), text="HDR", page=0)], role="header"),
        _block([_line((72, 48, 540, 62), text="HDR", page=1)], role="header"),
    ]
    findings, check = lr._check_chrome_position(headers)
    assert check.unable_to_assess is False
    assert findings == []
