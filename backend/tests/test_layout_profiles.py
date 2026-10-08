"""Per-document-type layout profiles: when they apply and what they drop."""

from __future__ import annotations

import pymupdf
import pytest

from app.config import settings
from app.fraud_detection import layout_anomalies as lr
from app.fraud_detection.layout_anomalies import check_layout_anomalies
from app.fraud_detection.layout_anomalies.profiles import PROFILES, profile_for

CONFIDENT = settings.CLASSIFICATION_CONFIDENCE_THRESHOLD
UNSURE = settings.CLASSIFICATION_CONFIDENCE_THRESHOLD - 0.01


def _finding(evidence: dict, text: str = "NRIC No.S8012345A") -> lr.LayoutAnomaly:
    return lr.LayoutAnomaly(
        anomaly_type="Indentation",
        page=1,
        location="Cover title near y=523",
        bbox=(215.6, 522.9, 337.6, 533.9),
        text=text,
        detected="215.6 pt",
        expected="238 pt",
        deviation="-22.4 pt",
        confidence=0.63,
        evidence=evidence,
        summary="placeholder",
    )


def _evidence(comparable: int, matching: int, kind: str = "indentation") -> dict:
    return {"kind": kind, "comparable_count": comparable, "matching_count": matching}


@pytest.mark.parametrize(
    ("document_type", "confidence"),
    [(None, 1.0), ("nric", None), ("nric", UNSURE), ("payslip", 1.0), ("unrecognised", 1.0)],
)
def test_no_profile_without_a_confident_known_type(document_type, confidence) -> None:
    assert profile_for(document_type, confidence) is None


def test_confident_nric_gets_its_profile() -> None:
    assert profile_for("nric", CONFIDENT) is PROFILES["nric"]


def test_nric_drops_an_indent_measured_against_one_other_line() -> None:
    assert PROFILES["nric"].explains(_finding(_evidence(comparable=2, matching=1)))


@pytest.mark.parametrize(
    "evidence",
    [
        _evidence(comparable=3, matching=2),
        _evidence(comparable=2, matching=2),
        _evidence(comparable=2, matching=1, kind="vertical"),
        _evidence(comparable=2, matching=1, kind="right-edge"),
        {"kind": "text-overlay"},
    ],
)
def test_nric_keeps_everything_else(evidence) -> None:
    assert not PROFILES["nric"].explains(_finding(evidence))


BODY = (
    "The registered office of the Company shall be at such place in Singapore "
    "as the directors may from time to time determine."
)


def _pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 90, 540, 160), BODY, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def one_line_indent(monkeypatch):
    """The indentation check reports one finding measured against a single other line."""

    def fake_check(blocks, groups=None, **_):
        block = blocks[0]
        finding = lr._anomaly(
            "Indentation",
            block,
            detected="215.6 pt",
            expected="238 pt",
            baseline=None,
            deviation="-22.4 pt",
            confidence=0.9,
            evidence={
                "kind": "indentation",
                "side": "left",
                "left_delta_pt": -22.4,
                "comparable_count": 2,
                "matching_count": 1,
            },
        )
        return [finding], lr._assessed()

    monkeypatch.setattr(lr, "_check_indentation", fake_check)


def _indents(result) -> list:
    return [a for a in result.anomalies if a.anomaly_type == "Indentation"]


@pytest.mark.usefixtures("one_line_indent")
def test_finding_is_reported_without_a_type() -> None:
    assert len(_indents(check_layout_anomalies("doc-1", _pdf()))) == 1


@pytest.mark.usefixtures("one_line_indent")
def test_confident_nric_profile_drops_the_finding() -> None:
    result = check_layout_anomalies("doc-1", _pdf(), "nric", CONFIDENT)
    assert _indents(result) == []


@pytest.mark.usefixtures("one_line_indent")
def test_unsure_classification_keeps_the_finding() -> None:
    result = check_layout_anomalies("doc-1", _pdf(), "nric", UNSURE)
    assert len(_indents(result)) == 1


@pytest.mark.usefixtures("one_line_indent")
def test_other_types_keep_the_finding() -> None:
    result = check_layout_anomalies("doc-1", _pdf(), "payslip", 1.0)
    assert len(_indents(result)) == 1


def test_proof_of_address_drops_a_chart_axis_label() -> None:
    label = _finding({"kind": "indentation", "unanchored": True}, text="MAR")
    assert PROFILES["proof_of_address"].explains(label)


@pytest.mark.parametrize(
    ("evidence", "text"),
    [
        ({"kind": "indentation", "unanchored": True}, "19 AUG"),
        ({"kind": "indentation"}, "MAR"),
        ({"kind": "vertical", "unanchored": True}, "MAR"),
    ],
)
def test_proof_of_address_keeps_moved_fields(evidence, text) -> None:
    assert not PROFILES["proof_of_address"].explains(_finding(evidence, text=text))
