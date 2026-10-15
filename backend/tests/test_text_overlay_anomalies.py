"""Text overlay: text boxes that physically overlap, and watermarks that are allowed to."""

from __future__ import annotations

import pymupdf
import pytest

from app.fraud_detection.layout_anomalies import text_overlay as to
from app.fraud_detection.layout_anomalies import _Block, _Line, check_layout_anomalies


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


def _pdf_bytes(build) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def _overlays(result) -> list:
    return [a for a in result.anomalies if a.anomaly_type == "Text Overlay"]


# --- _check_text_overlaps on hand-built lines ---------------------------------


def test_overlapping_lines_are_flagged() -> None:
    first = _line((72, 100, 300, 114), text="Total amount payable")
    second = _line((150, 102, 380, 116), text="SGD 12,450.00 paid in full")
    findings = to._check_text_overlaps([_block([first]), _block([second])])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.anomaly_type == "Text Overlay"
    assert finding.page == 1
    assert finding.bbox == (150, 102, 300, 114)
    assert 0.7 <= finding.confidence <= 1.0
    assert finding.evidence["kind"] == "text-overlay"
    assert finding.evidence["overlap_ratio"] == pytest.approx(150 * 12 / (228 * 14), abs=1e-4)
    assert "Total amount payable" in finding.text
    assert "SGD 12,450.00" in finding.text
    assert finding.location == "Text overlap near x=150, y=102"


def test_lines_inside_one_block_are_compared_too() -> None:
    first = _line((72, 100, 300, 114), text="Total amount payable")
    second = _line((150, 102, 380, 116), text="SGD 12,450.00 paid in full")
    findings = to._check_text_overlaps([_block([first, second])])
    assert len(findings) == 1


def test_touching_lines_are_not_an_overlay() -> None:
    above = _line((72, 100, 300, 114), text="Line one of the clause")
    below = _line((72, 114, 300, 128), text="Line two of the clause")
    assert to._check_text_overlaps([_block([above]), _block([below])]) == []


def test_small_overlap_under_the_ratio_is_ignored() -> None:
    # Ascent meeting descent: 2pt of a 14pt line is about 14% of the box.
    above = _line((72, 100, 300, 114), text="Line one of the clause")
    below = _line((72, 112, 300, 126), text="Line two of the clause")
    assert to._check_text_overlaps([_block([above]), _block([below])]) == []


def test_minor_overlap_below_the_ratio_is_ignored() -> None:
    first = _line((0, 0, 100, 10), text="Left value")
    second = _line((85, 0, 185, 10), text="Right value")
    assert to._check_text_overlaps([_block([first]), _block([second])]) == []


def test_overlap_exactly_at_the_ratio_is_flagged() -> None:
    first = _line((0, 0, 100, 10), text="Left value")
    second = _line((80, 0, 180, 10), text="Right value")
    findings = to._check_text_overlaps([_block([first]), _block([second])])
    assert len(findings) == 1
    assert findings[0].evidence["overlap_ratio"] == pytest.approx(to._TEXT_OVERLAP_MIN_RATIO)


def test_same_position_on_different_pages_is_not_an_overlay() -> None:
    page_one = _line((72, 100, 300, 114), text="Account number 0123")
    page_two = _line((72, 100, 300, 114), text="Account number 4567", page=1)
    assert to._check_text_overlaps([_block([page_one]), _block([page_two])]) == []


def test_blank_and_zero_size_lines_are_skipped() -> None:
    real = _line((72, 100, 300, 114), text="Account number 0123")
    blank = _line((72, 100, 300, 114), text="   ")
    flat = _line((72, 105, 300, 105), text="Flat line")
    assert to._check_text_overlaps([_block([real]), _block([blank]), _block([flat])]) == []


# --- watermarks ---------------------------------------------------------------


def _watermark(page: int) -> _Line:
    return _line((206, 380, 406, 412), text="CONFIDENTIAL", page=page)


def _body_under_watermark(page: int) -> _Line:
    return _line((72, 384, 540, 398), text="Clause text running under the watermark", page=page)


def test_centred_text_repeated_on_two_pages_is_an_expected_watermark() -> None:
    lines = [_watermark(0), _watermark(1)]
    assert to._is_expected_overlay(lines[0], lines) is True


def test_centred_text_on_one_page_is_not_a_watermark() -> None:
    line = _watermark(0)
    assert to._is_expected_overlay(line, [line]) is False


def test_repeated_text_off_centre_is_not_a_watermark() -> None:
    corner = [_line((20, 20, 120, 34), text="CONFIDENTIAL", page=p) for p in (0, 1)]
    assert to._is_expected_overlay(corner[0], corner) is False


def test_watermark_comparison_ignores_case_and_spacing() -> None:
    lines = [_watermark(0), _line((206, 380, 406, 412), text="confidential  ", page=1)]
    assert to._is_expected_overlay(lines[0], lines) is True


def test_text_under_a_watermark_is_not_flagged() -> None:
    blocks = [
        _block([_watermark(0)]),
        _block([_body_under_watermark(0)]),
        _block([_watermark(1)]),
        _block([_body_under_watermark(1)]),
    ]
    assert to._check_text_overlaps(blocks) == []


def test_single_page_stamp_over_text_is_flagged() -> None:
    blocks = [_block([_watermark(0)]), _block([_body_under_watermark(0)])]
    findings = to._check_text_overlaps(blocks)
    assert len(findings) == 1
    assert "CONFIDENTIAL" in findings[0].text


# --- through check_layout_anomalies -------------------------------------------


BODY = (
    "The registered office of the Company shall be at such place in Singapore "
    "as the directors may from time to time determine."
)


def test_text_typed_over_existing_text_is_reported() -> None:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 120), "Amount due: SGD 1,200.00", fontsize=11)
        page.insert_text((80, 122), "Amount due: SGD 9,800.00", fontsize=11)

    result = check_layout_anomalies("doc-1", _pdf_bytes(build))
    overlays = _overlays(result)
    assert len(overlays) == 1
    assert overlays[0].page == 1
    body = overlays[0].model_dump()
    assert body["type"] == "Text Overlay"
    assert body["checks"][0]["check"] == "text overlay"


def test_clean_document_has_no_overlay_findings() -> None:
    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for _ in range(4):
            page.insert_textbox(pymupdf.Rect(72, y, 540, y + 70), BODY, fontsize=11)
            y += 80

    assert _overlays(check_layout_anomalies("doc-1", _pdf_bytes(build))) == []


def test_stacked_header_lines_whose_letters_do_not_touch_are_not_an_overlay() -> None:
    # A real payslip header: a 15 pt Courier-Bold company name reserves ~6 pt
    # below its baseline, so its box overlaps the subtitle under it although
    # the letters are clearly apart.
    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((149, 77.7), "Accord Corporate Services Pte Ltd", fontname="cobo", fontsize=15)
        page.insert_text((251.7, 89.7), "PAYROLL STATEMENT", fontname="cour", fontsize=9)

    assert _overlays(check_layout_anomalies("doc-1", _pdf_bytes(build))) == []


def test_signature_written_on_a_signature_line_is_not_an_overlay() -> None:
    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((63, 488), "Rajesh Kumar s/o Suresh", fontname="tiit", fontsize=16)
        page.insert_text((63, 492), "_________________________", fontsize=11)

    assert _overlays(check_layout_anomalies("doc-1", _pdf_bytes(build))) == []


def test_new_value_typed_slightly_below_the_original_is_still_reported() -> None:
    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((215, 350), "NET PAY: SGD 3,000.00", fontsize=11)
        page.insert_text((216, 352), "NET PAY: SGD 4,000.00", fontsize=11)

    assert len(_overlays(check_layout_anomalies("doc-1", _pdf_bytes(build)))) == 1


def test_ink_box_falls_back_to_the_line_box_without_a_baseline() -> None:
    line = _line((72.0, 100.0, 300.0, 112.0))
    assert to._ink_box(line) == line.bbox
    with_baseline = _line((72.0, 100.0, 300.0, 116.0), baseline=112.0, size=10.0)
    assert to._ink_box(with_baseline) == (72.0, 112.0 - 7.2, 300.0, 112.0 + 1.8)


def _stamped_pages_pdf(stamped_pages: set[int]) -> bytes:
    # Each page gets its own clause. The same text at the same spot on every
    # page would itself look like a watermark.
    def build(doc):
        for index in range(2):
            page = doc.new_page(width=612, height=792)
            clause = f"Clause {index + 1}. {BODY}"
            page.insert_textbox(pymupdf.Rect(72, 380, 540, 450), clause, fontsize=11)
            if index in stamped_pages:
                page.insert_text((240, 400), "CONFIDENTIAL", fontsize=24)

    return _pdf_bytes(build)


def test_watermark_across_pages_is_not_reported() -> None:
    result = check_layout_anomalies("doc-1", _stamped_pages_pdf({0, 1}))
    assert _overlays(result) == []


def test_stamp_on_one_page_over_text_is_reported() -> None:
    result = check_layout_anomalies("doc-1", _stamped_pages_pdf({0}))
    overlays = _overlays(result)
    assert [a.page for a in overlays] == [1]
    assert "CONFIDENTIAL" in overlays[0].text
