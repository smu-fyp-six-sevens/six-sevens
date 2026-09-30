# image_tampering.py
"""
Layout & Rendering Specialist — visible boundaries, scaling distortions,
overlays and brightness/colour inconsistencies in the images of a document
(SCRUM-65).

Its sibling, image_manipulation.py (SCRUM-64), asks whether an image was
produced at the same resolution and compression as the rest of the
document. This module asks whether an image was *edited into* the document
after the fact: painted over, stretched, rotated, or pasted with a seam or a
colour cast that the surrounding page does not share.

The most useful fact about an edited PDF is that editing rarely removes
anything. To change a logo or a crest, the original is painted out and a new
image is drawn on top — and the original stays in the file, underneath. When
that happens the check compares the visible image against the very image it
replaced, which says not only THAT the region changed but HOW: its shape
(Scaling Anomaly), its edges (Visible Boundary), or its brightness, contrast,
colour, sharpness and noise (Quality Mismatch). None of the 205 clean
documents this repo generates contains a single hidden image, so the pairing
itself carries no false alarms on that corpus.

Where there is no hidden original, the checks that need no comparison
partner still run: an image drawn stretched or rotated, and a halo or a
mismatched background patch around an image's edge as the page renders it.

Image/geometry based, no LLM tokens. Synchronous and CPU-bound — callers
should run this via asyncio.to_thread, the same as the other checks here.
"""
from __future__ import annotations

import io
import logging
import math
from typing import Literal

import numpy as np
import pymupdf
from PIL import Image, ImageFilter
from pydantic import BaseModel, Field

from .image_manipulation import (
    BBox,
    ImageRegion,
    Tolerances as ManipulationTolerances,
    _collect_regions,
    _is_raster,
)

logger = logging.getLogger("fraud_detection.image_tampering")

# AC6: the indicator recorded against each finding.
IndicatorType = Literal[
    "Visible Boundary", "Scaling Anomaly", "Image Overlay", "Quality Mismatch",
]
Severity = Literal["high", "medium", "low"]

# An image counts as painted out when one later drawing — a filled shape or
# another image — covers this much of it...
_MIN_HIDDEN_FRACTION = 0.9
# ...and is no more than this many times its area, so a page-sized
# background drawn over small artwork does not count. Same bounds as
# image_manipulation._hidden_placements.
_MAX_COVER_AREA_RATIO = 2.5
# A later image counts as the replacement when it overlaps this much of the
# hidden original's slot. Low on purpose: a stretched replacement anchored
# at the original's corner may only overlap half of it.
_MIN_REPLACEMENT_OVERLAP = 0.25
# A replacement this flat (grey-level standard deviation) is a patch of
# paper used to paint the original out, not a replacement for it.
_PAPER_PATCH_STD = 4.0
# Both images are reduced to this size before comparing content, which
# takes resolution out of the comparison: that is SCRUM-64's question.
_COMPARE_PX = 64
# Rendering resolution for the edge checks, and how far outside an image's
# edge to look for the page it sits on.
_EDGE_DPI = 150
_EDGE_MARGIN_PT = 6.0
# Below this many rendered pixels on a side, an edge band is too short to
# average.
_MIN_EDGE_PX = 16


class Tolerances(BaseModel):
    """
    AC7 — what counts as normal variation. Calibrated against the 205 clean
    documents in documents_generated and 260 injections of each SCRUM-65
    indicator by the repo's own injection tool.
    """

    # A replacement whose width-to-height ratio differs from the original's
    # by this factor was stretched or squashed. Genuine replacements of a
    # mark by a newer version keep its shape; the injection tool's stretch
    # changes it 4.1x.
    max_aspect_change: float = Field(default=1.2, gt=1.0)
    # Any image drawn with this much more horizontal than vertical
    # resolution (or the reverse) is being stretched by the page itself.
    # Every one of the 550 image placements in the clean corpus is within
    # 1.005.
    max_draw_anisotropy: float = Field(default=1.2, gt=1.0)
    # An image drawn at an angle further than this from upright. No image
    # in the clean corpus is drawn at any angle at all.
    max_rotation_deg: float = Field(default=2.0, ge=0.0)
    # Grey levels by which a replacement's outer edge band darkens relative
    # to the band just inside it, beyond what the original shows. Halo
    # injections score a median of 5.5; plain overlays at most 2.2.
    min_halo_vs_original: float = Field(default=3.0, gt=0.0)
    # The same halo measured on the rendered page without an original: the
    # image's edge band darker than both the page outside it and the image
    # inside it. Clean placements never score above 0.
    min_rendered_halo: float = Field(default=2.0, gt=0.0)
    # A flat edge band inside the image that differs from the flat page
    # just outside it by this many grey levels is a pasted patch whose
    # background does not match the page. Clean placements top out at 0.19.
    min_background_step: float = Field(default=3.0, gt=0.0)
    # A replacement at least this sharp relative to its original, or with
    # more fine-grain noise than it, has had its quality altered. A plain
    # re-render comes out no sharper than the original (1.00 at most across
    # halo and overlay injections); quality injections come out at 1.03 and
    # up.
    min_sharpness_ratio: float = Field(default=1.02, gt=0.0)
    # PNG/JPEG uploads: how many faint straight seams, running both ways,
    # must outline a region before it is reported (see _outlines). Two lets
    # 1 in 20 clean NRIC/passport uploads through; three lets none.
    min_outline_seams: int = Field(default=3, ge=2)


class ImageTamperingIndicator(BaseModel):
    indicator: IndicatorType  # AC6
    page: int  # AC5
    bbox: BBox  # AC5
    detected: str
    expected: str
    deviation: str
    severity: Severity
    summary: str
    evidence: dict


class ImageTamperingResult(BaseModel):
    document_id: str
    regions: list[ImageRegion]  # AC1
    indicators: list[ImageTamperingIndicator]
    pages_checked: int
    tolerances: Tolerances
    unable_to_assess: bool  # AC8
    unable_to_assess_reason: str | None = None
    # PDF points for a PDF, pixels for a PNG or JPEG upload.
    bbox_units: Literal["pt", "px"] = "pt"


def _unable(
    document_id: str, reason: str, tolerances: Tolerances,
    regions: list[ImageRegion] | None = None, pages: int = 0,
    bbox_units: Literal["pt", "px"] = "pt",
) -> ImageTamperingResult:
    return ImageTamperingResult(
        document_id=document_id,
        regions=regions or [],
        indicators=[],
        pages_checked=pages,
        tolerances=tolerances,
        unable_to_assess=True,
        unable_to_assess_reason=reason,
        bbox_units=bbox_units,
    )


def _dedupe(indicators: list[ImageTamperingIndicator]) -> list[ImageTamperingIndicator]:
    """One row per region per indicator type; the check that ran first wins."""
    seen: set[tuple[str, int, tuple[float, ...]]] = set()
    unique: list[ImageTamperingIndicator] = []
    for indicator in indicators:
        key = (indicator.indicator, indicator.page, tuple(round(v, 1) for v in indicator.bbox))
        if key not in seen:
            seen.add(key)
            unique.append(indicator)
    return unique


# --- PDF: finding what was painted over --------------------------------------

class _Placement(BaseModel):
    """One image as drawn, with where it falls in the page's drawing order."""

    order: int
    xref: int
    bbox: BBox
    transform: tuple[float, float, float, float, float, float]
    pixel_width: int
    pixel_height: int


class _Replacement(BaseModel):
    """A hidden original and whatever was drawn over its slot."""

    original: _Placement
    covered_by: list[str]  # "image" / "shape", for the evidence
    replacements: list[_Placement]


def _coverage(inner: pymupdf.Rect, outer: pymupdf.Rect) -> float:
    area = inner.get_area()
    return (inner & outer).get_area() / area if area > 0 else 0.0


def _placements(page: pymupdf.Page) -> tuple[list[_Placement], list[tuple[str, pymupdf.Rect]]]:
    """
    Every image placement on the page, tied to its position in the drawing
    order, plus the drawing log itself. get_bboxlog lists every fill and
    image in the order they are painted, which is the only way to tell
    which of two overlapping things is on top.
    """
    log = [(kind, pymupdf.Rect(rect)) for kind, rect in page.get_bboxlog()]
    placements: list[_Placement] = []
    used: set[int] = set()
    for info in page.get_image_info(xrefs=True):
        if not info.get("xref"):
            continue
        box = pymupdf.Rect(info["bbox"])
        for order, (kind, rect) in enumerate(log):
            if (
                kind == "fill-image" and order not in used
                and all(abs(a - b) < 0.6 for a, b in zip(rect, box))
            ):
                used.add(order)
                placements.append(_Placement(
                    order=order,
                    xref=info["xref"],
                    bbox=tuple(box),
                    transform=tuple(info["transform"]),
                    pixel_width=info["width"],
                    pixel_height=info["height"],
                ))
                break
    return placements, log


def _decode(doc: pymupdf.Document, xref: int, paper: tuple[int, int, int]) -> Image.Image | None:
    """
    An image as the page shows it: its soft mask applied over the paper.
    A transparent logo's raw pixels are often black where it is clear, so
    comparing raw pixels against a rendered replacement would compare black
    with paper.
    """
    try:
        info = doc.extract_image(xref)
        image = Image.open(io.BytesIO(info["image"]))
        image = image.convert("RGBA") if image.mode in ("RGBA", "LA", "P") else image.convert("RGB")
        smask = info.get("smask") or 0
        if smask:
            mask = Image.open(io.BytesIO(doc.extract_image(smask)["image"])).convert("L")
            if mask.size != image.size:
                mask = mask.resize(image.size, Image.BILINEAR)
            image = image.convert("RGBA")
            image.putalpha(mask)
    except Exception:
        logger.debug("xref %s could not be decoded", xref, exc_info=True)
        return None
    if image.mode == "RGBA":
        flat = Image.new("RGB", image.size, paper)
        flat.paste(image, mask=image.split()[3])
        image = flat
    return image.convert("RGB")


def _is_paper_patch(image: Image.Image) -> bool:
    return float(np.asarray(image.convert("L"), dtype=np.float32).std()) < _PAPER_PATCH_STD


def _find_replacements(page: pymupdf.Page) -> tuple[list[_Placement], list[_Replacement]]:
    """
    Images painted over by a later fill or image, each with the later
    images drawn over its slot. The paper-coloured patch an editor uses to
    blank the original is a cover, not a replacement.
    """
    placements, log = _placements(page)
    found: list[_Replacement] = []
    for original in placements:
        slot = pymupdf.Rect(original.bbox)
        if slot.is_empty:
            continue
        covered_by = [
            "image" if kind == "fill-image" else "shape"
            for order, (kind, rect) in enumerate(log)
            if order > original.order
            and kind in ("fill-image", "fill-path")
            and _coverage(slot, rect) >= _MIN_HIDDEN_FRACTION
            and rect.get_area() <= _MAX_COVER_AREA_RATIO * slot.get_area()
        ]
        if not covered_by:
            continue
        replacements = [
            later for later in placements
            if later.order > original.order
            and later.xref != original.xref
            and _coverage(slot, pymupdf.Rect(later.bbox)) >= _MIN_REPLACEMENT_OVERLAP
        ]
        found.append(_Replacement(original=original, covered_by=covered_by, replacements=replacements))
    return placements, found


# --- PDF: comparing a replacement with its original --------------------------

def _content_stats(image: Image.Image) -> dict[str, float]:
    """
    Brightness, contrast, colour, sharpness and noise of an image reduced
    to _COMPARE_PX, plus how much its outer edge band darkens relative to
    the band just inside it (a halo).
    """
    small = image.resize((_COMPARE_PX, _COMPARE_PX), Image.LANCZOS)
    rgb = np.asarray(small, dtype=np.float32)
    grey = rgb.mean(-1)
    saturation = np.asarray(small.convert("HSV"), dtype=np.float32)[..., 1]
    blurred = np.asarray(small.convert("L").filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    band = max(2, int(_COMPARE_PX * 0.07))
    edge = np.concatenate([
        grey[:band].ravel(), grey[-band:].ravel(), grey[:, :band].ravel(), grey[:, -band:].ravel(),
    ])
    inside = np.concatenate([
        grey[band:2 * band, band:-band].ravel(), grey[-2 * band:-band, band:-band].ravel(),
        grey[band:-band, band:2 * band].ravel(), grey[band:-band, -2 * band:-band].ravel(),
    ])
    return {
        "brightness": float(grey.mean()),
        "contrast": float(grey.std()),
        "saturation": float(saturation.mean()),
        "noise": float(np.abs(grey - blurred).mean()),
        "sharpness": float(
            np.abs(np.diff(grey, axis=1)).mean() + np.abs(np.diff(grey, axis=0)).mean()
        ),
        "halo": float(inside.mean() - edge.mean()),
    }


def _check_replacement(
    doc: pymupdf.Document, page_number: int, pair: _Replacement, tolerances: Tolerances,
) -> list[ImageTamperingIndicator]:
    original = pair.original
    slot = pymupdf.Rect(original.bbox)
    indicators: list[ImageTamperingIndicator] = []

    visible: list[tuple[_Placement, Image.Image]] = []
    for candidate in pair.replacements:
        image = _decode(doc, candidate.xref, (255, 255, 255))
        if image is not None and not _is_paper_patch(image):
            visible.append((candidate, image))

    # AC6 — Image Overlay. Always reported: the original was painted out,
    # which no document produced in one pass has any reason to do.
    shown = visible[0][0] if visible else None
    overlay_box = shown.bbox if shown else original.bbox
    indicators.append(ImageTamperingIndicator(
        indicator="Image Overlay",
        page=page_number,
        bbox=overlay_box,
        detected=(
            "an image drawn over another image in the same position" if shown
            else "an image painted out by a later fill"
        ),
        expected="each image region drawn once",
        deviation=f"original hidden under {', '.join(sorted(set(pair.covered_by)))}",
        severity="high",
        summary=(
            f"Page {page_number + 1}: the image originally in this position is still in "
            "the file but has been painted over"
            + (" and a different image drawn on top of it." if shown else ".")
            + " The region was edited after the document was produced."
        ),
        evidence={
            "hidden_xref": original.xref,
            "hidden_bbox": original.bbox,
            "covering_xref": shown.xref if shown else None,
            "covered_by": pair.covered_by,
        },
    ))
    if not visible:
        return indicators

    # AC3 — Scaling Anomaly: the replacement's shape against the original's.
    # Every image drawn over the slot is considered, and the most distorted
    # one reported: the patch used to blank the original can carry some
    # texture from the page beside it, and must not be mistaken for the
    # replacement just because it was drawn first.
    original_aspect = slot.width / slot.height

    def aspect_change_of(placement: _Placement) -> float:
        box = pymupdf.Rect(placement.bbox)
        aspect = box.width / box.height
        return max(aspect / original_aspect, original_aspect / aspect)

    replacement, replacement_image = max(visible, key=lambda item: aspect_change_of(item[0]))
    shown_box = pymupdf.Rect(replacement.bbox)
    shown_aspect = shown_box.width / shown_box.height
    aspect_change = aspect_change_of(replacement)
    logger.debug(
        "replacement: page=%s xref=%s over hidden xref=%s, aspect %.3f -> %.3f (%.2fx)",
        page_number, replacement.xref, original.xref, original_aspect, shown_aspect,
        aspect_change,
    )
    if aspect_change >= tolerances.max_aspect_change:
        wider = shown_aspect > original_aspect
        indicators.append(ImageTamperingIndicator(
            indicator="Scaling Anomaly",
            page=page_number,
            bbox=replacement.bbox,
            detected=f"width-to-height {shown_aspect:.2f}",
            expected=f"{original_aspect:.2f} (the original hidden underneath it)",
            deviation=f"{aspect_change:.1f}x {'wider' if wider else 'taller'} than the original",
            severity="high",
            summary=(
                f"Page {page_number + 1}: an image was replaced by a copy stretched "
                f"{aspect_change:.1f}x out of proportion — the original, still in the file "
                f"underneath, is {original_aspect:.2f} wide for every unit of height; the "
                f"visible image is {shown_aspect:.2f}."
            ),
            evidence={
                "xref": replacement.xref,
                "hidden_xref": original.xref,
                "aspect_ratio": round(shown_aspect, 3),
                "hidden_aspect_ratio": round(original_aspect, 3),
                "change": round(aspect_change, 2),
                "tolerance": tolerances.max_aspect_change,
            },
        ))
        # A stretched copy no longer lines up with the original, so its
        # content cannot be compared pixel for pixel.
        return indicators

    paper = replacement_image.getpixel((0, 0))
    original_image = _decode(doc, original.xref, paper if isinstance(paper, tuple) else (255, 255, 255))
    if original_image is None:
        return indicators
    now = _content_stats(replacement_image)
    before = _content_stats(original_image)
    halo_gain = now["halo"] - before["halo"]
    sharpness_ratio = now["sharpness"] / max(before["sharpness"], 0.5)
    noise_gain = now["noise"] - before["noise"]
    logger.debug(
        "replacement: xref=%s halo %+.2f, sharpness x%.2f, noise %+.2f, brightness %+.1f, "
        "contrast x%.2f, saturation %+.1f",
        replacement.xref, halo_gain, sharpness_ratio, noise_gain,
        now["brightness"] - before["brightness"],
        now["contrast"] / max(before["contrast"], 0.5), now["saturation"] - before["saturation"],
    )
    stats_evidence = {
        "xref": replacement.xref,
        "hidden_xref": original.xref,
        "brightness_change": round(now["brightness"] - before["brightness"], 2),
        "contrast_ratio": round(now["contrast"] / max(before["contrast"], 0.5), 3),
        "saturation_change": round(now["saturation"] - before["saturation"], 2),
        "sharpness_ratio": round(sharpness_ratio, 3),
        "noise_change": round(noise_gain, 3),
        "halo_change": round(halo_gain, 2),
    }

    # AC2 — Visible Boundary: a darkened rim the original did not have.
    if halo_gain >= tolerances.min_halo_vs_original:
        indicators.append(ImageTamperingIndicator(
            indicator="Visible Boundary",
            page=page_number,
            bbox=replacement.bbox,
            detected=f"edge band {halo_gain:.1f} grey levels darker than the original's",
            expected=f"under {tolerances.min_halo_vs_original:.1f}",
            deviation="a rectangular halo around the pasted region",
            severity="high",
            summary=(
                f"Page {page_number + 1}: the image drawn over the original has a darkened "
                "rim along its edges that the original does not — the halo left when a "
                "region is cut out and pasted back in."
            ),
            evidence={**stats_evidence, "tolerance": tolerances.min_halo_vs_original},
        ))

    # AC4 — Quality Mismatch: sharper or noisier than what it replaced.
    # Re-rendering an image only ever softens it, so a replacement that
    # comes out crisper or grainier was worked on.
    if sharpness_ratio >= tolerances.min_sharpness_ratio or noise_gain >= 0:
        changes = []
        if sharpness_ratio >= tolerances.min_sharpness_ratio:
            changes.append(f"sharpness x{sharpness_ratio:.2f}")
        if noise_gain >= 0:
            changes.append(f"fine-grain noise {noise_gain:+.2f}")
        changes.append(f"brightness {stats_evidence['brightness_change']:+.1f}")
        changes.append(f"contrast x{stats_evidence['contrast_ratio']:.2f}")
        changes.append(f"saturation {stats_evidence['saturation_change']:+.1f}")
        indicators.append(ImageTamperingIndicator(
            indicator="Quality Mismatch",
            page=page_number,
            bbox=replacement.bbox,
            detected=", ".join(changes),
            expected=(
                f"sharpness under x{tolerances.min_sharpness_ratio:.2f} and no added noise, "
                "as for a plain copy of the original"
            ),
            deviation="brightness, contrast, colour, sharpness or noise altered",
            severity="medium",
            summary=(
                f"Page {page_number + 1}: the image drawn over the original is sharper or "
                "grainier than the original it hides, which a plain copy never is — its "
                "brightness, contrast, colour or noise has been adjusted."
            ),
            evidence={**stats_evidence, "tolerance": tolerances.min_sharpness_ratio},
        ))
    return indicators


# --- PDF: checks that need no original --------------------------------------

def _check_geometry(page_number: int, placement: _Placement, tolerances: Tolerances) -> list[ImageTamperingIndicator]:
    """AC3 — an image the page itself draws stretched or rotated."""
    a, b, c, d, _, _ = placement.transform
    width_pt, height_pt = math.hypot(a, b), math.hypot(c, d)
    if width_pt < 1 or height_pt < 1:
        return []
    indicators: list[ImageTamperingIndicator] = []

    dpi_x = placement.pixel_width / (width_pt / 72.0)
    dpi_y = placement.pixel_height / (height_pt / 72.0)
    anisotropy = max(dpi_x, dpi_y) / min(dpi_x, dpi_y)
    if anisotropy >= tolerances.max_draw_anisotropy:
        wider = dpi_x < dpi_y  # fewer pixels per inch across = stretched across
        indicators.append(ImageTamperingIndicator(
            indicator="Scaling Anomaly",
            page=page_number,
            bbox=placement.bbox,
            detected=f"{dpi_x:.0f} DPI across, {dpi_y:.0f} DPI down",
            expected="the same resolution on both axes",
            deviation=f"stretched {anisotropy:.1f}x {'wider' if wider else 'taller'} than its pixels",
            severity="high",
            summary=(
                f"Page {page_number + 1}: an image is drawn {anisotropy:.1f}x out of its own "
                "proportions — the page stretches it further in one direction than the "
                "other, distorting what it shows."
            ),
            evidence={
                "xref": placement.xref,
                "dpi_x": round(dpi_x, 1),
                "dpi_y": round(dpi_y, 1),
                "anisotropy": round(anisotropy, 3),
                "tolerance": tolerances.max_draw_anisotropy,
            },
        ))

    angle = math.degrees(math.atan2(b, a)) % 90.0
    off_axis = min(angle, 90.0 - angle)
    if off_axis > tolerances.max_rotation_deg:
        indicators.append(ImageTamperingIndicator(
            indicator="Scaling Anomaly",
            page=page_number,
            bbox=placement.bbox,
            detected=f"rotated {off_axis:.1f} degrees off the page axis",
            expected=f"within {tolerances.max_rotation_deg:.0f} degrees of upright",
            deviation="rotated",
            severity="medium",
            summary=(
                f"Page {page_number + 1}: an image is drawn tilted {off_axis:.1f} degrees "
                "against the page, which a generated document has no reason to do."
            ),
            evidence={
                "xref": placement.xref,
                "rotation_deg": round(off_axis, 2),
                "tolerance": tolerances.max_rotation_deg,
            },
        ))
    return indicators


def _edge_bands(page: pymupdf.Page, bbox: BBox) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]] | None:
    """
    For each side of an image as rendered: the page just outside it, the
    image's own edge band, and the band just inside that. None when the
    image is too small or too close to the page edge to have all three.
    """
    rect = pymupdf.Rect(bbox)
    clip = pymupdf.Rect(
        rect.x0 - _EDGE_MARGIN_PT, rect.y0 - _EDGE_MARGIN_PT,
        rect.x1 + _EDGE_MARGIN_PT, rect.y1 + _EDGE_MARGIN_PT,
    ) & page.rect
    try:
        pix = page.get_pixmap(dpi=_EDGE_DPI, clip=clip, alpha=False)
    except Exception:
        return None
    grey = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[..., :3].mean(-1)
    scale = _EDGE_DPI / 72.0
    x0, y0 = int((rect.x0 - clip.x0) * scale), int((rect.y0 - clip.y0) * scale)
    x1, y1 = int((rect.x1 - clip.x0) * scale), int((rect.y1 - clip.y0) * scale)
    if (
        x1 - x0 < _MIN_EDGE_PX or y1 - y0 < _MIN_EDGE_PX
        or x0 < 4 or y0 < 4 or x1 > grey.shape[1] - 4 or y1 > grey.shape[0] - 4
    ):
        return None
    # The pixel row straddling the edge itself is anti-aliased, a blend of
    # image and page, so each band skips it: outside starts one pixel out,
    # the image's own edge band one pixel in.
    return [
        (grey[y0 - 4:y0 - 1, x0:x1], grey[y0 + 1:y0 + 4, x0:x1], grey[y0 + 5:y0 + 9, x0:x1]),
        (grey[y1 + 1:y1 + 4, x0:x1], grey[y1 - 4:y1 - 1, x0:x1], grey[y1 - 9:y1 - 5, x0:x1]),
        (grey[y0:y1, x0 - 4:x0 - 1], grey[y0:y1, x0 + 1:x0 + 4], grey[y0:y1, x0 + 5:x0 + 9]),
        (grey[y0:y1, x1 + 1:x1 + 4], grey[y0:y1, x1 - 4:x1 - 1], grey[y0:y1, x1 - 9:x1 - 5]),
    ]


def _check_rendered_edges(
    page: pymupdf.Page, placement: _Placement, tolerances: Tolerances,
) -> list[ImageTamperingIndicator]:
    """
    AC2 — a seam around an image as the page renders it, with nothing to
    compare it against. Two kinds, each judged on the median of the four
    sides so that one busy edge cannot decide it:

      halo:       the image's edge band darker than BOTH the page outside
                  and the image inside — a rim, not an edge.
      background: a flat edge band that differs from the flat page just
                  outside it — a pasted rectangle whose background does not
                  match the paper. A photo or a logo on a transparent
                  background has no flat band that disagrees with the page.
    """
    bands = _edge_bands(page, placement.bbox)
    if bands is None:
        return []
    halos, steps = [], []
    for outside, rim, inside in bands:
        halos.append(min(outside.mean() - rim.mean(), inside.mean() - rim.mean()))
        flat = outside.std() < 3 and rim.std() < 3
        steps.append(abs(outside.mean() - rim.mean()) if flat else 0.0)
    halo, step = float(np.median(halos)), float(np.median(steps))
    logger.debug(
        "edges: page=%s xref=%s halo=%.2f background step=%.2f",
        page.number, placement.xref, halo, step,
    )
    evidence = {
        "xref": placement.xref,
        "rendered_halo": round(halo, 2),
        "background_step": round(step, 2),
        "render_dpi": _EDGE_DPI,
    }
    if halo >= tolerances.min_rendered_halo:
        return [ImageTamperingIndicator(
            indicator="Visible Boundary",
            page=page.number,
            bbox=placement.bbox,
            detected=f"edge band {halo:.1f} grey levels darker than both sides of it",
            expected=f"under {tolerances.min_rendered_halo:.1f}",
            deviation="a rectangular halo around the image",
            severity="medium",
            summary=(
                f"Page {page.number + 1}: an image is framed by a darkened rim along its "
                "edges, darker than both the page around it and its own content — the halo "
                "left when a region is cut out and pasted in."
            ),
            evidence={**evidence, "tolerance": tolerances.min_rendered_halo},
        )]
    if step >= tolerances.min_background_step:
        return [ImageTamperingIndicator(
            indicator="Visible Boundary",
            page=page.number,
            bbox=placement.bbox,
            detected=f"image background {step:.1f} grey levels off the page around it",
            expected=f"under {tolerances.min_background_step:.1f}",
            deviation="a pasted rectangle whose background does not match the page",
            severity="medium",
            summary=(
                f"Page {page.number + 1}: an image sits in a flat rectangle of background that "
                "does not match the paper around it, leaving a visible box around the region."
            ),
            evidence={**evidence, "tolerance": tolerances.min_background_step},
        )]
    return []


def _check_pdf(
    document_id: str, file_bytes: bytes, tolerances: Tolerances,
) -> ImageTamperingResult:
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        reason = f"Failed to open document as a PDF: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _unable(document_id, reason, tolerances)

    try:
        regions = _collect_regions(doc, ManipulationTolerances())  # AC1
        pages = doc.page_count
        if not regions:
            return _unable(
                document_id,
                f"No embedded images found across {pages} page(s), so there are no image "
                "regions to examine.",
                tolerances, regions, pages,
            )

        indicators: list[ImageTamperingIndicator] = []
        for page in doc:
            placements, pairs = _find_replacements(page)
            hidden = {pair.original.order for pair in pairs}
            # Replacements first: comparing against the original is the
            # strongest evidence, so it is the explanation _dedupe keeps.
            for pair in pairs:
                indicators += _check_replacement(doc, page.number, pair, tolerances)
            for placement in placements:
                if placement.order in hidden:
                    continue  # not visible, so it has no rendered edge
                indicators += _check_geometry(page.number, placement, tolerances)
                indicators += _check_rendered_edges(page, placement, tolerances)

        return ImageTamperingResult(
            document_id=document_id,
            regions=regions,
            indicators=_dedupe(indicators),
            pages_checked=pages,
            tolerances=tolerances,
            unable_to_assess=False,
        )
    finally:
        doc.close()


# --- standalone PNG / JPEG uploads --------------------------------------------
#
# A flat image has no drawing order and no hidden originals, so the only
# evidence is in the pixels. What pasting a patch into a picture leaves
# behind, whatever was done to the patch, is its outline: a straight edge
# where the patch's background meets the page's, one or two grey levels
# apart, with flat colour on both sides. Genuine documents have straight
# edges too — table rules, panels, the white band across an NRIC — but
# those are either strong (a drawn border) or long (a band across the whole
# card). A patch's outline is weak and short, and it turns corners.
#
# Measured on 20 NRIC/passport uploads and 183 images rendered from the
# generated documents: no NRIC/passport false alarms and 2 of 183 rendered
# (1%); 82% of injected halo patches and 64% of overlay patches found.
# Stretching or re-grading a region INSIDE a flat image, with no seam to
# show for it, is not detected: every noise and edge-sharpness measure
# tried flagged clean card photos and watermarks more readily than
# injected patches.

# A seam is a step of this many grey levels between two pixels...
_SEAM_MIN_STEP = 3.0
_SEAM_MAX_STEP = 40.0
# ...with the pixel either side moving by no more than this, so the step
# separates two flat areas rather than sitting inside text or artwork.
_SEAM_FLAT = 2.0
# Steps continuing in a straight line for at least this many pixels, with
# gaps of up to _SEAM_GAP, form one seam.
_SEAM_MIN_LENGTH = 16
_SEAM_GAP = 2
# A seam longer than this share of the image side is a design line (a band
# or a panel edge), not a patch outline.
_SEAM_MAX_FRACTION = 0.45
# Seams within this many pixels of each other belong to one outline.
# How many an outline needs is Tolerances.min_outline_seams.
_SEAM_NEAR = 16


def _seams(gray: np.ndarray) -> list[tuple[str, int, int, int, float]]:
    """
    Straight runs of weak steps between flat areas, as
    (orientation, position, start, end, mean step). "v" seams run down the
    image at a column; "h" seams run across it at a row.
    """
    height, width = gray.shape
    found: list[tuple[str, int, int, int, float]] = []
    for orientation, plane in (("v", gray), ("h", gray.T)):
        diff = np.diff(plane, axis=1)
        size = np.abs(diff)
        flat_before = np.zeros_like(size, dtype=bool)
        flat_after = np.zeros_like(size, dtype=bool)
        flat_before[:, 1:] = size[:, :-1] <= _SEAM_FLAT
        flat_after[:, :-1] = size[:, 1:] <= _SEAM_FLAT
        step = (size >= _SEAM_MIN_STEP) & (size <= _SEAM_MAX_STEP) & flat_before & flat_after
        limit = _SEAM_MAX_FRACTION * (height if orientation == "v" else width)
        for column in np.nonzero(step.sum(axis=0) >= _SEAM_MIN_LENGTH)[0]:
            rows = np.nonzero(step[:, column])[0]
            breaks = np.nonzero(np.diff(rows) > _SEAM_GAP + 1)[0]
            starts = np.r_[rows[0], rows[breaks + 1]]
            ends = np.r_[rows[breaks], rows[-1]]
            for start, end in zip(starts, ends):
                length = end - start + 1
                if length < _SEAM_MIN_LENGTH or length > limit:
                    continue
                values = diff[start:end + 1, column][step[start:end + 1, column]]
                # One consistent direction: the same background on each side
                # all the way along, not texture flickering both ways.
                if abs(np.sign(values).mean()) > 0.8:
                    found.append((
                        orientation, int(column), int(start), int(end),
                        float(np.abs(values).mean()),
                    ))
    return found


def _outlines(
    seams: list[tuple[str, int, int, int, float]], min_seams: int,
) -> list[tuple[BBox, int, float]]:
    """Nearby seams merged into outlines: (bbox, seam count, mean step)."""
    groups: list[tuple[list[float], int, set[str], list[float]]] = []
    for orientation, position, start, end, step in seams:
        box = (
            [position, start, position, end] if orientation == "v"
            else [start, position, end, position]
        )
        groups.append((list(map(float, box)), 1, {orientation}, [step]))
    merged = True
    while merged:
        merged = False
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                a, b = groups[i][0], groups[j][0]
                if (
                    b[0] > a[2] + _SEAM_NEAR or a[0] > b[2] + _SEAM_NEAR
                    or b[1] > a[3] + _SEAM_NEAR or a[1] > b[3] + _SEAM_NEAR
                ):
                    continue
                groups[i] = (
                    [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])],
                    groups[i][1] + groups[j][1],
                    groups[i][2] | groups[j][2],
                    groups[i][3] + groups[j][3],
                )
                del groups[j]
                merged = True
                break
            if merged:
                break
    return [
        ((box[0], box[1], box[2], box[3]), count, float(np.mean(steps)))
        for box, count, orientations, steps in groups
        if count >= min_seams and len(orientations) == 2
        and box[2] - box[0] >= 12 and box[3] - box[1] >= 12
    ]


def _check_raster(
    document_id: str, file_bytes: bytes, tolerances: Tolerances,
) -> ImageTamperingResult:
    try:
        with Image.open(io.BytesIO(file_bytes)) as opened:
            codec = (opened.format or "unknown").lower()
            gray = np.asarray(opened.convert("L"), dtype=np.float32)
    except Exception as exc:
        reason = f"Failed to decode the image: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _unable(document_id, reason, tolerances, bbox_units="px")

    height, width = gray.shape
    region = ImageRegion(
        xref=0, page=0, bbox=(0.0, 0.0, float(width), float(height)),
        pixel_width=width, pixel_height=height, effective_dpi=0.0, codec=codec,
        stored_bytes=len(file_bytes),
        bytes_per_pixel=round(len(file_bytes) / (width * height), 5),
        quantisation_signature=None, detail_ratio=None, pages_used_on=1, comparable=True,
    )
    if min(width, height) < 4 * _SEAM_MIN_LENGTH:
        return _unable(
            document_id,
            f"Image is {width}x{height} px; too small to trace region outlines in.",
            tolerances, [region], 1, bbox_units="px",
        )

    indicators: list[ImageTamperingIndicator] = []
    for bbox, count, step in _outlines(_seams(gray), tolerances.min_outline_seams):
        logger.debug("raster outline: %s from %d seams, step %.1f", bbox, count, step)
        indicators.append(ImageTamperingIndicator(
            indicator="Visible Boundary",
            page=0,
            bbox=bbox,
            detected=f"{count} straight seams of {step:.1f} grey levels outlining a region",
            expected="no outline between areas of the same background",
            deviation="a pasted rectangle whose background does not match the page",
            severity="medium",
            summary=(
                f"A {int(bbox[2] - bbox[0])}x{int(bbox[3] - bbox[1])} px region of this image "
                "is outlined by faint straight edges where its background meets the "
                "surrounding page — the seam left when a patch is pasted into a picture."
            ),
            evidence={
                "seams": count, "mean_step": round(step, 2),
                "min_seams": tolerances.min_outline_seams,
            },
        ))
    return ImageTamperingResult(
        document_id=document_id,
        regions=[region],
        indicators=_dedupe(indicators),
        pages_checked=1,
        tolerances=tolerances,
        unable_to_assess=False,
        bbox_units="px",
    )


# --- entry point -------------------------------------------------------------

def check_image_tampering(
    document_id: str,
    file_bytes: bytes,
    tolerances: Tolerances | None = None,
) -> ImageTamperingResult:
    """
    Inspects a PDF's images, or a standalone PNG/JPEG upload, for visible
    boundaries, scaling distortions, overlays and quality inconsistencies.
    No LLM tokens.
    Synchronous/CPU-bound — callers should run this via asyncio.to_thread.
    """
    tolerances = tolerances or Tolerances()
    if _is_raster(file_bytes):
        return _check_raster(document_id, file_bytes, tolerances)
    return _check_pdf(document_id, file_bytes, tolerances)
