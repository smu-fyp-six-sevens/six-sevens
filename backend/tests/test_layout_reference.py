"""Curated reference layouts: building one, applying it, and stepping aside when it does not fit."""

from __future__ import annotations

import json

import pymupdf
import pytest

from app.config import settings
from app.fraud_detection.layout_anomalies import check_layout_anomalies
from app.fraud_detection.layout_anomalies import reference as ref
from app.fraud_detection.layout_anomalies.extraction import _extract_blocks, _open_document

LABELS = [
    ((72, 100), "Tax reference number:"),
    ((72, 130), "Name of taxpayer:"),
    ((72, 160), "Year of assessment 2025"),
    ((72, 190), "Chargeable income"),
    ((72, 220), "Tax payable"),
    ((72, 250), "Date of notice:"),
    # A second section lower down, which a longer table above can push down.
    ((72, 400), "Payment summary"),
    ((72, 430), "Amount paid to date"),
    ((72, 460), "Balance payable"),
    ((72, 490), "Payment due date"),
    ((72, 520), "GIRO deduction plan"),
    ((72, 550), "Enquiries and appeals"),
]
SECTION_TWO = 350.0
VALUE_X = 260.0
AMOUNT_RIGHT = 520.0


def _form(
    name: str,
    income: str,
    *,
    move: str | tuple[str, ...] | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    page_dx: float = 0.0,
    page_dy: float = 0.0,
    push_below: float | None = None,
    push: float = 0.0,
    extra: list[tuple[tuple[float, float], str]] = (),
) -> bytes:
    """A one-page form: fixed labels, a left-aligned name, a right-aligned amount."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)

    def put(x: float, y: float, text: str) -> None:
        moved = text == move if isinstance(move, str) else text in (move or ())
        shift = (dx, dy) if moved else (0.0, 0.0)
        # A longer table above pushes everything below it down together.
        pushed = push if push_below is not None and y >= push_below else 0.0
        page.insert_text((x + page_dx + shift[0], y + page_dy + pushed + shift[1]), text, fontsize=11)

    for (x, y), text in [*LABELS, *extra]:
        put(x, y, text)
    put(VALUE_X, 130, name)
    put(AMOUNT_RIGHT - pymupdf.get_text_length(income, fontsize=11), 190, income)
    data = doc.tobytes()
    doc.close()
    return data


def _blocks(data: bytes):
    doc = _open_document(data)
    try:
        return _extract_blocks(doc)
    finally:
        doc.close()


@pytest.fixture
def noa_reference() -> ref.ReferenceLayout:
    copies = [_blocks(_form("TAN WEI MING", "481,600.00")), _blocks(_form("LIM HUI LING", "92,300.00"))]
    return ref.build_reference("test_noa", "notice_of_assessment", copies)


def _compare(reference, data: bytes) -> ref.ReferenceComparison:
    return ref.compare_with_reference(_blocks(data), reference)


def test_shared_text_becomes_labels_and_values_keep_no_text(noa_reference) -> None:
    assert {label.text for label in noa_reference.labels} == {text.lower() for _, text in LABELS}
    assert len(noa_reference.slots) == 2
    assert all(slot.text is None for slot in noa_reference.slots)


def test_a_value_position_from_any_copy_is_a_slot() -> None:
    # The second copy's name sits lower (as a wrapped name would); a third document doing the same fits.
    copies = [
        _blocks(_form("TAN WEI MING", "481,600.00")),
        _blocks(_form("MUHAMMAD FAIZAL BIN RAHMAN", "92,300.00", move="MUHAMMAD FAIZAL BIN RAHMAN", dy=10)),
    ]
    built = ref.build_reference("test_noa", "notice_of_assessment", copies)
    assert len(built.slots) == 3
    lower = _form("AHMAD SYAFIQ BIN OSMAN", "7,250.00", move="AHMAD SYAFIQ BIN OSMAN", dy=10)
    assert _compare(built, lower).findings == []


def test_a_reference_needs_two_copies() -> None:
    with pytest.raises(ValueError):
        ref.build_reference("x", "notice_of_assessment", [_blocks(_form("A", "1.00"))])


def test_genuine_copy_with_other_values_has_no_findings(noa_reference) -> None:
    comparison = _compare(noa_reference, _form("NG SIEW LAN", "7,250.00"))
    assert comparison.fit == 1.0
    assert comparison.findings == []


def test_whole_page_offset_is_not_a_finding(noa_reference) -> None:
    comparison = _compare(noa_reference, _form("NG SIEW LAN", "7,250.00", page_dx=5, page_dy=3))
    assert comparison.fit == 1.0
    assert comparison.findings == []


def test_a_moved_label_is_not_reported(noa_reference) -> None:
    # Labels place the document on the reference; a label in another spot is not a finding.
    comparison = _compare(noa_reference, _form("NG SIEW LAN", "7,250.00", move="Tax payable", dx=12))
    assert comparison.fit == pytest.approx(11 / 12)
    assert comparison.findings == []


def test_moved_value_is_reported(noa_reference) -> None:
    comparison = _compare(noa_reference, _form("NG SIEW LAN", "7,250.00", move="NG SIEW LAN", dy=10))
    assert [f.text for f in comparison.findings] == ["NG SIEW LAN"]
    finding = comparison.findings[0]
    assert finding.anomaly_type == "Spacing"
    assert finding.evidence["kind"] == "reference-position"
    assert finding.evidence["element"] == "value"
    assert finding.evidence["dy_pt"] == pytest.approx(10, abs=0.5)
    # Named by document type for the officer, never by the reference file's internal name.
    assert "standard notice of assessment layout" in finding.summary
    assert "test_noa" not in finding.summary and "test_noa" not in str(finding.evidence)


def test_another_form_does_not_fit(noa_reference, tmp_path, monkeypatch) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for y, text in [(100, "PAY PERIOD"), (130, "EMPLOYEE"), (160, "Basic salary"), (190, "Net pay")]:
        page.insert_text((300, y), text, fontsize=11)
    other = doc.tobytes()
    doc.close()

    assert _compare(noa_reference, other).fit < ref.FIT_MIN
    _store(noa_reference, tmp_path, monkeypatch)
    assert ref.best_reference(_blocks(other), "notice_of_assessment") is None


def _store(reference: ref.ReferenceLayout, tmp_path, monkeypatch) -> None:
    folder = tmp_path / reference.document_type
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{reference.name}.json").write_text(reference.model_dump_json())
    monkeypatch.setattr(ref, "REFERENCES_DIR", tmp_path)
    ref.load_references.cache_clear()


@pytest.fixture
def stored(noa_reference, tmp_path, monkeypatch):
    _store(noa_reference, tmp_path, monkeypatch)
    yield noa_reference
    ref.load_references.cache_clear()


def _reference_findings(result) -> list:
    return [
        a
        for a in result.anomalies
        if any(s.get("kind") == "reference-position" for s in a.evidence.get("signals") or [a.evidence])
    ]


@pytest.mark.usefixtures("stored")
def test_confident_type_uses_the_stored_reference() -> None:
    data = _form("NG SIEW LAN", "7,250.00", move="NG SIEW LAN", dy=10)
    result = check_layout_anomalies(
        "doc-1", data, "notice_of_assessment", settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    assert [a.text for a in _reference_findings(result)] == ["NG SIEW LAN"]


@pytest.mark.usefixtures("stored")
def test_unsure_type_or_no_type_skips_the_reference() -> None:
    data = _form("NG SIEW LAN", "7,250.00", move="NG SIEW LAN", dy=10)
    unsure = settings.CLASSIFICATION_CONFIDENCE_THRESHOLD - 0.01
    assert _reference_findings(check_layout_anomalies("doc-1", data, "notice_of_assessment", unsure)) == []
    assert _reference_findings(check_layout_anomalies("doc-1", data)) == []


@pytest.fixture
def two_versions(noa_reference, tmp_path, monkeypatch):
    """A second version of the form that prints two labels 12 pt further right."""
    later = [
        _blocks(_form(name, income, move=("Tax payable", "Chargeable income"), dx=12))
        for name, income in [("TAN WEI MING", "481,600.00"), ("LIM HUI LING", "92,300.00")]
    ]
    _store(noa_reference, tmp_path, monkeypatch)
    _store(ref.build_reference("test_noa_v2", "notice_of_assessment", later), tmp_path, monkeypatch)
    yield
    ref.load_references.cache_clear()


@pytest.mark.usefixtures("two_versions")
def test_a_line_where_another_fitting_version_has_it_is_not_reported() -> None:
    # Fits both versions at 11/12: each has one of the two labels in its own place.
    data = _form("NG SIEW LAN", "7,250.00", move="Tax payable", dx=12)
    result = check_layout_anomalies(
        "doc-1", data, "notice_of_assessment", settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    assert _reference_findings(result) == []


def test_a_value_where_a_less_fitting_version_has_it_is_not_reported(noa_reference, tmp_path, monkeypatch) -> None:
    # A second version whose copies had the name a line lower (a name long enough to wrap) and one
    # label moved, so the document fits the first version best but only the second knows its name.
    lower = [
        _blocks(_form(name, income, move=(name, "Tax payable"), dy=10))
        for name, income in [("MUHAMMAD FAIZAL BIN RAHMAN", "481,600.00"), ("PRIYA D/O SUBRAMANIAM", "92,300.00")]
    ]
    _store(noa_reference, tmp_path, monkeypatch)
    _store(ref.build_reference("test_noa_long_names", "notice_of_assessment", lower), tmp_path, monkeypatch)
    data = _form("AHMAD SYAFIQ BIN OSMAN", "7,250.00", move="AHMAD SYAFIQ BIN OSMAN", dy=10)
    fitting = ref.fitting_references(_blocks(data), "notice_of_assessment")
    assert [c.reference.name for c in fitting] == ["test_noa", "test_noa_long_names"]
    result = check_layout_anomalies(
        "doc-1", data, "notice_of_assessment", settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    assert _reference_findings(result) == []
    ref.load_references.cache_clear()


@pytest.mark.usefixtures("two_versions")
def test_a_value_out_of_place_in_every_fitting_version_is_reported() -> None:
    # Labels are never reported against a reference (only values that moved on their own).
    data = _form("NG SIEW LAN", "7,250.00", move="NG SIEW LAN", dy=10)
    result = check_layout_anomalies(
        "doc-1", data, "notice_of_assessment", settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
    )
    assert [a.text for a in _reference_findings(result)] == ["NG SIEW LAN"]


def test_committed_references_load_and_have_no_value_text() -> None:
    ref.load_references.cache_clear()
    for path in ref.REFERENCES_DIR.rglob("*.json"):
        loaded = ref.ReferenceLayout.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert loaded.document_type == path.parent.name
        assert loaded.labels
        assert all(slot.text is None for slot in loaded.slots)


def test_section_pushed_down_by_a_longer_table_is_not_a_finding(noa_reference) -> None:
    comparison = _compare(
        noa_reference, _form("NG SIEW LAN", "7,250.00", push_below=SECTION_TWO, push=20)
    )
    assert comparison.fit == 1.0
    assert comparison.findings == []


def test_a_value_moved_on_its_own_is_reported_when_another_section_was_pushed(noa_reference) -> None:
    data = _form(
        "NG SIEW LAN", "7,250.00", push_below=SECTION_TWO, push=20, move="NG SIEW LAN", dy=10
    )
    comparison = _compare(noa_reference, data)
    assert comparison.fit == 1.0
    assert [f.text for f in comparison.findings] == ["NG SIEW LAN"]


def test_text_repeated_on_a_page_is_a_value_slot_not_a_label() -> None:
    repeated = [((400, 300), "Single"), ((400, 330), "Single")]
    copies = [
        _blocks(_form("TAN WEI MING", "481,600.00", extra=repeated)),
        _blocks(_form("LIM HUI LING", "92,300.00", extra=repeated)),
    ]
    built = ref.build_reference("test", "credit_report", copies)
    assert "single" not in {label.text for label in built.labels}


def test_a_label_matches_the_copy_that_sits_in_place(noa_reference) -> None:
    # An extra "Tax payable" above the real one must not take the real one's label.
    data = _form("NG SIEW LAN", "7,250.00", extra=[((72, 205), "Tax payable")])
    comparison = _compare(noa_reference, data)
    assert comparison.fit == 1.0


def _as_photo(blocks, zoom: float = 2.0, offset: tuple[float, float] = (40.0, 25.0)):
    """What a photo of the form looks like after OCR: zoomed, offset, no text baselines."""
    photo = []
    for block in blocks:
        lines = [
            line.model_copy(
                update={
                    "bbox": tuple(
                        v * zoom + offset[i % 2] for i, v in enumerate(line.bbox)
                    ),
                    "baseline": None,
                }
            )
            for line in block.lines
        ]
        photo.append(block.model_copy(update={"lines": lines}))
    return photo


def test_born_digital_copy_is_not_rescaled(noa_reference) -> None:
    lines = ref._doc_lines(_blocks(_form("NG SIEW LAN", "7,250.00")))
    assert ref._scale_and_shift(lines, noa_reference, False) == (1.0, 0.0, 0.0)


def test_photo_of_a_genuine_copy_fits_with_no_findings(noa_reference) -> None:
    photo = _as_photo(_blocks(_form("NG SIEW LAN", "7,250.00")))
    zoom = ref._scale_and_shift(ref._doc_lines(photo), noa_reference, True)[0]
    assert zoom == pytest.approx(2.0, abs=0.01)
    comparison = ref.compare_with_reference(photo, noa_reference)
    assert comparison.fit == 1.0
    assert comparison.findings == []


def test_moved_field_on_a_photo_is_reported_in_reference_points(noa_reference) -> None:
    photo = _as_photo(_blocks(_form("NG SIEW LAN", "7,250.00", move="NG SIEW LAN", dy=10)))
    comparison = ref.compare_with_reference(photo, noa_reference)
    assert [f.text for f in comparison.findings] == ["NG SIEW LAN"]
    # The photo is at 2x zoom; the shift is reported in the reference's points.
    assert comparison.findings[0].evidence["dy_pt"] == pytest.approx(10, abs=1)


def test_ocr_drops_lines_it_is_unsure_of(monkeypatch) -> None:
    """A misread crest motto scores low; printed text scores high."""
    import sys
    import types

    from app.fraud_detection.layout_anomalies.extraction import _ocr_blocks

    class FakeEngine:
        def __call__(self, image):
            return types.SimpleNamespace(
                boxes=[[[10, 10], [200, 10], [200, 30], [10, 30]], [[300, 10], [420, 10], [420, 24], [300, 24]]],
                txts=["IDENTITY CARD NO.", "MAJOLA INGCAPURA"],
                scores=[0.99, 0.67],
            )

    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=FakeEngine))
    doc = pymupdf.open()
    doc.new_page(width=500, height=300)
    try:
        texts = [line.text for block in _ocr_blocks(doc) for line in block.lines]
    finally:
        doc.close()
    assert texts == ["IDENTITY CARD NO."]


def _placed(y: float, page: int = 0) -> ref._Placed:
    line = _blocks(_form("NG SIEW LAN", "7,250.00"))[0].lines[0].model_copy(update={"page": page})
    return ref._Placed(block=None, line=line, x0=72.0, x1=200.0, y=y, text="x")  # type: ignore[arg-type]


def test_three_lines_off_by_the_same_amount_are_a_reflow_not_an_edit() -> None:
    section = [(_placed(300 + 14 * i), 0.0, -14.0, "label") for i in range(3)]
    assert all(ref._moved_together(p, dx, dy, section, 2.0) for p, dx, dy, _ in section)


def test_two_neighbouring_lines_off_by_the_same_amount_are_a_reflow() -> None:
    footer = [(_placed(760), 0.0, -48.8, "value"), (_placed(772), 0.0, -48.8, "value")]
    assert all(ref._moved_together(p, dx, dy, footer, 2.0) for p, dx, dy, _ in footer)


def test_one_moved_field_among_unrelated_offsets_is_still_an_edit() -> None:
    moved = (_placed(400), 12.0, 0.0, "label")
    others = [(_placed(100), 0.0, -14.0, "value"), (_placed(600), 0.0, 9.0, "value")]
    assert not ref._moved_together(moved[0], moved[1], moved[2], [moved, *others], 2.0)


def test_a_value_whose_neighbours_sit_in_place_stands_alone() -> None:
    value = _placed(400)
    assert ref._stands_alone(value, [(value, False), (_placed(380), True), (_placed(420), True)])


def test_a_value_moved_with_a_neighbour_is_a_reflow_whatever_the_amounts() -> None:
    # A long name wraps: the next line drops 14 pt, the one after it 28 pt. Neither is an edit.
    value = _placed(400)
    assert not ref._stands_alone(value, [(value, False), (_placed(380), True), (_placed(414), False)])


def test_a_value_with_no_compared_neighbours_is_not_reported() -> None:
    value = _placed(400)
    assert not ref._stands_alone(value, [(value, False), (_placed(500), True)])


def test_reference_findings_are_not_page_margin_findings() -> None:
    from app.fraud_detection.layout_anomalies import signals

    assert signals._is_page_wide("left-margin")
    assert signals._is_page_wide("header-position")
    assert not signals._is_page_wide("reference-position")


@pytest.mark.parametrize(
    ("text", "machine_readable"),
    [
        ("PASGP<<MEENA<DO<KRISHNAN<<<<<<<<<<<<<<<<<<<<", True),
        ("E7790962B7SGP8009205M3104161S8056387D<<<<<66", True),
        ("REPUBLIC OF SINGAPORE", False),
        ("Total << 5", False),
    ],
)
def test_passport_machine_readable_lines_are_not_compared(text, machine_readable) -> None:
    assert ref._is_machine_readable(text) is machine_readable


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("PHOTO", True),
        ("PLACEHOLDER", True),
        ("FINGERPRINT", True),
        ("Photo Placeholder", True),
        ("SIGNATURE PLACEHOLDER", True),
        ("PHOTO ID REQUIRED", False),
        ("Fingerprint scanned on 02-06-2021", False),
    ],
)
def test_placeholder_captions_are_not_compared(text, placeholder) -> None:
    assert ref._is_placeholder(text) is placeholder


def test_placeholder_caption_is_not_a_moved_value(noa_reference) -> None:
    data = _form("NG SIEW LAN", "7,250.00", extra=[((150, 200), "PHOTO"), ((140, 212), "PLACEHOLDER")])
    assert _compare(noa_reference, data).findings == []
