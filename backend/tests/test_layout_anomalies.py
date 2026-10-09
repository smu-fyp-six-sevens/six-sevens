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


def _cover_then_clause_pages_pdf(*, inset_last: bool = False, inner_pages: int = 2) -> bytes:
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
        for page_index in range(inner_pages):
            page = doc.new_page(width=612, height=792)
            inset = 50 if inset_last and page_index == inner_pages - 1 else 0
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
    assert result.unable_to_assess is True
    assert "one-page" in (result.unable_to_assess_reason or "")
    assert result.indentation.unable_to_assess is True
    assert result.alignment.unable_to_assess is True
    assert result.margin.unable_to_assess is True
    assert result.spacing.unable_to_assess is True


def _form_lines(*, shift_last: bool) -> list[_Block]:
    """One-page form: short labels, one of them tagged body, the rest headings."""
    blocks = []
    for index in range(5):
        x = 100.0 if shift_last and index == 4 else 72.0
        blocks.append(
            _block(
                [_line((x, 80 + index * 24, x + 180, 92 + index * 24), text=f"Field label {index} on this form")],
                role="body" if index == 0 else "heading",
            )
        )
    return blocks


def test_one_page_form_lines_can_show_an_indent() -> None:
    clean, clean_check = lr._check_indentation(_form_lines(shift_last=False))
    assert clean_check.unable_to_assess is False
    assert clean == []

    findings, check = lr._check_indentation(_form_lines(shift_last=True))
    assert check.unable_to_assess is False
    assert findings
    assert findings[0].evidence["left_delta_pt"] == 28.0


def _sentence(x0: float, y0: float, x1: float, text: str) -> _Line:
    return _line((x0, y0, x1, y0 + 12), text=text)


def test_a_line_that_slides_inside_its_block_is_named() -> None:
    block = _block([
        _sentence(466, 544, 541, "$1,091.60"),
        _sentence(78, 547, 475, "Total Amount Payable will be deducted from your account"),
        _sentence(54, 557, 73, "2026"),
    ])
    findings = lr._check_shifted_lines([block])
    assert findings
    assert "Total Amount Payable" in findings[0].text
    assert findings[0].evidence["left_delta_pt"] == 24.0


def test_one_line_past_a_stable_block_edge_is_named() -> None:
    lines = [
        _sentence(78, 195, 516, "If yes, please provide the tax identifying number for the entity"),
        _sentence(78, 219, 513, "If no, please complete the following table indicating tax residence"),
        _sentence(102, 242, 550, "require that the self-certification include a tax identifying number"),
        _sentence(78, 255, 192, "each reportable jurisdiction"),
    ]
    findings = lr._check_shifted_lines([_block(lines, role="table")])
    edges = [item for item in findings if item.evidence.get("kind") == "right-edge"]
    assert edges
    assert "self-certification" in edges[0].text
    assert edges[0].evidence["right_delta_pt"] > 20


def test_short_tokens_in_a_small_block_are_not_a_shifted_line() -> None:
    block = _block([
        _line((83, 350, 101, 362), text="4020"),
        _line((60, 356, 74, 366), text="kWh"),
    ])
    assert lr._check_shifted_lines([block]) == []


def test_first_line_indent_and_a_shared_indent_are_not_shifts() -> None:
    first_line = _block([
        _sentence(90, 100, 500, "The registered office of the Company shall be in Singapore today"),
        _sentence(72, 114, 500, "The registered office of the Company shall be in Singapore today"),
        _sentence(72, 128, 420, "and the rest of this clause continues at the same left edge"),
    ])
    shared = _block([
        _sentence(72, 200, 500, "If yes, please provide the tax identifying number for the entity"),
        _sentence(114, 214, 500, "I undertake to advise the bank within thirty days of any change"),
        _sentence(114, 228, 500, "I undertake to advise the bank within thirty days of any change"),
    ])
    assert lr._check_shifted_lines([first_line, shared]) == []


def test_a_last_line_shifted_right_is_named_from_the_line_above() -> None:
    text = "This is a computer-generated payslip and does not require a signature"
    blocks = [
        _block([_sentence(64, 348, 160, "Total deductions for this pay period are listed above")]),
        _block([_sentence(92, 432, 340, text)]),
    ]
    findings = lr._check_shifted_lines(blocks)
    assert findings
    assert "computer-generated" in findings[0].text
    assert findings[0].evidence["left_delta_pt"] == 28.0


def test_a_line_shifted_left_of_the_lines_around_it_is_named() -> None:
    text = "The company shall keep its registers at the registered office"
    blocks = [
        _block([_sentence(72, 100, 500, text)]),
        _block([_sentence(44, 128, 472, text)]),
        _block([_sentence(72, 156, 500, text)]),
    ]
    findings = lr._check_shifted_lines(blocks)
    shifted = [item for item in findings if item.evidence.get("left_delta_pt", 0) < 0]
    assert shifted
    assert shifted[0].evidence["left_delta_pt"] == -28.0


def test_a_line_moved_down_onto_the_next_line_is_named() -> None:
    kept = "The information in this report is collated from contributing members"
    moved = "Monthly instalment amounts for joint accounts are reported in full"
    blocks = [
        _block([_sentence(48, 640, 520, "Explanation of the score ranges from one thousand upward")]),
        _block([_sentence(48, 681, 500, kept)]),
        _block([_sentence(48, 682, 560, moved)]),
        _block([_sentence(48, 710, 540, "The bureau accepts no liability for the use of this report")]),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [item for item in findings if "joint accounts" in item.text]
    assert named
    assert named[0].evidence["kind"] == "vertical"
    assert named[0].evidence["vertical_delta_pt"] > 0


def test_a_line_moved_up_between_its_neighbours_is_named() -> None:
    text = "The borrower shall not permit the outstanding balance to exceed it"
    ys = (100, 114, 128, 142, 154, 190)
    blocks = [
        _block([_sentence(62, y, 520, "This facility is repayable on demand at the bank's sole discretion")])
        for y in ys[:4]
    ]
    blocks.append(_block([_sentence(62, ys[4], 520, text)]))
    blocks.append(
        _block([_sentence(62, ys[5], 520, "Interest rates change with movements in the bank prime rate")])
    )
    findings = lr._check_shifted_lines(blocks)
    named = [item for item in findings if "outstanding balance" in item.text]
    assert named
    assert named[0].evidence["kind"] == "vertical"
    assert named[0].evidence["vertical_delta_pt"] < 0


def test_a_paragraph_break_is_not_a_vertical_shift() -> None:
    blocks = [
        _block([_sentence(72, y, 520, f"Clause number {index} continues across the full content column")])
        for index, y in enumerate((100, 114, 128, 180, 194))
    ]
    assert lr._check_shifted_lines(blocks) == []


def test_a_sentence_split_onto_two_baselines_is_named() -> None:
    blocks = [
        _block([_sentence(52, 374, 332, "The company was incorporated under the Companies Act 1967, on and from")]),
        _block([_sentence(334, 392, 442, "18 Oct 2021, and is a Private")]),
        _block([_sentence(52, 405, 160, "company limited by shares today")]),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [item for item in findings if "incorporated under" in item.text]
    assert named
    assert named[0].evidence["kind"] == "vertical"
    assert named[0].evidence["vertical_delta_pt"] == -18.0


def test_a_left_slide_is_judged_against_a_similar_wide_line() -> None:
    text = "Singapore dollar deposits of non-bank depositors and monies and deposits"
    blocks = [
        _block([_sentence(35, 221, 137, "Deposit Insurance Scheme for Singapore dollar accounts")]),
        _block([_sentence(35, 242, 554, "Scheme are insured by the Singapore Deposit Insurance Corporation today")]),
        _block([_sentence(13, 248, 533, text)]),
        _block([_sentence(35, 252, 464, "Foreign currency deposits, dual currency investments, structured deposits")]),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [item for item in findings if "non-bank depositors" in item.text and item.evidence.get("left_delta_pt")]
    assert named
    assert named[0].evidence["left_delta_pt"] < -12


def test_a_line_dropped_into_the_gap_between_paragraphs_is_named() -> None:
    blocks = [
        _block([_sentence(63, 226, 505, "The information contained in this product is collated from lodgements")]),
        _block([_sentence(63, 238, 165, "government verified sources and records")]),
        _block([_sentence(63, 272, 516, "The list of officers for this entity is available for online authentication")]),
        _block([_sentence(63, 310, 306, "Assistant registrar of companies and business names office")]),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [item for item in findings if "list of officers" in item.text]
    assert named
    assert named[0].evidence["kind"] == "vertical"
    assert named[0].evidence["vertical_delta_pt"] > 12


def test_a_value_shifted_off_its_column_is_named() -> None:
    blocks = [
        _block([_sentence(330, 100, 470, "SINGAPORE CITIZEN")], role="cover"),
        _block([_sentence(330, 128, 400, "SINGAPORE")], role="cover"),
        _block([_sentence(330, 156, 390, "S7645675T")], role="cover"),
        _block(
            [
                _sentence(230, 184, 300, "06 JAN 2024"),
                _sentence(302, 184, 470, "MINISTRY OF HOME AFFAIRS"),
            ],
            role="cover",
        ),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [finding for finding in findings if "MINISTRY" in finding.text]
    assert named
    assert named[0].evidence["left_delta_pt"] < -12


def test_a_value_is_judged_against_the_column_beside_it() -> None:
    blocks = [
        _block([_sentence(302, 40, 360, "Country Code")], role="cover"),
        _block([_sentence(302, 58, 340, "SGP")], role="cover"),
        _block([_sentence(330, 400, 470, "SINGAPORE CITIZEN")], role="cover"),
        _block([_sentence(330, 428, 400, "SINGAPORE")], role="cover"),
        _block([_sentence(330, 456, 390, "S7645675T")], role="cover"),
        _block([_sentence(302, 484, 470, "MINISTRY OF HOME AFFAIRS")], role="cover"),
    ]
    findings = lr._check_shifted_lines(blocks)
    named = [finding for finding in findings if "MINISTRY" in finding.text]
    assert named
    assert named[0].evidence["expected_left_indent_pt"] == 330
    assert named[0].evidence["left_delta_pt"] < -12


def test_a_value_that_still_starts_with_its_column_is_not_a_shift() -> None:
    blocks = [
        _block([_sentence(330, y, 450, text)], role="cover")
        for y, text in (
            (100, "SINGAPORE CITIZEN"),
            (128, "SINGAPORE"),
            (156, "MINISTRY OF HOME AFFAIRS"),
            (184, "S7645675T"),
        )
    ]
    findings = lr._check_shifted_lines(blocks)
    assert not any("MINISTRY" in finding.text for finding in findings)


def test_a_hanging_indent_is_not_a_column_shift() -> None:
    blocks = [
        _block([_sentence(72, 100, 520, "The borrower shall maintain all security requested")]),
        _block([_sentence(90, 118, 520, "by the bank and shall execute every supplemental")]),
        _block([_sentence(90, 136, 520, "document the bank reasonably requires to perfect it")]),
        _block([_sentence(90, 154, 520, "and shall keep that security in full force")]),
    ]
    assert lr._check_shifted_lines(blocks) == []


def test_a_right_hand_amount_is_not_a_horizontal_shift() -> None:
    blocks = [
        _block([_sentence(72, 200, 400, "Basic salary for the employee during this pay period")]),
        _block([_line((490, 200, 540, 212), text="6,960.00")]),
        _block([_sentence(72, 224, 400, "Allowances for the employee during this pay period")]),
    ]
    assert lr._check_shifted_lines(blocks) == []


# ---------------------------------------------------------------------------
# public API — detections (AC1–AC4)
# ---------------------------------------------------------------------------


def test_consistent_document_has_no_indent_or_alignment_anomalies() -> None:
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    assert result.unable_to_assess is False
    assert not any(a.anomaly_type == "Indentation" for a in result.anomalies)
    assert not any(a.anomaly_type == "Alignment" for a in result.anomalies)


def _statement_columns(*, shift_one: bool = False) -> list[_Block]:
    blocks: list[_Block] = []
    y = 120.0
    for i in range(6):
        blocks.append(
            _block([
                _line(
                    (40, y, 150, y + 11),
                    text=f"01/06/2026 10:0{i}:00 AM",
                )
            ])
        )
        x = 258.0 if shift_one and i == 3 else 230.0
        blocks.append(
            _block([
                _line(
                    (x, y, x + 340, y + 11),
                    text=f"GIRO Payment PREMISES RENTAL {i} 0.00 5147.30",
                )
            ])
        )
        y += 28
    return blocks


def test_repeated_columns_are_not_indentation_anomalies() -> None:
    findings, _check = lr._check_indentation(_statement_columns())
    assert findings == []


def test_a_line_off_every_column_is_still_an_indent() -> None:
    findings, _check = lr._check_indentation(_statement_columns(shift_one=True))
    assert findings
    assert any("RENTAL 3" in finding.text for finding in findings)


def _protruding_paragraph_pdf() -> bytes:
    def build(doc):
        for page_index in range(3):
            page = doc.new_page(width=612, height=792)
            y = 90
            for block_index in range(3):
                protrude = page_index == 2 and block_index == 1
                right = 590 if protrude else 500
                text = (
                    BODY + " Extra words sit further along this same line."
                    if protrude
                    else BODY
                )
                _textbox(page, pymupdf.Rect(72, y, right, y + 70), text, align=0)
                y += 80

    return _pdf_bytes(build)


def test_paragraph_past_the_usual_right_edge_is_named() -> None:
    result = check_layout_anomalies("doc-1", _protruding_paragraph_pdf())
    edges = [a for a in result.anomalies if "right-edge" in _kinds(a)]
    assert len(edges) == 1
    assert edges[0].page == 3


def test_a_wide_address_does_not_set_the_body_right_edge() -> None:
    blocks: list[_Block] = []
    for page in range(3):
        body_right = 590 if page == 2 else 500
        for index in range(3):
            right = body_right if index == 1 else 500
            blocks.append(
                _block(
                    [
                        _line(
                            (72, 90 + index * 30, right, 112 + index * 30),
                            text=f"Body paragraph {page}-{index} fills the usual column today",
                            page=page,
                        )
                    ]
                )
            )
        blocks.append(
            _block(
                [
                    _line(
                        (72, 400, 580, 422),
                        text="42 Orchard Road #01-02 Singapore 238801",
                        page=page,
                    )
                ],
                role="address",
            )
        )
    findings = lr._check_protruding_right(blocks)
    assert len(findings) == 1
    assert findings[0].page == 3
    assert "Orchard" not in findings[0].text
    assert "body" in (findings[0].baseline or "")


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
    # Two inner pages have no majority margin, so neither can be called the
    # odd one out. Three give a pattern for the inset last page to break.
    result = check_layout_anomalies(
        "doc-1", _cover_then_clause_pages_pdf(inset_last=True, inner_pages=3)
    )
    margins = [a for a in result.anomalies if a.anomaly_type == "Margin"]
    assert any(a.page == 4 for a in margins)
    assert all(a.page not in (1, 2, 3) for a in margins)


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


def test_structural_role_refines_body_before_comparison() -> None:
    heading = _block([_line((72, 80, 280, 94), text="2. REGISTERED OFFICE")], role="heading")
    body = _block([_sentence(72, 110, 540, "The company is registered and operates from Singapore.")])
    listed = _block([_sentence(108, 140, 540, "(a) NRIC of the borrower and each guarantor")])
    label = _block([_line((72, 170, 280, 184), text="Name: John Tan")])
    address = _block([_line((72, 200, 420, 214), text="42 Orchard Road #01-02 Singapore 238801")])
    dated = _block([_line((72, 230, 180, 244), text="06 Jan 2024")])
    header = _block([_line((200, 20, 400, 34), text="GOLDEN HARVEST TRADING PTE LTD")], role="header")

    assert lr.classify_structural_role(heading) == "heading"
    assert lr.classify_structural_role(header) == "header"
    assert lr.classify_structural_role(body) == "body"
    assert lr.classify_structural_role(listed) == "list_item"
    assert lr.classify_structural_role(label) == "label_value"
    assert lr.classify_structural_role(address) == "address"
    assert lr.classify_structural_role(dated) == "date"

    blocks = [heading, body, listed, label, address, dated, header]
    lr._assign_structural_roles(blocks)
    for block in blocks:
        block.alignment = "left-aligned"
    heading.alignment = "centred"
    groups = lr.build_comparable_groups(blocks)
    by_role = {group.role: group for group in groups}
    assert set(by_role) == {
        "heading",
        "body",
        "list_item",
        "label_value",
        "address",
        "date",
        "header",
    }
    assert by_role["heading"].alignment == "centred"
    assert by_role["body"].alignment == "left-aligned"
    assert by_role["body"].page_region == "body"
    assert all("Name:" not in block.text for block in by_role["body"].members)
    assert "(a) NRIC" in by_role["list_item"].members[0].text


def _list_and_body(*, shift_last: bool) -> list[_Block]:
    """Full-width list under left-aligned clauses. The list indent is shared."""
    intro = _block(
        [_sentence(72, 90, 540, "The Borrower shall provide the following documents today")]
    )
    labels = (
        "(a) NRIC of the borrower and each guarantor on this loan",
        "(b) Proof of address for the borrower and each guarantor",
        "(c) Bank statement for the last three months of trading",
    )
    items = []
    for index, label in enumerate(labels):
        left = 150.0 if shift_last and index == 2 else 108.0
        items.append(_block([_sentence(left, 120 + index * 20, 540, label)]))
    closing = _block(
        [_sentence(72, 200, 540, "The company is registered and operates in Singapore today")]
    )
    return [intro, *items, closing]


def test_consistent_list_indent_is_not_an_anomaly() -> None:
    blocks = _list_and_body(shift_last=False)
    lr._assign_structural_roles(blocks)
    assert {block.role for block in blocks[1:4]} == {"list_item"}
    findings, check = lr._check_indentation(blocks)
    assert check.unable_to_assess is False
    assert findings == []


def test_a_list_item_off_the_list_indent_is_an_anomaly() -> None:
    blocks = _list_and_body(shift_last=True)
    lr._assign_structural_roles(blocks)
    findings, check = lr._check_indentation(blocks)
    assert check.unable_to_assess is False
    assert findings
    assert any("(c) Bank statement" in finding.text for finding in findings)
    assert all("(a) NRIC" not in finding.text for finding in findings)
    assert all("(b) Proof" not in finding.text for finding in findings)


def test_list_structure_retires_an_indent_signal() -> None:
    """An indent signal on a consistent list is the list, not a tamper."""
    blocks = _list_and_body(shift_last=False)
    openings = lr._consistent_list_openings(blocks)
    assert {(page, text[:3]) for page, text in openings} == {(1, "(a)"), (1, "(b)"), (1, "(c)")}

    sample = next(block for block in blocks if block.text.startswith("(a)"))
    finding = lr._anomaly(
        "Indentation",
        sample,
        detected="108 pt",
        expected="72 pt",
        baseline=None,
        deviation="+36 pt",
        confidence=0.8,
        evidence={"kind": "indentation", "side": "left", "left_delta_pt": 36.0},
    )
    signal = lr._layout_signal(finding)
    assert signal.type == "indentation"
    assert signal.strength == 0.8
    assert signal.deviation == 36.0
    assert lr._decide_from_signals([signal], blocks) == []

    shifted = _list_and_body(shift_last=True)
    outlier = next(block for block in shifted if block.text.startswith("(c)"))
    outlier_finding = lr._anomaly(
        "Indentation",
        outlier,
        detected="150 pt",
        expected="108 pt",
        baseline=None,
        deviation="+42 pt",
        confidence=0.84,
        evidence={"kind": "indentation", "side": "left", "left_delta_pt": 42.0},
    )
    kept = lr._decide_from_signals([lr._layout_signal(outlier_finding)], shifted)
    assert kept == [outlier_finding]


def _clause_paragraphs(*, shift_clause: int | None = None, shift_index: int | None = None) -> list[_Block]:
    """Three clauses. Body under a clause is only compared with that clause."""
    blocks: list[_Block] = []
    y = 80.0
    for clause in (1, 2, 3):
        blocks.append(
            _block(
                [_line((72, y, 280, y + 14), text=f"{clause}. TERMS OF THIS CLAUSE")],
                role="heading",
            )
        )
        y += 22
        for index in range(3):
            left = 72.0
            if clause == shift_clause and index == shift_index:
                left = 108.0
            elif shift_clause == clause and shift_index is None:
                left = 108.0
            blocks.append(
                _block(
                    [
                        _sentence(
                            left,
                            y,
                            540,
                            f"Clause {clause} paragraph {index} runs across this content column",
                        )
                    ]
                )
            )
            y += 24
        y += 12
    return blocks


def test_a_clause_keeps_its_own_indent_baseline() -> None:
    blocks = _clause_paragraphs(shift_clause=3)
    findings, check = lr._check_indentation(blocks)
    assert check.unable_to_assess is False
    assert findings == []
    groups = [group for group in lr.build_comparable_groups(blocks) if group.role == "body"]
    assert len(groups) == 3
    assert {group.section for group in groups} == {0, 1, 2}


def test_a_paragraph_is_judged_against_its_own_clause() -> None:
    blocks = _clause_paragraphs(shift_clause=3, shift_index=1)
    findings, check = lr._check_indentation(blocks)
    assert check.unable_to_assess is False
    named = [finding for finding in findings if "paragraph 1" in finding.text]
    assert named
    assert all("Clause 1" not in finding.text and "Clause 2" not in finding.text for finding in findings)
    assert named[0].evidence["left_delta_pt"] == 36.0


def test_three_neighbours_with_one_indent_are_a_structural_pattern() -> None:
    blocks = [
        _block(
            [_sentence(108, 100 + index * 18, 420, f"({letter}) Item {letter} of this repeated list")],
            role="list_item",
        )
        for index, letter in enumerate("abc")
    ]
    odd = _block(
        [_sentence(150, 160, 420, "(d) Item d sits off the list indent")],
        role="list_item",
    )
    patterns = lr.repeated_structural_patterns(blocks + [odd])
    assert len(patterns) == 1
    assert patterns[0].role == "list_item"
    assert patterns[0].indentation == pytest.approx(108.0, abs=1.0)
    assert len(patterns[0].members) == 3
    assert all(lr._matches_structural_pattern(block, patterns) for block in blocks)
    assert lr._matches_structural_pattern(odd, patterns) is False

    sample = blocks[0]
    finding = lr._anomaly(
        "Indentation",
        sample,
        detected="108 pt",
        expected="72 pt",
        baseline=None,
        deviation="+36 pt",
        confidence=0.8,
        evidence={"kind": "indentation", "side": "left", "left_delta_pt": 36.0},
    )
    assert lr._decide_from_signals([lr._layout_signal(finding)], blocks + [odd]) == []


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


def test_row_alignment_flags_a_heading_that_left_the_line_beside_it() -> None:
    date = _block(
        [_line((490, 971, 621, 995), text="24 JUN 2025")],
        role="heading",
    )
    ministry = _block(
        [_line((689, 950, 994, 979), text="MINISTRY OF HOME AFFAIRS")],
        role="heading",
    )
    findings = lr._check_row_alignment([date, ministry])
    assert findings
    assert any("MINISTRY OF HOME AFFAIRS" in a.text for a in findings)
    assert any(abs(a.evidence["top_delta_pt"]) >= 12 for a in findings)
    assert any(abs(a.evidence["baseline_delta_pt"]) >= 12 for a in findings)


def test_row_alignment_flags_a_heading_beside_a_body_line() -> None:
    label = _block([_line((72, 200, 180, 214), text="Date of issue")])
    value = _block(
        [_line((320, 192, 520, 206), text="MINISTRY OF HOME AFFAIRS")],
        role="heading",
    )
    findings = lr._check_row_alignment([label, value])
    assert findings
    assert any("MINISTRY OF HOME AFFAIRS" in a.text for a in findings)


def test_row_alignment_ignores_a_heading_whose_baseline_still_meets() -> None:
    """A wordmark can sit a few pt off at the top and still share a baseline."""
    left = _block([_line((40, 40, 160, 62), text="PASSPORT")], role="heading")
    right = _block(
        [_line((180, 47, 420, 61), text="REPUBLIC OF SINGAPORE")],
        role="heading",
    )
    assert lr._check_row_alignment([left, right]) == []


def test_row_alignment_ignores_a_heading_inside_a_tall_paragraph() -> None:
    paragraph = _block(
        [
            _line((72, 266, 340, 280), text="2,678.14 12 JUN UTILITY BILL PAYMENT"),
            _line((72, 520, 340, 543), text="closing balance for the period"),
        ]
    )
    amount = _block(
        [_line((365, 521, 544, 532), text="209.53 14,723.64")],
        role="heading",
    )
    assert lr._check_row_alignment([paragraph, amount]) == []


def test_row_alignment_ignores_an_aligned_heading() -> None:
    date = _block(
        [_line((490, 971, 621, 995), text="24 JUN 2025")],
        role="heading",
    )
    ministry = _block(
        [_line((689, 971, 994, 995), text="MINISTRY OF HOME AFFAIRS")],
        role="heading",
    )
    assert lr._check_row_alignment([date, ministry]) == []


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


def test_paragraph_spacing_measures_a_wide_gap_but_needs_corroboration() -> None:
    """The check measures the gap; on its own a gap describes the template."""
    pdf = _wide_paragraph_gap_pdf()
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    blocks = lr._extract_blocks(doc)
    lr._assign_structural_roles(blocks)
    findings, check = lr._check_paragraph_spacing(blocks, lr.build_comparable_groups(blocks))
    assert check.unable_to_assess is False
    assert any(abs(a.evidence["gap_delta_pt"]) >= 8 for a in findings)

    result = check_layout_anomalies("doc-1", pdf)
    assert result.spacing.unable_to_assess is False
    assert not any(_kinds(a) == {"paragraph-spacing"} for a in result.anomalies)


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

    body = finding.model_dump()
    assert set(body) == {"type", "page", "where", "text", "summary", "confidence", "checks"}
    assert body["type"] == "Indentation"
    assert [check["check"] for check in body["checks"]] == [
        "indentation",
        "right edge",
        "paragraph spacing",
    ]
    assert body["checks"][0]["difference"] == "+24.0 pt"
    assert "evidence" not in body
    assert "detected" not in body


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


def test_paragraph_spacing_ignores_section_break_gaps() -> None:
    blocks = [
        _block([_line((72, 90, 540, 130), text=BODY)]),
        _block([_line((72, 140, 540, 180), text=BODY)]),
        _block([_line((72, 190, 540, 230), text=BODY)]),
        _block([_line((72, 430, 540, 470), text=BODY)]),
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
        lr.alignment, "_nearest_row_pairs", lambda _blocks: [(label, amount), (label, amount)]
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


def test_split_leading_headings_leaves_too_short_a_remainder_unsplit() -> None:
    heading_line = _line((72, 100, 300, 112), text="2. NAME")
    short_rest_line = _line((72, 114, 120, 126), text="Ok.")
    block = _block([heading_line, short_rest_line])
    assert lr._split_leading_headings([block]) == [block]


def test_is_clause_heading_text_rejects_blank_text() -> None:
    assert lr._is_clause_heading_text("   ") is False


def test_has_strong_majority_needs_at_least_two_comparable_peers() -> None:
    assert lr._has_strong_majority(matching=1, comparable=1) is False


def test_check_indentation_skips_a_shift_that_does_not_reach_the_column_right() -> None:
    baseline = [
        _block([_line((72, 100 + i * 30, 540, 112 + i * 30))]) for i in range(3)
    ]
    shifted_but_short = _block([_line((92, 190, 520, 202))])
    anomalies, check = lr._check_indentation([*baseline, shifted_but_short])
    assert check.unable_to_assess is False
    assert anomalies == []


def test_delta_confidence_stays_low_just_past_the_threshold() -> None:
    barely = lr._delta_confidence(8.2, 8.0, 1.0)
    clear = lr._delta_confidence(24.0, 12.0, 1.0)
    assert barely < lr._SIGNAL_STRENGTH_FLOOR
    assert clear >= lr._SIGNAL_STRENGTH_FLOOR


def test_decide_drops_a_weak_signal_and_a_detector_that_fires_everywhere() -> None:
    host = _block([_line((72, 100, 540, 140), text=BODY)])
    weak = lr._anomaly(
        "Spacing",
        host,
        detected="16.2 pt",
        expected="8 pt",
        baseline=None,
        deviation="+8.2 pt",
        confidence=lr._delta_confidence(8.2, 8.0, 1.0),
        evidence={"kind": "paragraph-spacing", "gap_delta_pt": 8.2},
    )
    assert lr._decide_from_signals([lr._layout_signal(weak)], [host]) == []

    blocks = [
        _block([_line((72, 40 + i * 36, 540, 70 + i * 36), text=f"{BODY} {i}")])
        for i in range(6)
    ]
    signals = [
        lr._layout_signal(
            lr._anomaly(
                "Spacing",
                block,
                detected="30 pt",
                expected="12 pt",
                baseline=None,
                deviation="+18 pt",
                confidence=0.9,
                evidence={"kind": "paragraph-spacing", "gap_delta_pt": 18.0},
            )
        )
        for block in blocks
    ]
    assert lr._decide_from_signals(signals, blocks) == []


def test_decide_keeps_the_strongest_regions() -> None:
    blocks = [
        _block([_line((72, 30 + i * 24, 540, 48 + i * 24), text=f"{BODY} {i}")])
        for i in range(30)
    ]
    signals = []
    for index in range(12):
        score = round(0.95 - index * 0.04, 2)
        signals.append(
            lr._layout_signal(
                lr._anomaly(
                    "Spacing",
                    blocks[index],
                    detected="30 pt",
                    expected="12 pt",
                    baseline=None,
                    deviation="+18 pt",
                    confidence=score,
                    evidence={"kind": "paragraph-spacing", "gap_delta_pt": 18.0},
                )
            )
        )
    kept = lr._decide_from_signals(signals, blocks)
    assert len(kept) == lr._MAX_FINDINGS_PER_DOCUMENT
    assert min(item.confidence for item in kept) == pytest.approx(0.59)


def test_line_box_and_paragraph_box_are_one_finding() -> None:
    paragraph = lr._anomaly(
        "Indentation",
        _block([_line((72, 200, 540, 280), text=BODY)]),
        detected="96 pt",
        expected="72 pt",
        baseline=None,
        deviation="+24 pt",
        confidence=0.8,
        evidence={"kind": "indentation", "left_delta_pt": 24.0},
    )
    line = LayoutAnomaly(
        anomaly_type="Indentation",
        page=1,
        location="Line near y=210",
        bbox=(96.0, 210.0, 520.0, 224.0),
        text="Moved.",
        detected="96 pt",
        expected="72 pt",
        deviation="+24 pt",
        confidence=0.7,
        evidence={"kind": "right-edge", "right_delta_pt": -20.0},
        summary="short",
    )
    merged = lr._coalesce_signals([paragraph, line])
    assert len(merged) == 1
    assert {item["kind"] for item in merged[0].evidence["signals"]} == {
        "indentation",
        "right-edge",
    }
    assert merged[0].bbox[1] == pytest.approx(200.0)


def test_paragraph_spacing_attributes_a_moved_block_once() -> None:
    height = 30.0
    rhythm = 16.0
    shift = 14.0
    y = 80.0
    blocks = []
    for index in range(5):
        y0 = y + (shift if index == 2 else 0.0)
        text = f"Clause {index + 1} of the Company shall be at such place in Singapore."
        blocks.append(_block([_line((72, y0, 540, y0 + height), text=text)]))
        y += height + rhythm
    findings, check = lr._check_paragraph_spacing(blocks)
    assert check.unable_to_assess is False
    assert len(findings) == 1
    assert "Clause 3" in findings[0].text
    assert findings[0].evidence["space"] == "before"


def test_short_page_extent_is_not_a_margin_finding() -> None:
    pages = [
        PageMargins(
            page=page,
            left=72,
            right=72 if page < 3 else 200,
            top=72,
            bottom=72 if page < 3 else 420,
            page_width=612,
            page_height=792,
        )
        for page in (1, 2, 3)
    ]
    blocks = [
        _block([_line((72, 72, 540, 400), text=BODY, page=0)]),
        _block([_line((72, 72, 540, 400), text=BODY, page=1)]),
        _block([_line((72, 72, 220, 100), text=BODY, page=2)]),
    ]
    findings, check = lr._check_margins(pages, blocks)
    assert check.unable_to_assess is False
    assert findings == []


def test_top_and_bottom_are_not_margin_findings() -> None:
    pages = [
        PageMargins(
            page=1, left=72, right=72, top=72, bottom=72, page_width=612, page_height=792
        ),
        PageMargins(
            page=2, left=72, right=72, top=130, bottom=360, page_width=612, page_height=792
        ),
    ]
    blocks = [
        _block([_line((72, 72, 540, 400), text=BODY, page=0)]),
        _block([_line((72, 130, 540, 400), text=BODY, page=1)]),
    ]
    findings, check = lr._check_margins(pages, blocks)
    assert check.unable_to_assess is False
    assert findings == []


def test_row_alignment_ignores_mixed_sizes_on_one_baseline() -> None:
    label = _block(
        [_line((72, 180, 280, 210), text="Chargeable Income", baseline=200.0, size=12)]
    )
    amount = _block(
        [_line((460, 191, 540, 203), text="316,800", baseline=200.0, size=9)]
    )
    assert lr._check_row_alignment([label, amount]) == []

    shifted = _block(
        [_line((460, 203, 540, 215), text="316,800", baseline=212.0, size=9)]
    )
    findings = lr._check_row_alignment([label, shifted])
    assert findings
    assert findings[0].evidence["baseline_delta_pt"] == pytest.approx(12.0)


def test_consecutive_neighbours_do_not_raise_confidence() -> None:
    first = _finding(confidence=0.62, bbox=(72.0, 100.0, 540.0, 160.0))
    second = _finding(confidence=0.62, bbox=(72.0, 180.0, 540.0, 240.0))
    lr._mark_local_consecutive([first, second])
    assert first.confidence == 0.62
    assert second.confidence == 0.62
    assert first.evidence.get("local_pattern") is True
    assert first.evidence.get("consecutive_run") == 2


def _form_column(shifted_left: float) -> list[_Block]:
    """Payslip-style labels stacked on one left edge, amounts on the right."""
    labels = ["Base pay", "Variable pay", "Benefits", "Tax withholding", "Other deductions"]
    blocks = []
    for index, label in enumerate(labels):
        left = shifted_left if label == "Other deductions" else 62.7
        y0 = 194.0 + index * 20
        blocks.append(_block([_line((left, y0, left + 6 * len(label), y0 + 12), text=label, size=9.5)]))
        blocks.append(
            _block([_line((475.6, y0, 532.6, y0 + 12), text=f"SGD {index + 1}00.00", size=9.5)])
        )
    return blocks


def test_unanchored_line_off_its_column_is_named() -> None:
    findings = lr._check_unanchored_lines(_form_column(86.7))
    assert [finding.text for finding in findings] == ["Other deductions"]
    evidence = findings[0].evidence
    assert evidence["unanchored"] is True
    assert evidence["left_delta_pt"] == pytest.approx(24.0, abs=0.1)
    assert "start at 62.7 pt" in (findings[0].baseline or "")


def test_unanchored_check_leaves_an_intact_column_alone() -> None:
    assert lr._check_unanchored_lines(_form_column(62.7)) == []


def test_centred_title_is_anchored_to_the_page_centre() -> None:
    title = _line((236.0, 60.0, 376.0, 74.0), text="STATEMENT OF ACCOUNT")
    body = [_line((72.0, 90.0 + i * 14, 300.0, 102.0 + i * 14)) for i in range(3)]
    assert lr._shares_an_edge(title, body) is True


def test_text_centred_in_a_drawn_cell_is_anchored() -> None:
    header = _line((144.0, 310.5, 208.7, 319.5), text="PRODUCT NAME")
    rules = [(82.8, 300.0, 330.0), (270.0, 300.0, 330.0)]
    assert lr._shares_an_edge(header, [], rules) is True
    assert lr._shares_an_edge(header, []) is False


def test_cell_nudged_off_its_row_is_named() -> None:
    blocks = []
    for index in range(3):
        y0 = 200.0 + index * 20
        shift = 10.0 if index == 1 else 0.0
        blocks.append(
            _block([_line((50.0, y0 + shift, 120.0, y0 + 12 + shift), text=f"Item {index}", baseline=y0 + 10 + shift)])
        )
        blocks.append(_block([_line((300.0, y0, 360.0, y0 + 12), text=f"{index}0.00", baseline=y0 + 10)]))
        blocks.append(_block([_line((420.0, y0, 480.0, y0 + 12), text=f"{index}1.00", baseline=y0 + 10)]))
    findings = lr._check_vertical_unanchored_lines(blocks)
    assert [finding.text for finding in findings] == ["Item 1"]
    assert findings[0].evidence["vertical_delta_pt"] == pytest.approx(10.0)


def test_a_section_break_is_not_a_spacing_anomaly() -> None:
    block = _block([_sentence(72, 300, 540, "ASSETS Non-current assets Property and equipment")])
    finding = lr._anomaly(
        "Spacing",
        block,
        detected="250 pt",
        expected="12 pt",
        baseline=None,
        deviation="+237.5 pt",
        confidence=0.9,
        evidence={"kind": "paragraph-spacing", "space": "before", "gap_delta_pt": 237.5},
    )
    assert lr._decide_from_signals([lr._layout_signal(finding)], [block]) == []


def test_larger_company_name_off_its_address_column_is_named() -> None:
    """A heading set larger than the address under it still belongs to that column."""
    name = _line((63.6, 74.9, 235.3, 89.4), text="Silver Oak Logistics Pte Ltd", size=13.0)
    street = _line((39.6, 91.8, 138.9, 104.0), text="6 Battery Road, #21-08", size=9.6)
    city = _line((39.6, 104.8, 118.1, 117.0), text="Singapore 049909", size=9.6)
    findings = lr._check_unanchored_lines([_block([name]), _block([street]), _block([city])])
    assert [finding.text for finding in findings] == ["Silver Oak Logistics Pte Ltd"]
    assert findings[0].evidence["left_delta_pt"] == pytest.approx(24.0, abs=0.1)


def _id_card(country_left: float) -> list[_Block]:
    """NRIC-style card: each short label sits flush over its value."""
    pairs = [
        (188.0, 199.0, "Race", "CHINESE"),
        (188.0, 247.0, "Date of birth", "08-12-1980"),
        (388.0, 247.0, "Sex", "F"),
        (188.0, 295.0, "Country/Place of birth", "SINGAPORE"),
    ]
    blocks = []
    for left, top, label, value in pairs:
        label_left = country_left if label.startswith("Country") else left
        blocks.append(
            _block([_line((label_left, top, label_left + 5 * len(label), top + 10), text=label, size=7.5)])
        )
        blocks.append(
            _block([_line((left, top + 12, left + 7 * len(value), top + 26), text=value, size=11.5)])
        )
    return blocks


def test_label_off_the_pages_label_value_offset_is_named() -> None:
    findings = lr._check_pair_offsets(_id_card(country_left=212.0))
    assert [finding.text for finding in findings] == ["Country/Place of birth"]
    assert findings[0].evidence["left_delta_pt"] == pytest.approx(24.0)
    assert "offset by 0 pt" in (findings[0].baseline or "")


def test_flush_label_value_card_has_no_pair_finding() -> None:
    assert lr._check_pair_offsets(_id_card(country_left=188.0)) == []


def test_repeated_heading_indent_is_not_a_pair_finding() -> None:
    """Items indented under their headings share one offset, so it is the design."""
    blocks = []
    for index, (heading, item) in enumerate(
        [("Current assets", "Inventories"), ("Current liabilities", "Trade payables"), ("Equity", "Share capital")]
    ):
        top = 100.0 + index * 40
        blocks.append(_block([_line((71.2, top, 160.0, top + 12), text=heading)]))
        blocks.append(_block([_line((81.2, top + 13, 170.0, top + 25), text=item)]))
    assert lr._check_pair_offsets(blocks) == []


def test_right_aligned_amounts_are_not_a_pair_finding() -> None:
    blocks = [
        _block([_line((480.0, 100, 540.0, 112), text="56,889.15")]),
        _block([_line((494.0, 113, 540.0, 125), text="9,102.40")]),
        _block([_line((100.0, 200, 160.0, 212), text="Name")]),
        _block([_line((100.0, 213, 190.0, 225), text="Tan Wei Ming")]),
        _block([_line((100.0, 260, 160.0, 272), text="Account")]),
        _block([_line((100.0, 273, 190.0, 285), text="123-456-789")]),
    ]
    assert lr._check_pair_offsets(blocks) == []


def test_one_line_alignment_needs_corroboration_but_a_paragraph_does_not() -> None:
    one_line = _block([_sentence(300, 100, 540, "Date Opened / Closed")])
    paragraph = _block(
        [_sentence(150, 200, 460, "This clause has been digitally inserted"), _sentence(170, 214, 440, "after the document was signed")]
    )

    def alignment(block: _Block) -> LayoutAnomaly:
        return lr._anomaly(
            "Alignment",
            block,
            detected="Centre-aligned",
            expected="Left-aligned",
            baseline=None,
            deviation="1 paragraph",
            confidence=0.9,
            evidence={"alignment": "centred", "expected_alignment": "left-aligned"},
        )

    blocks = [one_line, paragraph]
    assert lr._corroborated(alignment(one_line), blocks) is False
    assert lr._corroborated(alignment(paragraph), blocks) is True


def test_alignment_is_not_reported_on_table_cells() -> None:
    cell = _block([_line((460, 200, 540, 214), text="316,800.00")], role="table")
    assert not lr.alignment._reportable_alignment(cell, matching=9, comparable=10)


def test_alignment_needs_three_quarters_of_peers_to_agree() -> None:
    paragraph = _block([_line((72, 200, 540, 214))])
    assert lr.alignment._reportable_alignment(paragraph, matching=3, comparable=4)
    assert not lr.alignment._reportable_alignment(paragraph, matching=5, comparable=8)


def test_row_alignment_highlights_both_members_of_the_pair() -> None:
    # A moved label is as likely as a moved value, so the box covers both.
    label = _block([_line((72, 212, 280, 226), text="Chargeable Income")])
    amount = _block([_line((460, 200, 540, 214), text="316,800")])
    findings = lr._check_row_alignment([label, amount])
    assert len(findings) == 1
    assert findings[0].bbox == (72, 200, 540, 226)


def _kind_finding(kind: str, bbox, confidence: float, **kwargs) -> LayoutAnomaly:
    return _finding(bbox=bbox, confidence=confidence, evidence={"kind": kind}, **kwargs)


def test_weaker_finding_beside_a_stronger_one_is_folded_in() -> None:
    strong = _kind_finding("indentation", (72, 100, 300, 114), 0.9)
    beside = _kind_finding("vertical", (72, 120, 300, 134), 0.6)
    merged = lr._merge_neighbours([beside, strong])
    assert merged == [strong]
    assert strong.confidence == 0.9
    assert strong.bbox == (72, 100, 300, 134)
    assert {s["kind"] for s in strong.evidence["signals"]} == {"indentation", "vertical"}


def test_findings_far_apart_stay_separate() -> None:
    first = _kind_finding("indentation", (72, 100, 300, 114), 0.9)
    far = _kind_finding("vertical", (72, 300, 300, 314), 0.6)
    assert lr._merge_neighbours([first, far]) == [first, far]


def test_neighbours_do_not_chain_down_a_table() -> None:
    rows = [_kind_finding("vertical", (72, 100 + 20 * i, 300, 114 + 20 * i), 0.9 - 0.1 * i) for i in range(4)]
    merged = lr._merge_neighbours(list(rows))
    # The strongest row absorbs only the row next to it; the third starts its own finding.
    assert len(merged) == 2


def test_margin_and_overlay_findings_are_never_folded() -> None:
    strong = _kind_finding("indentation", (72, 100, 300, 114), 0.9)
    margin = _kind_finding("left-margin", (72, 100, 300, 114), 0.5, anomaly_type="Margin")
    overlay = _kind_finding("text-overlay", (80, 102, 120, 112), 0.5, anomaly_type="Text Overlay")
    assert len(lr._merge_neighbours([strong, margin, overlay])) == 3


def test_a_long_uppercase_table_is_not_a_numbered_heading() -> None:
    rows = [
        _line((51, 600 + 12 * i, 560, 611 + 12 * i), text=f"{26 + i} JUN ATM WITHDRAWAL 90.00 6,919.61")
        for i in range(4)
    ]
    assert lr._is_heading_block(_block(rows)) is False
    assert lr._is_heading_block(_block([_line((72, 100, 280, 114), text="2. REGISTERED OFFICE")])) is True


def _wrapped_cell(x: float, row_baseline: float, texts: list[str], pitch: float = 9.2) -> _Block:
    """A table cell wrapped over several lines, centred on its row."""
    first = row_baseline - pitch * (len(texts) - 1) / 2
    lines = [
        _line((x, first + i * pitch - 7, x + 120, first + i * pitch + 1), text=text, baseline=first + i * pitch, size=7.2)
        for i, text in enumerate(texts)
    ]
    return _block(lines)


def test_lines_of_wrapped_cells_at_the_page_spacing_are_not_off_their_row() -> None:
    blocks = []
    for row in range(4):
        base = 200 + 60 * row
        blocks.append(_block([_line((40, base - 7, 90, base + 1), text=f"0{row + 1}/06/2026", baseline=base, size=7.5)]))
        blocks.append(_block([_line((360, base - 7, 400, base + 1), text=f"{row + 1},205.50", baseline=base, size=7.5)]))
        blocks.append(_wrapped_cell(230, base, ["OTHER ASIA LINK", "COMMODITIES PTE LTD", f"SG3P47569785280{row}"]))
    assert lr._check_vertical_unanchored_lines(blocks) == []


def test_a_nudged_line_breaks_its_cells_spacing_and_is_still_flagged() -> None:
    lines = [_line((230, 190, 350, 198), text="OTHER ASIA LINK", baseline=197, size=7.2)]
    cells = [_wrapped_cell(230, 300 + 60 * row, ["FUNDS TRANSFER", "REF 0000"]) for row in range(3)]
    in_rhythm = lr.anchors._cell_rhythm(
        [(cell, line) for cell in cells for line in cell.lines]
        + [(_block(lines + [_line((230, 214, 350, 222), text="SG3P475697852806", baseline=221, size=7.2)]), line) for line in lines]
    )
    assert all(id(line) in in_rhythm for cell in cells for line in cell.lines)
    assert not any(id(line) in in_rhythm for line in lines)


def _statement_pages(issuer_x_by_page: list[float], *, band_y: float = 70.0) -> list[_Block]:
    """Pages with a running title, an issuer line under it, and transaction rows."""
    blocks = []
    for page, issuer_x in enumerate(issuer_x_by_page):
        blocks.append(_block([_line((40, 20, 300, 32), text="ACCOUNT STATEMENT", page=page)], role="header"))
        blocks.append(_block([_line((issuer_x, band_y, issuer_x + 180, band_y + 10), text="Oversea-Chinese Banking Corp", page=page)], role="header"))
        for row in range(3):
            y = 300 + 40 * row + 10 * page
            blocks.append(_block([_line((40, y, 200, y + 10), text="GIRO PAYMENT", page=page)]))
    return blocks


def test_issuer_line_moved_on_one_page_is_flagged_against_the_others() -> None:
    findings = lr._check_moved_furniture(_statement_pages([40, 40, 52]))
    assert [(a.page, a.text) for a in findings] == [(3, "Oversea-Chinese Banking Corp")]
    assert findings[0].evidence["left_delta_pt"] == pytest.approx(12)


def test_furniture_in_place_on_every_page_is_not_flagged() -> None:
    assert lr._check_moved_furniture(_statement_pages([40, 40, 40])) == []


def test_two_pages_cannot_say_which_copy_moved() -> None:
    assert lr._check_moved_furniture(_statement_pages([40, 52])) == []


def test_repeated_transaction_text_is_not_furniture() -> None:
    # "GIRO PAYMENT" repeats within each page and sits mid-page: content, not furniture.
    findings = lr._check_moved_furniture(_statement_pages([40, 40, 40]))
    assert not any("GIRO" in a.text for a in findings)


def test_weak_findings_are_dropped_and_the_rest_ranked_strongest_first(monkeypatch) -> None:
    # Different kinds and far apart, so no other filter or merge touches them.
    def fake_indentation(blocks, groups=None, **_):
        findings = [
            lr._anomaly(
                anomaly_type,
                blocks[0].model_copy(update={"bbox": (72.0, y, 540.0, y + 14)}),
                detected="96 pt",
                expected="72 pt",
                baseline=None,
                deviation="+24 pt",
                confidence=confidence,
                evidence={"kind": kind, "left_delta_pt": 24.0, "vertical_delta_pt": 10.0, "unanchored": True},
            )
            for y, confidence, anomaly_type, kind in (
                (100.0, 0.75, "Spacing", "vertical"),
                (300.0, 0.5, "Spacing", "vertical"),
                (500.0, 0.95, "Indentation", "indentation"),
            )
        ]
        return findings, lr._assessed()

    monkeypatch.setattr(lr, "_check_indentation", fake_indentation)
    result = check_layout_anomalies("doc-1", _consistent_pdf())
    confidences = [a.confidence for a in result.anomalies if a.anomaly_type in {"Indentation", "Spacing"}]
    assert confidences == [0.95, 0.75]
    assert all(a.confidence >= lr.REPORT_MIN_CONFIDENCE for a in result.anomalies)
