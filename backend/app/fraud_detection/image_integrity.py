# image_integrity.py
"""
Image Integrity — a plain-English report over the two image checks.

check_image_manipulation (SCRUM-64) and check_image_tampering (SCRUM-65) each
return measurement-level results: regions, tolerances, raw ratios. Those are
right for debugging and for the detection agent, but an officer running the
individual "Image integrity" check needs a verdict and what was found, in
words. This module builds that from the two results without re-running
anything; the raw results are returned alongside it unchanged.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .image_manipulation import (
    ImageManipulationIndicator,
    ImageManipulationResult,
    ImageRegion,
    ImageTamperingIndicator,
    ImageTamperingResult,
)

OverallStatus = Literal["clear", "flagged", "partially_assessed", "not_assessed"]


class IntegrityFinding(BaseModel):
    indicator: str
    severity: str
    where: str
    what_was_found: str
    measured: str
    expected: str
    difference: str


class ImageIntegrityReport(BaseModel):
    status: OverallStatus
    verdict: str
    summary: str
    file: str
    images: list[str]
    findings: list[IntegrityFinding]


# --- describing the file and its images ------------------------------------------

def _is_upload(result: ImageManipulationResult | ImageTamperingResult) -> bool:
    return result.bbox_units == "px"


def _kb(n_bytes: int) -> str:
    return f"{n_bytes / 1024:,.0f} KB"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _describe_file(manipulation: ImageManipulationResult) -> str:
    regions = manipulation.regions
    if _is_upload(manipulation) and regions:
        region = regions[0]
        dpi = f", claiming {region.effective_dpi:g} DPI" if region.effective_dpi else ""
        return (
            f"An image upload: a {region.pixel_width} × {region.pixel_height} px "
            f"{region.codec.upper()} ({_kb(region.stored_bytes)}{dpi})."
        )
    distinct = {r.xref for r in regions}
    comparable = {r.xref for r in regions if r.comparable}
    pages = manipulation.pages_checked
    if not distinct:
        return f"A PDF of {_plural(pages, 'page')} with no embedded images."
    return (
        f"A PDF of {_plural(pages, 'page')} with {_plural(len(distinct), 'distinct embedded image')}, "
        f"{len(comparable)} of them large enough to compare."
    )


def _describe_region(region: ImageRegion, manipulation: ImageManipulationResult) -> str:
    tolerances = manipulation.tolerances
    if _is_upload(manipulation):
        text = f"the whole upload, {region.pixel_width} × {region.pixel_height} px {region.codec.upper()}."
    else:
        text = (
            f"a {region.pixel_width} × {region.pixel_height} px {region.codec.upper()} "
            f"drawn at {region.effective_dpi:.0f} DPI."
        )

    notes: list[str] = []
    if region.detail_ratio is not None:
        if region.detail_ratio < tolerances.min_detail_ratio:
            notes.append(
                f"detail ratio {region.detail_ratio:.2f} is LOW — it holds less fine detail than its "
                f"size suggests, as if enlarged from something smaller (normal is {tolerances.min_detail_ratio:g} or more)"
            )
        else:
            notes.append(
                f"detail ratio {region.detail_ratio:.2f} is normal (an image enlarged from a smaller "
                f"source falls below {tolerances.min_detail_ratio:g})"
            )
    if region.covered_by_xref is not None:
        notes.append("hidden underneath another image — the original that image replaced, compared against it")
    elif not region.comparable and not _is_upload(manipulation):
        x0, y0, x1, y1 = region.bbox
        if region.pages_used_on > 1:
            notes.append(
                f"repeated on {region.pages_used_on} pages, so treated as template artwork and not compared"
            )
        elif (x1 - x0) * (y1 - y0) < tolerances.min_region_area_pt2:
            notes.append("logo or icon size, so not compared")
    if not notes:
        return text
    joined = "; ".join(notes)
    return f"{text} {joined[0].upper()}{joined[1:]}."


def _images(manipulation: ImageManipulationResult) -> list[str]:
    seen: set[int] = set()
    lines = []
    for region in manipulation.regions:
        if region.xref in seen and not _is_upload(manipulation):
            continue
        seen.add(region.xref)
        page = "" if _is_upload(manipulation) else f" (page {region.page + 1})"
        lines.append(f"Image {len(lines) + 1}{page}: {_describe_region(region, manipulation)}")
    return lines


def _where(page: int, bbox: tuple[float, float, float, float], upload: bool) -> str:
    x0, y0, x1, y1 = bbox
    if upload:
        return f"A {x1 - x0:.0f} × {y1 - y0:.0f} px area starting {x0:.0f} px from the left and {y0:.0f} px from the top."
    return f"Page {page + 1}, a {x1 - x0:.0f} × {y1 - y0:.0f} pt area {x0:.0f} pt from the left and {y0:.0f} pt from the top."


def _findings(
    indicators: list[ImageManipulationIndicator] | list[ImageTamperingIndicator], upload: bool
) -> list[IntegrityFinding]:
    return [
        IntegrityFinding(
            indicator=i.indicator,
            severity=i.severity,
            where=_where(i.page, i.bbox, upload),
            what_was_found=i.summary,
            measured=i.detected,
            expected=i.expected,
            difference=i.deviation,
        )
        for i in indicators
    ]


def build_image_integrity_report(
    manipulation: ImageManipulationResult, tampering: ImageTamperingResult
) -> ImageIntegrityReport:
    findings = _findings(manipulation.indicators, _is_upload(manipulation)) + _findings(
        tampering.indicators, _is_upload(tampering)
    )
    unable = [
        f"The {name} check could not run: {result.unable_to_assess_reason or 'no reason given.'}"
        for name, result in (("resolution and compression", manipulation), ("tampering", tampering))
        if result.unable_to_assess
    ]

    if findings:
        status: OverallStatus = "flagged"
        verdict = f"{_plural(len(findings), 'possible sign')} of image manipulation found"
        summary = " ".join(f.what_was_found for f in findings[:3]) + (
            f" ({len(findings) - 3} more in the findings.)" if len(findings) > 3 else ""
        )
    elif len(unable) == 2:
        status = "not_assessed"
        verdict = "Image integrity could not be checked"
        summary = " ".join(unable)
    elif unable:
        status = "partially_assessed"
        verdict = "No signs of manipulation found, but only part of the check could run"
        summary = f"{unable[0]} The other check ran and found nothing beyond its limits."
    else:
        status = "clear"
        verdict = "No signs of image manipulation found"
        summary = (
            "Both checks ran and nothing crossed their limits. That means nothing measurable stood "
            "out — not that the image is proven genuine."
        )

    return ImageIntegrityReport(
        status=status,
        verdict=verdict,
        summary=summary,
        file=_describe_file(manipulation),
        images=_images(manipulation),
        findings=findings,
    )
