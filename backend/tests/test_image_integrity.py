from __future__ import annotations

from app.fraud_detection.image_integrity import build_image_integrity_report
from app.fraud_detection.image_manipulation import (
    ImageManipulationResult,
    ImageRegion,
    ImageTamperingIndicator,
    ImageTamperingResult,
    TamperingTolerances,
    Tolerances,
)


def _region(**overrides) -> ImageRegion:
    values = dict(
        xref=5, page=0, bbox=(72.0, 72.0, 272.0, 305.0), pixel_width=300, pixel_height=350,
        effective_dpi=108.0, codec="png", stored_bytes=40_000, bytes_per_pixel=0.38,
        quantisation_signature=None, pages_used_on=1, detail_ratio=0.45, comparable=True,
    )
    values.update(overrides)
    return ImageRegion(**values)


def _manipulation(regions=(), unable: str | None = None, units="pt") -> ImageManipulationResult:
    return ImageManipulationResult(
        document_id="doc-1", regions=list(regions), indicators=[], pages_checked=1,
        tolerances=Tolerances(), unable_to_assess=unable is not None,
        unable_to_assess_reason=unable, bbox_units=units,
    )


def _tampering(indicators=(), unable: str | None = None, units="pt") -> ImageTamperingResult:
    return ImageTamperingResult(
        document_id="doc-1", regions=[], indicators=list(indicators), pages_checked=1,
        tolerances=TamperingTolerances(), unable_to_assess=unable is not None,
        unable_to_assess_reason=unable, bbox_units=units,
    )


def _scaling() -> ImageTamperingIndicator:
    return ImageTamperingIndicator(
        indicator="Scaling Anomaly", page=0, bbox=(72.0, 72.0, 472.0, 305.0),
        detected="54 DPI across, 108 DPI down", expected="the same resolution on both axes",
        deviation="stretched 2.0x wider than its pixels", severity="high",
        summary="Page 1: an image is drawn 2.0x out of its own proportions.", evidence={},
    )


def test_clean_upload_reads_as_clear_and_says_what_was_checked() -> None:
    upload = _region(bbox=(0.0, 0.0, 1576.0, 2100.0), pixel_width=1576, pixel_height=2100,
                     codec="jpeg", effective_dpi=72.0, stored_bytes=393_882, detail_ratio=0.30)
    report = build_image_integrity_report(_manipulation([upload], units="px"), _tampering(units="px"))

    assert report.status == "clear"
    assert report.verdict == "No signs of image manipulation found"
    assert "not that the image is proven genuine" in report.summary
    assert report.file == "An image upload: a 1576 × 2100 px JPEG (385 KB, claiming 72 DPI)."
    assert report.images == [
        "Image 1: the whole upload, 1576 × 2100 px JPEG. Detail ratio 0.30 is normal "
        "(an image enlarged from a smaller source falls below 0.18)."
    ]
    assert report.findings == []
    assert "SCRUM" not in report.model_dump_json()


def test_findings_are_spelled_out_with_location_and_comparison() -> None:
    report = build_image_integrity_report(_manipulation([_region()]), _tampering([_scaling()]))

    assert report.status == "flagged"
    assert report.verdict == "1 possible sign of image manipulation found"
    finding = report.findings[0]
    assert finding.indicator == "Scaling Anomaly"
    assert finding.where == "Page 1, a 400 × 233 pt area 72 pt from the left and 72 pt from the top."
    assert finding.measured == "54 DPI across, 108 DPI down"
    assert finding.expected == "the same resolution on both axes"
    assert report.summary.startswith("Page 1: an image is drawn 2.0x")


def test_one_check_unable_is_partially_assessed_not_clear() -> None:
    report = build_image_integrity_report(
        _manipulation([_region()], unable="Only 1 distinct image asset large enough to compare."),
        _tampering(),
    )

    assert report.status == "partially_assessed"
    assert report.summary.startswith(
        "The resolution and compression check could not run: Only 1 distinct image"
    )


def test_both_checks_unable_is_not_assessed() -> None:
    report = build_image_integrity_report(
        _manipulation(unable="No embedded images found."), _tampering(unable="No embedded images found."),
    )
    assert report.status == "not_assessed"
    assert report.file == "A PDF of 1 page with no embedded images."


def test_pdf_images_explain_why_some_are_not_compared() -> None:
    logo = _region(xref=9, bbox=(10.0, 10.0, 40.0, 40.0), comparable=False)
    template = _region(xref=11, pages_used_on=4, comparable=False)
    enlarged = _region(xref=12, detail_ratio=0.1)
    report = build_image_integrity_report(_manipulation([_region(), logo, template, enlarged]), _tampering())

    assert report.images[0].startswith("Image 1 (page 1): a 300 × 350 px PNG drawn at 108 DPI.")
    assert "logo or icon size, so not compared" in report.images[1]
    assert "repeated on 4 pages" in report.images[2]
    assert "Detail ratio 0.10 is LOW" in report.images[3]
