"""
Font-consistency: a run is judged against its structural peers (its column,
the section headings), styled rows and signatures are not deviations.
"""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf
import pytest

from app.fraud_detection import font_consistency as fc
from app.fraud_detection.font_consistency import (
    ExtractedField,
    _Span,
    _agreed,
    _build_rows,
    _build_runs,
    _compile_detected_styles,
    _extract_spans,
    _field_search_text,
    _group_anomalies,
    _overlaps,
    check_font_consistency,
)

_LABELS = ["Inventories", "Trade receivables", "Other receivables", "Cash and bank", "Prepayments", "Deposits paid"]
_AMOUNTS = ["$119,620", "$194,390", "$233,890", "$1,223,500", "$88,100", "$14,900"]


def _pdf_bytes(build) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def _table_pdf(
    *,
    label_font: dict[int, str] | None = None,
    label_size: dict[int, float] | None = None,
    bold_row: int | None = None,
) -> bytes:
    """A label column and an amount column; per-row overrides tamper with one label."""

    def build(doc):
        page = doc.new_page(width=400, height=300)
        for i, (label, amount) in enumerate(zip(_LABELS, _AMOUNTS)):
            y = 80 + i * 18
            font = (label_font or {}).get(i, "hebo" if i == bold_row else "helv")
            size = (label_size or {}).get(i, 9)
            page.insert_text((50, y), label, fontsize=size, fontname=font)
            page.insert_text((250, y), amount, fontsize=9, fontname="hebo" if i == bold_row else "helv")

    return _pdf_bytes(build)


def _flagged_texts(data: bytes) -> list[str]:
    result = check_font_consistency("doc-1", data)
    assert result.unable_to_assess is False
    return [loc.text for anomaly in result.anomalies for loc in anomaly.locations]


def _span(**kwargs) -> _Span:
    values = dict(
        text="HELLO", font="Helvetica", size=12.0, bold=False, italic=False,
        underline=False, bbox=(10.0, 20.0, 80.0, 34.0), page=0,
    )
    values.update(kwargs)
    return _Span(**values)


def _blank_pdf() -> bytes:
    return _pdf_bytes(lambda doc: doc.new_page(width=200, height=200))


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


# --- the three tampers the check must catch ---------------------------------


def test_clean_table_has_no_findings() -> None:
    assert _flagged_texts(_table_pdf()) == []


def test_label_in_another_font_is_flagged() -> None:
    assert _flagged_texts(_table_pdf(label_font={2: "cour"})) == ["Other receivables"]


def test_label_in_another_size_is_flagged() -> None:
    assert _flagged_texts(_table_pdf(label_size={0: 12})) == ["Inventories"]


def test_underlined_heading_among_plain_headings_is_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=400)
        for i, title in enumerate(["Entity Information", "Business Activity", "Capital", "Audit Firms", "Charges"]):
            y = 60 + i * 70
            page.insert_text((40, y), title, fontsize=10, fontname="hebo")
            page.insert_text((60, y + 18), f"Detail line for {title}", fontsize=9, fontname="helv")
            page.insert_text((250, y + 18), f"value {i}", fontsize=9, fontname="helv")
            if title == "Capital":
                page.draw_line((40, y + 2), (40 + fc.pymupdf.get_text_length(title, "hebo", 10), y + 2), width=0.6)

    result = check_font_consistency("doc-1", _pdf_bytes(build))
    flagged = [a for a in result.anomalies if a.locations[0].text == "Capital"]
    assert len(result.anomalies) == 1
    assert flagged[0].deviation_type == "emphasis"
    assert flagged[0].detected["underline"] is True
    assert flagged[0].expected["underline"] is False


# --- what must not fire -----------------------------------------------------


def test_styled_total_row_is_not_flagged() -> None:
    # A bold row whose label and amount agree is a total, not a deviation.
    assert _flagged_texts(_table_pdf(bold_row=3)) == []


def test_bold_column_head_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=300)
        page.insert_text((50, 60), "Description", fontsize=9, fontname="hebo")
        for i, label in enumerate(_LABELS):
            page.insert_text((50, 80 + i * 18), label, fontsize=9, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == []


def test_signature_in_another_font_on_a_signing_rule_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=400)
        for i, label in enumerate(_LABELS):
            page.insert_text((50, 60 + i * 18), label, fontsize=9, fontname="helv")
        page.insert_text((50, 220), "Tan Wei Ming", fontsize=16, fontname="tiit")
        page.insert_text((50, 232), "_" * 28, fontsize=9, fontname="helv")
        page.insert_text((50, 246), "Director", fontsize=9, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == []


def test_script_font_name_is_a_signature() -> None:
    assert fc._SCRIPT_FONT_RE.search("DancingScript-Regular")
    assert not fc._SCRIPT_FONT_RE.search("Helvetica-Bold")


def test_separator_punctuation_in_another_size_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=300)
        for i, label in enumerate(_LABELS):
            y = 60 + i * 20
            page.insert_text((50, y), label, fontsize=8.5, fontname="helv")
            page.insert_text((180, y), ":", fontsize=10, fontname="helv")
            page.insert_text((200, y), _AMOUNTS[i], fontsize=8.5, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == []


def test_inline_bold_term_in_a_sentence_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=500, height=200)
        for i in range(5):
            y = 60 + i * 14
            page.insert_text((40, y), "the Company means", fontsize=9, fontname="helv")
            page.insert_text((140, y), "Golden Harvest Pte Ltd", fontsize=9, fontname="hebo")
            page.insert_text((262, y), "and its successors in title", fontsize=9, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == []


def test_restyled_word_inside_a_sentence_is_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=500, height=200)
        page.insert_text((40, 80), "The facility amount is", fontsize=10, fontname="helv")
        width = pymupdf.get_text_length("The facility amount is ", "helv", 10)
        page.insert_text((40 + width, 80), "$4,500,000", fontsize=10, fontname="cour")
        width2 = pymupdf.get_text_length("$4,500,000 ", "cour", 10)
        page.insert_text((40 + width + width2, 80), "payable monthly", fontsize=10, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == ["$4,500,000"]


def test_style_recurring_in_a_column_is_a_tier_not_a_deviation() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=300)
        rows = [("Name A", "hebo"), ("1 Road", "helv"), ("Name B", "hebo"), ("2 Road", "helv"), ("Name C", "hebo"),
                ("3 Road", "helv"), ("Name D", "hebo")]
        for i, (text, font) in enumerate(rows):
            page.insert_text((50, 60 + i * 16), text, fontsize=9, fontname=font)

    assert _flagged_texts(_pdf_bytes(build)) == []


# --- public API edges -------------------------------------------------------


def test_check_font_consistency_invalid_empty_and_image_only() -> None:
    bad = check_font_consistency("doc-1", b"not-a-pdf")
    assert bad.unable_to_assess is True
    assert bad.unable_to_assess_reason

    empty = check_font_consistency("doc-1", _blank_pdf())
    assert empty.unable_to_assess is True
    assert "No extractable text" in (empty.unable_to_assess_reason or "")

    image_only = check_font_consistency("doc-1", _image_only_pdf())
    assert image_only.unable_to_assess is True
    assert "embedded images" in (image_only.unable_to_assess_reason or "")


def test_check_font_consistency_returns_empty_on_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(fc, "_extract_spans", lambda doc: (_ for _ in ()).throw(RuntimeError("boom")))
    result = check_font_consistency("doc-1", _table_pdf())
    assert result.unable_to_assess is True
    assert "Unexpected error" in (result.unable_to_assess_reason or "")


def test_finding_is_named_after_the_extracted_field_it_sits_on() -> None:
    fields = [
        ExtractedField(field_name="amount", field_type="number", field_value=100.0, confidence=1),
        ExtractedField(field_name="blank", field_type="string", field_value="  ", confidence=1),
        ExtractedField(field_name="missing", field_type="string", field_value="NOT IN DOC", confidence=1),
        ExtractedField(field_name="asset", field_type="string", field_value='"Other receivables"', confidence=1),
    ]
    result = check_font_consistency("doc-1", _table_pdf(label_font={2: "cour"}), fields)
    assert [a.field_name for a in result.anomalies] == ["asset"]


# --- building blocks --------------------------------------------------------


def test_extract_spans_skips_non_text_empty_and_bad_bbox() -> None:
    page = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 600, 800),
        get_drawings=lambda: [{}, {"rect": None}, {"rect": pymupdf.Rect(0, 15.5, 30, 16.5)}],
        get_text=lambda mode: {
            "blocks": [
                {"type": 1},
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "  "},
                                {"text": "Hi", "bbox": [0, 0, 1]},
                                {"text": "Yo", "bbox": [1, 2, 20, 16], "font": "Helv", "size": 12, "flags": 16 | 2},
                            ]
                        }
                    ],
                },
            ]
        },
    )
    spans = _extract_spans([page])
    assert len(spans) == 1
    assert spans[0].text == "Yo"
    assert spans[0].bold is True
    assert spans[0].italic is True


def test_underline_ignores_page_wide_rules() -> None:
    narrow = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 600, 800),
        get_drawings=lambda: [{"rect": pymupdf.Rect(0, 100, 500, 100.5)}],
    )
    assert fc._underline_rects(narrow) == []


def test_runs_split_on_column_gaps_not_word_gaps() -> None:
    spans = [
        _span(text="Name", bbox=(10, 0, 40, 12)),
        _span(text="Tan", bbox=(43, 0, 60, 12)),
        _span(text="S1234567D", bbox=(200, 0, 260, 12)),
    ]
    runs = _build_runs(spans)
    assert sorted(run.text for run in runs) == ["Name Tan", "S1234567D"]


def test_build_rows_splits_on_baseline() -> None:
    top = _span(text="A", bbox=(0, 0, 20, 12))
    same = _span(text="B", bbox=(30, 0, 50, 12.5))
    below = _span(text="C", bbox=(0, 40, 20, 54))
    rows = _build_rows([top, same, below])
    assert len(rows) == 2
    assert len(next(iter(rows.values()))) == 2


def test_agreed_needs_a_clear_majority() -> None:
    assert _agreed(["a", "a", "a", "b"], 3, 0.6) == "a"
    assert _agreed(["a", "a", "b"], 3, 0.6) is None
    assert _agreed(["a", "a", "a", "b", "b", "b"], 3, 0.6) is None
    assert _agreed([], 3, 0.6) is None


def test_compile_detected_styles_counts_each_style() -> None:
    styles = _compile_detected_styles([_span(), _span(), _span(font="Courier")])
    assert styles[0].occurrence_count == 2


def test_group_anomalies_merges_identical_deviations_rarest_first() -> None:
    base = dict(
        page=0, bbox=(0, 0, 1, 1), text="x", deviation_type="font",
        detected={"font": "Courier"}, expected={"font": "Helvetica"},
    )
    other = dict(base, text="y", detected={"font": "Times"})
    grouped = _group_anomalies(
        [fc._RawAnomaly(**base), fc._RawAnomaly(**base), fc._RawAnomaly(**other)]
    )
    assert [a.occurrence_count for a in grouped] == [1, 2]


def test_field_search_text_handles_json_numbers_and_blank() -> None:
    assert _field_search_text(ExtractedField(field_name="n", field_type="number", field_value=12.5, confidence=1)) is None
    assert _field_search_text(ExtractedField(field_name="n", field_type="string", field_value="  ", confidence=1)) is None
    assert (
        _field_search_text(
            ExtractedField(field_name="c", field_type="string", field_value='"ACME Pte Ltd"', confidence=1)
        )
        == "ACME Pte Ltd"
    )
    assert _field_search_text(ExtractedField(field_name="l", field_type="string", field_value="not json", confidence=1)) == "not json"
    assert _field_search_text(ExtractedField(field_name="t", field_type="object", field_value={"a": 1}, confidence=1)) is None


def test_overlaps_and_zero_area(monkeypatch: pytest.MonkeyPatch) -> None:
    rect = pymupdf.Rect(10, 10, 40, 30)
    assert _overlaps((10, 10, 40, 30), rect) is True
    assert _overlaps((100, 100, 120, 120), rect) is False
    monkeypatch.setattr(pymupdf.Rect, "get_area", lambda self: 0.0)
    assert _overlaps((10, 10, 40, 30), pymupdf.Rect(10, 10, 40, 30)) is False


def test_font_family_returns_original_name_when_stripping_leaves_nothing() -> None:
    assert fc._font_family("Bold") == "Bold"
    assert fc._font_family("ABCDEF+Aptos,Bold") == "Aptos"


def test_is_underlined_false_for_zero_width_span() -> None:
    assert fc._is_underlined((100.0, 0.0, 100.0, 50.0), []) is False


def test_is_underlined_false_when_no_rect_is_near_the_baseline() -> None:
    far_rect = pymupdf.Rect(0, 1000, 100, 1005)
    assert fc._is_underlined((0.0, 0.0, 100.0, 20.0), [far_rect]) is False


def test_document_title_larger_than_its_section_headings_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=500)
        page.insert_text((40, 40), "ANNUAL FINANCIAL STATEMENTS", fontsize=16, fontname="hebo")
        for i, title in enumerate(["1. GENERAL INFORMATION", "2. BASIS OF PREPARATION", "3. REVENUE", "4. OTHER INCOME"]):
            page.insert_text((40, 90 + i * 60), title, fontsize=9.5, fontname="hebo")
            page.insert_text((40, 106 + i * 60), "Body text that follows the heading above it.", fontsize=9, fontname="helv")

    assert _flagged_texts(_pdf_bytes(build)) == []


def test_underlined_instruction_sentence_is_not_flagged() -> None:
    def build(doc):
        page = doc.new_page(width=500, height=300)
        for i, label in enumerate(_LABELS):
            page.insert_text((40, 60 + i * 18), label, fontsize=9, fontname="helv")
        sentence = "If you have selected Reason B for any of the countries please explain below"
        page.insert_text((40, 60 + 3 * 18), sentence, fontsize=9, fontname="helv")
        width = pymupdf.get_text_length(sentence, "helv", 9)
        page.draw_line((40, 60 + 3 * 18 + 1.5), (40 + width, 60 + 3 * 18 + 1.5), width=0.6)

    assert _flagged_texts(_pdf_bytes(build)) == []


# --- numbers the column peers cannot judge ------------------------------------------------------


def _runs(data: bytes) -> list:
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return _build_runs([span for span in _extract_spans(doc) if fc._is_assessable(span)])
    finally:
        doc.close()


_PAYSLIP_ROWS = [
    ("Basic salary", "3,290.00"), ("Allowances", "630.00"), ("Overtime", "140.00"),
    ("Total earnings", "4,020.00"), ("Employee contribution", "-658.00"),
    ("Other deductions", "-55.96"), ("Total deductions", "713.96"),
]


def _payslip_pdf(*, courier: str | None = None) -> bytes:
    """Right-aligned amounts; a tampered one is redrawn at its left origin, so it ends further right."""

    def build(doc):
        page = doc.new_page(width=400, height=300)
        for i, (label, amount) in enumerate(_PAYSLIP_ROWS):
            y = 80 + i * 18
            font = "hebo" if label.startswith("Total") else "helv"
            page.insert_text((40, y), label, fontsize=9, fontname=font)
            left = 360 - pymupdf.get_text_length(amount, font, 9)
            page.insert_text((left, y), amount, fontsize=9, fontname="cour" if amount == courier else font)

    return _pdf_bytes(build)


def _capital_pdf(value_size: float) -> bytes:
    """A one-row table: no column to compare the value with, only its row."""

    def build(doc):
        page = doc.new_page(width=560, height=200)
        for x, head, cell in (
            (40, "Issued Share Capital", "500,000.00"), (170, "Number of Shares", "500,000.0"),
            (300, "Currency", "SINGAPORE, DOLLARS"), (430, "Share Type", "ORDINARY"),
        ):
            page.insert_text((x, 60), head, fontsize=8, fontname="hebo")
            size = value_size if cell == "500,000.00" else 8
            page.insert_text((x, 90), cell, fontsize=size, fontname="helv")

    return _pdf_bytes(build)


def test_a_right_aligned_number_in_another_typeface_is_flagged_though_it_left_its_column() -> None:
    assert "4,020.00" in _flagged_texts(_payslip_pdf(courier="4,020.00"))


def test_a_lone_table_value_in_a_larger_size_is_flagged_against_its_row() -> None:
    assert "500,000.00" in _flagged_texts(_capital_pdf(value_size=10.8))


def test_a_table_value_in_its_rows_own_size_is_not_flagged_by_the_row_check() -> None:
    assert fc._row_deviations(_runs(_capital_pdf(value_size=8))) == []


def test_a_larger_size_that_recurs_in_the_document_is_a_tier_not_a_deviation() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=200)
        for i, amount in enumerate(["1,200.00", "3,400.00", "5,600.00"]):
            y = 60 + i * 20
            page.insert_text((40, y), "Account", fontsize=8, fontname="helv")
            page.insert_text((120, y), "Balance", fontsize=8, fontname="helv")
            page.insert_text((200, y), amount, fontsize=11, fontname="helv")

    assert fc._row_deviations(_runs(_pdf_bytes(build))) == []


def test_numbers_split_between_two_typefaces_have_no_foreign_one() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=200)
        for i, amount in enumerate(["1,200.00", "3,400.00", "5,600.00", "7,800.00", "9,000.00", "2,100.00"]):
            page.insert_text((40, 60 + i * 18), f"Item {i}", fontsize=9, fontname="helv")
            page.insert_text((250, 60 + i * 18), amount, fontsize=9, fontname="cour" if i % 2 else "helv")

    assert fc._foreign_font_deviations(_runs(_pdf_bytes(build))) == []


def test_too_few_numbers_to_know_the_documents_typeface_flag_nothing() -> None:
    def build(doc):
        page = doc.new_page(width=400, height=200)
        for i, (amount, font) in enumerate([("1,200.00", "helv"), ("3,400.00", "helv"), ("5,600.00", "cour")]):
            page.insert_text((40, 60 + i * 18), amount, fontsize=9, fontname=font)

    assert fc._foreign_font_deviations(_runs(_pdf_bytes(build))) == []
