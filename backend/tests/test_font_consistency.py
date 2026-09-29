"""
Font-consistency v2: open/empty/error paths plus real mixed-style PDFs.

font_consistency.py (v1) was retired once v2 superseded it as the real
check; the span-extraction/style-comparison building blocks it originated
(_Span, _compile_detected_styles, _dimension_deviations, _dominant_pattern,
_extract_spans) moved into font_consistency_v2.py itself rather than being
deleted, since v2 still relies on them unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace

import pymupdf
import pytest

from app.fraud_detection import font_consistency_v2 as v2
from app.fraud_detection.font_consistency_v2 import (
    ExtractedField,
    _Span,
    _build_rows,
    _check_field_anchored,
    _compile_detected_styles,
    _dimension_deviations,
    _dominant_pattern,
    _extract_spans,
    _field_search_text,
    _group_anomalies_v2,
    _match_field_occurrences,
    _overlaps,
    _score_row_deviations,
    check_font_consistency_v2,
)


def _pdf_bytes(build) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def _consistent_pdf() -> bytes:
    def build(doc):
        page = doc.new_page(width=400, height=240)
        page.insert_text((48, 80), "Golden Harvest", fontsize=12, fontname="helv")
        page.insert_text((48, 120), "Trading Pte Ltd", fontsize=12, fontname="helv")

    return _pdf_bytes(build)


def _mixed_line_pdf(*, font: bool = False, size: bool = False, bold: bool = False) -> bytes:
    def build(doc):
        page = doc.new_page(width=600, height=200)
        page.insert_text((40, 100), "AAA", fontsize=12, fontname="helv")
        page.insert_text((90, 100), "BBB", fontsize=12, fontname="helv")
        second_font = "cour" if font else ("hebo" if bold else "helv")
        second_size = 20 if size else 12
        page.insert_text((140, 100), "XXX", fontsize=second_size, fontname=second_font)

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


def _span(**kwargs) -> _Span:
    values = dict(
        text="HELLO",
        font="Helvetica",
        size=12.0,
        bold=False,
        italic=False,
        underline=False,
        bbox=(10.0, 20.0, 80.0, 34.0),
        page=0,
        line_key=(0, 0, 0),
    )
    values.update(kwargs)
    return _Span(**values)


# ---------------------------------------------------------------------------
# span extraction & style-comparison building blocks (originated in the now-
# retired v1 module; v2 depends on them directly, unchanged)
# ---------------------------------------------------------------------------


def test_extract_spans_skips_non_text_empty_and_bad_bbox() -> None:
    page = SimpleNamespace(
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
                                {
                                    "text": "Yo",
                                    "bbox": [1, 2, 20, 16],
                                    "font": "Helv",
                                    "size": 12,
                                    "flags": 16 | 2,
                                },
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


def test_compile_detected_styles_and_dimension_deviations() -> None:
    """
    _compile_detected_styles and _dimension_deviations originated in the
    retired v1 module and now live directly in font_consistency_v2.py (see
    this file's module docstring) — kept here as unit coverage since v2's
    own tests only exercise these indirectly.
    """
    majority = _span(text="AAA", font="Helvetica", size=12.0, bbox=(0, 0, 20, 12))
    majority2 = _span(text="BBB", font="Helvetica", size=12.0, bbox=(20, 0, 40, 12))
    deviant = _span(text="XXX", font="Courier", size=18.0, bold=True, bbox=(40, 0, 80, 12))
    spans = [majority, majority2, deviant]

    styles = _compile_detected_styles(spans)
    assert styles[0].occurrence_count >= 1

    dims = _dimension_deviations(deviant, _dominant_pattern(spans))
    assert dims["font"] is True
    assert dims["size"] is True


# ---------------------------------------------------------------------------
# v2 helpers
# ---------------------------------------------------------------------------


def test_field_search_text_handles_json_numbers_and_blank() -> None:
    assert _field_search_text(ExtractedField(field_name="n", field_type="number", field_value=12.5, confidence=1)) is None
    assert _field_search_text(ExtractedField(field_name="n", field_type="string", field_value="  ", confidence=1)) is None
    assert (
        _field_search_text(
            ExtractedField(field_name="c", field_type="string", field_value='"ACME Pte Ltd"', confidence=1)
        )
        == "ACME Pte Ltd"
    )
    assert (
        _field_search_text(ExtractedField(field_name="c", field_type="string", field_value="ACME", confidence=1))
        == "ACME"
    )
    assert _field_search_text(ExtractedField(field_name="t", field_type="object", field_value={"a": 1}, confidence=1)) is None
    assert _field_search_text(ExtractedField(field_name="l", field_type="string", field_value="not json", confidence=1)) == "not json"


def test_overlaps_and_zero_area(monkeypatch: pytest.MonkeyPatch) -> None:
    rect = pymupdf.Rect(10, 10, 40, 30)
    assert _overlaps((10, 10, 40, 30), rect) is True
    assert _overlaps((100, 100, 120, 120), rect) is False
    monkeypatch.setattr(pymupdf.Rect, "get_area", lambda self: 0.0)
    assert _overlaps((10, 10, 40, 30), pymupdf.Rect(10, 10, 40, 30)) is False


def test_build_rows_splits_on_baseline() -> None:
    top = _span(text="A", bbox=(0, 0, 20, 12), line_key=(0, 0, 0))
    same = _span(text="B", bbox=(30, 0, 50, 12.5), line_key=(0, 0, 1))
    below = _span(text="C", bbox=(0, 40, 20, 54), line_key=(0, 1, 0))
    rows = _build_rows([top, same, below])
    assert len(rows) == 2
    assert len(next(iter(rows.values()))) == 2


def test_match_field_occurrences_skips_empty_pages_and_search_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=200)
    page.insert_text((40, 80), "ACME Pte Ltd", fontsize=12, fontname="helv")
    doc.new_page(width=300, height=200)
    spans = _extract_spans(doc)
    found = _match_field_occurrences(doc, {0: spans}, "ACME Pte Ltd")
    assert found
    monkeypatch.setattr(
        pymupdf.Page,
        "search_for",
        lambda self, text: (_ for _ in ()).throw(ValueError("bad query")),
    )
    assert _match_field_occurrences(doc, {0: spans}, "ACME Pte Ltd") == []
    doc.close()


# ---------------------------------------------------------------------------
# v2 public API
# ---------------------------------------------------------------------------


def test_check_font_consistency_v2_invalid_empty_and_image_only() -> None:
    bad = check_font_consistency_v2("doc-1", b"not-a-pdf")
    assert bad.unable_to_assess is True
    assert bad.unable_to_assess_reason

    empty = check_font_consistency_v2("doc-1", _blank_pdf())
    assert empty.unable_to_assess is True
    assert "No extractable text" in (empty.unable_to_assess_reason or "")

    image_only = check_font_consistency_v2("doc-1", _image_only_pdf())
    assert image_only.unable_to_assess is True
    assert "embedded images" in (image_only.unable_to_assess_reason or "")


def test_check_font_consistency_v2_returns_empty_on_internal_error(monkeypatch) -> None:
    monkeypatch.setattr(v2, "_extract_spans", lambda doc: (_ for _ in ()).throw(RuntimeError("boom")))
    result = check_font_consistency_v2("doc-1", _consistent_pdf())
    assert result.unable_to_assess is True
    assert "Unexpected error" in (result.unable_to_assess_reason or "")


def test_score_row_deviations_and_group_v2() -> None:
    majority = _span(text="AAA", font="Helvetica", size=12.0)
    majority2 = _span(text="BBB", font="Helvetica", size=12.0, bbox=(20, 20, 40, 34))
    deviant = _span(text="XXX", font="Courier", size=18.0, bold=True, bbox=(40, 20, 80, 34))
    spans = [majority, majority2, deviant]
    key = (0, 0)
    raw = _score_row_deviations(
        spans,
        {id(span): key for span in spans},
        {key: _dominant_pattern(spans)},
    )
    assert raw
    assert raw[0].deviation_type == "combo"
    grouped = _group_anomalies_v2(raw + raw)
    assert grouped[0].occurrence_count >= 1
    assert grouped[0].occurrence_count <= grouped[-1].occurrence_count or len(grouped) == 1


def test_check_field_anchored_flags_deviating_occurrence(monkeypatch: pytest.MonkeyPatch) -> None:
    majority = _span(text="ACME Pte Ltd", font="Helvetica", size=12.0)
    deviant = _span(text="ACME Pte Ltd", font="Courier", size=18.0, bbox=(10, 80, 90, 98))
    monkeypatch.setattr(
        v2,
        "_match_field_occurrences",
        lambda *args, **kwargs: [[majority], [deviant]],
    )
    raw = _check_field_anchored(
        [
            ExtractedField(field_name="skip", field_type="number", field_value=1, confidence=1),
            ExtractedField(field_name="company", field_type="string", field_value="ACME Pte Ltd", confidence=1),
            ExtractedField(field_name="missing", field_type="string", field_value="NOPE", confidence=1),
        ],
        SimpleNamespace(),
        {0: [majority, deviant]},
        _dominant_pattern([majority, majority, deviant]),
    )
    assert any(item.field_name == "company" for item in raw)
    monkeypatch.setattr(v2, "_match_field_occurrences", lambda *a, **k: [])
    assert (
        _check_field_anchored(
            [ExtractedField(field_name="company", field_type="string", field_value="ACME Pte Ltd", confidence=1)],
            SimpleNamespace(),
            {0: [majority]},
            _dominant_pattern([majority]),
        )
        == []
    )


def test_check_font_consistency_v2_row_and_field_anchored() -> None:
    consistent = check_font_consistency_v2("doc-1", _consistent_pdf())
    assert consistent.unable_to_assess is False
    assert consistent.all_detected_styles

    mixed = check_font_consistency_v2("doc-1", _mixed_line_pdf(font=True))
    assert mixed.unable_to_assess is False

    def build(doc):
        page = doc.new_page(width=420, height=240)
        page.insert_text((40, 80), "ACME Pte Ltd", fontsize=12, fontname="helv")
        page.insert_text((40, 140), "Other text on the page", fontsize=12, fontname="helv")
        page.insert_text((40, 180), "ACME Pte Ltd", fontsize=12, fontname="cour")

    pdf = _pdf_bytes(build)
    fields = [
        ExtractedField(field_name="amount", field_type="number", field_value=100.0, confidence=1),
        ExtractedField(field_name="blank", field_type="string", field_value="   ", confidence=1),
        ExtractedField(field_name="missing", field_type="string", field_value="NOT IN DOC", confidence=1),
        ExtractedField(
            field_name="company",
            field_type="string",
            field_value='"ACME Pte Ltd"',
            confidence=0.9,
        ),
    ]
    anchored = check_font_consistency_v2("doc-1", pdf, fields)
    assert anchored.unable_to_assess is False


def test_check_field_anchored_direct(monkeypatch) -> None:
    def build(doc):
        page = doc.new_page(width=300, height=160)
        page.insert_text((40, 80), "HELLO WORLD", fontsize=12, fontname="helv")

    pdf = _pdf_bytes(build)
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    spans = _extract_spans(doc)
    by_page = {0: spans}
    baseline = _dominant_pattern(spans)
    raw = _check_field_anchored(
        [ExtractedField(field_name="x", field_type="string", field_value="HELLO WORLD", confidence=1)],
        doc,
        by_page,
        baseline,
    )
    assert isinstance(raw, list)
    doc.close()


def test_font_family_returns_original_name_when_stripping_leaves_nothing() -> None:
    assert v2._font_family("Bold") == "Bold"


def test_is_underlined_false_for_zero_width_span() -> None:
    assert v2._is_underlined((100.0, 0.0, 100.0, 50.0), []) is False


def test_is_underlined_false_when_no_rect_is_near_the_baseline() -> None:
    far_rect = pymupdf.Rect(0, 1000, 100, 1005)
    assert v2._is_underlined((0.0, 0.0, 100.0, 20.0), [far_rect]) is False


def test_dominant_empty_and_tie() -> None:
    assert v2._dominant([]) is None
    assert v2._dominant(["a", "b"]) is None
