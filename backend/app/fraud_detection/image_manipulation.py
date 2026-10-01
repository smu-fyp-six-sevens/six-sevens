# image_manipulation.py
"""
Digital Authenticity specialist (detection agent tool: image_manipulation_tool) — resolution and compression inconsistencies
between the images embedded in one document.

A document assembled honestly is internally consistent: every image in it
went through the same scanner, the same export, the same compression pass.
A region that was pasted in from somewhere else usually did not, and that
shows up in two measurable ways — the pixels-per-inch it is actually drawn
at, and the way it was compressed.

Neither signal means anything in isolation. A 600 DPI logo beside a 150 DPI
photograph is an ordinary document, not a forgery, so every check here is
comparative: a region is only reported when it disagrees with the rest of
the SAME document by more than a configured tolerance (AC6). Where there is
nothing to compare against, the check says so rather than guessing (AC7).

Image/geometry based, no LLM tokens. Synchronous and CPU-bound — callers
should run this via asyncio.to_thread, the same as the other checks here.
"""
from __future__ import annotations

import hashlib
import io
import logging
from statistics import median
from typing import Literal

import numpy as np
import pymupdf
from PIL import Image, ImageChops, ImageStat
from pydantic import BaseModel, Field

logger = logging.getLogger("fraud_detection.image_manipulation")

BBox = tuple[float, float, float, float]
# AC5: the indicator recorded against each finding.
IndicatorType = Literal["Resolution Mismatch", "Compression Mismatch"]
Severity = Literal["high", "medium", "low"]

# JPEG segment markers used by _jpeg_quantisation_tables.
_JPEG_SOI = 0xD8
_JPEG_SOS = 0xDA
_JPEG_DQT = 0xDB
_JPEG_STANDALONE = {0x01, *range(0xD0, 0xD8)}

# Codecs whose stored size reflects a QUALITY setting rather than image
# content. Only these are eligible for the bytes-per-pixel comparison —
# see _check_compression, signal 3.
_LOSSY_CODECS = frozenset({"jpeg", "jpg", "jpx", "jp2", "jxr"})

# Round-trip factors for the detail measurement in _detail_ratio: one octave
# down, and three. 2 is the finest scale that can be measured at all; 8 is
# coarse enough that even a heavily enlarged region still holds real detail
# there, which is what makes the two comparable.
_DETAIL_FINE_FACTOR = 2
_DETAIL_COARSE_FACTOR = 8
# Below this, dividing by 8 leaves too few pixels for the coarse measurement
# to mean anything.
_MIN_DETAIL_PIXELS = 64

# Below this many comparable assets, a median is too easily moved by the very
# region being judged: altering one of three values shifts the midpoint enough
# that a different, untouched region becomes the most distant from it. The
# finding is still real — the document IS internally inconsistent — but which
# region is the altered one stops being knowable from resolution alone, so it
# is reported as an inconsistency across a named set rather than as an
# accusation against one region. Four is the smallest count at which two
# regions can agree and still outvote a third.
_MIN_CONFIDENT_ATTRIBUTION = 4

# An image counts as hidden when a later image covers this much of it...
_MIN_HIDDEN_FRACTION = 0.9
# ...and the covering image is no more than this many times its area. Above
# that the later image is a panel or a page-sized background drawn over
# small artwork, not a replacement for it. A replacement is drawn over the
# same slot, give or take the few points of padding needed to hide the
# original's edges. None of the 205 clean documents in this repo's corpus
# has any pair that meets both conditions.
_MAX_COVER_AREA_RATIO = 2.5


class Tolerances(BaseModel):
    """
    AC6 — what counts as normal variation.

    Defaults are deliberately loose. Scanning, photocopying, resizing and
    ordinary PDF generation all shift these numbers around, and a check that
    an officer learns to ignore is worse than no check, so each threshold is
    set where an innocent explanation stops being plausible rather than
    where a difference first becomes measurable.
    """

    # A region must differ from the document's baseline by this FACTOR, not
    # this margin. 2.0 = at least double or half the surrounding resolution;
    # resizing a photo to fit a template rarely crosses that on its own.
    dpi_ratio: float = Field(default=2.0, gt=1.0)
    # Same idea for stored bytes per pixel, wider because compression varies
    # far more than geometry across legitimate sources.
    bytes_per_pixel_ratio: float = Field(default=3.0, gt=1.0)
    # Below this displayed area (in points squared) a region is a logo, a
    # crest or an icon. Those are placed by the template rather than by
    # whoever filled the document in, and legitimately differ from body
    # content in both resolution and compression — a vector-sharp letterhead
    # logo beside a 150 DPI scan is an ordinary document, so comparing them
    # produces noise, not findings. Such regions are still identified (AC1),
    # just excluded from comparison (AC6).
    #
    # 1500pt² is calibrated against this repo's own generated documents
    # (doc_gen_scripts), whose real content regions are:
    #   certificate of incorporation  18225pt² crest, 1897pt² ACRA stamp
    #   NRIC card                     2716-5581pt² portrait and crest
    #   corporate bank statement      2364pt² logo
    #   credit report                 2252pt² bureau logo
    # The smallest of those is the 1897pt² stamp, so the floor sits just
    # under it. Earlier defaults of 5000 and 2000 each excluded real
    # content — 5000 blanked every document this pipeline produces, and
    # 2000 left a single-page certificate with only one comparable region,
    # which is never assessable. Template furniture is excluded by page
    # repetition (see _flaggable) rather than by size, so this floor only
    # has to clear genuinely decorative marks.
    min_region_area_pt2: float = Field(default=1500.0, ge=0.0)
    # Fewer comparable regions than this and there is no "surrounding
    # content" to compare against, which is an Unable to Assess (AC7), not
    # a clean result.
    min_comparable_regions: int = Field(default=2, ge=2)
    # An enlarged region holds almost no detail at its finest scale while
    # still holding some at coarser ones, so the ratio between the two
    # collapses (see _detail_ratio). Measured across every image asset this
    # repo's generators embed, genuine artwork scores between 0.250
    # (uob_stamp.jpg) and 0.496 (nric_crest.jpeg). The ACRA crest shrunk and
    # blown back up to its original pixel dimensions scores 0.076 to 0.123
    # across enlargement factors of 2x through 6x. 0.18 sits between the two
    # populations, about 1.4x below the nearest genuine asset and 1.5x above
    # the nearest forgery. Unlike the thresholds above this one is not a
    # ratio between two regions, so it is the only absolute number here —
    # but it is a ratio internal to one image, which is why it survives
    # contrast and scale differences that defeat measuring detail directly.
    min_detail_ratio: float = Field(default=0.18, gt=0.0, lt=1.0)
    # --- standalone PNG/JPEG uploads (see _check_raster) ---
    # A window of a flat image is flagged when at least this share of its
    # hard pixel steps, on BOTH axes, stand alone with flat pixels either
    # side (see _step_isolation). Measured over 64px windows of 81 clean
    # rendered documents and 20 NRIC/passport uploads, genuine content —
    # anti-aliased text, photos, crests — keeps at least one neighbour of a
    # step moving; a region enlarged without smoothing does not. At 0.4 the
    # NRIC/passport set shows no false alarms and 91% of injected
    # resolution_mismatch patches are caught.
    min_step_isolation: float = Field(default=0.4, gt=0.0, le=1.0)
    # A finding needs this many adjacent flagged windows. One window on its
    # own is most often a single pixel-sharp rule or border; requiring two
    # halves false alarms on rendered documents for a 1-point cost in
    # detection.
    min_flagged_windows: int = Field(default=2, ge=1)


class ImageRegion(BaseModel):
    """AC1"""

    xref: int
    page: int
    bbox: BBox
    pixel_width: int
    pixel_height: int
    # Pixels per inch as RENDERED, not as stored: a 2000px image shrunk into
    # a 100pt box is drawn at ~1440 DPI. This is the number that differs
    # when a region comes from somewhere else.
    effective_dpi: float
    codec: str
    stored_bytes: int
    bytes_per_pixel: float
    # None for anything that is not JPEG. Two JPEGs from the same encoder at
    # the same quality share these tables exactly; different tables mean
    # different compression provenance.
    quantisation_signature: str | None
    # How many pages this same embedded asset (xref) is drawn on. A logo or
    # icon repeated on every page is template furniture placed by the
    # issuer, not content anyone filled in.
    pages_used_on: int
    # Detail at the image's finest scale divided by detail three octaves
    # coarser (see _detail_ratio). A low value means the region carries far
    # less real detail than its stored pixel count claims — it was enlarged
    # from something smaller. None when the image is too small to measure or
    # could not be decoded.
    detail_ratio: float | None = None
    # Regions below the size floor, and template furniture, are kept (AC1
    # asks for identification) but excluded from comparison (AC6).
    comparable: bool
    # Set when a later, opaque image is drawn over this one (see
    # _hidden_placements). A hidden region is not part of what the reader
    # sees, so it is excluded from comparison — but it is the original that
    # the visible region replaced, which _check_covered_regions compares
    # against directly.
    covered_by_xref: int | None = None
    covered_by_bbox: BBox | None = None


class ImageManipulationIndicator(BaseModel):
    indicator: IndicatorType  # AC5
    page: int  # AC4
    bbox: BBox  # AC4
    detected: str
    expected: str
    deviation: str
    severity: Severity
    summary: str
    evidence: dict


class ImageManipulationResult(BaseModel):
    document_id: str
    regions: list[ImageRegion]  # AC1
    indicators: list[ImageManipulationIndicator]
    pages_checked: int
    tolerances: Tolerances
    unable_to_assess: bool  # AC7
    unable_to_assess_reason: str | None = None
    # What the bbox numbers mean: PDF points for a PDF, pixels for a PNG or
    # JPEG upload, which has no page geometry of its own.
    bbox_units: Literal["pt", "px"] = "pt"


def _unable(
    document_id: str, reason: str, tolerances: Tolerances,
    regions: list[ImageRegion] | None = None, pages: int = 0,
) -> ImageManipulationResult:
    return ImageManipulationResult(
        document_id=document_id,
        regions=regions or [],
        indicators=[],
        pages_checked=pages,
        tolerances=tolerances,
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


# --- measurement -------------------------------------------------------------

def _jpeg_quantisation_tables(data: bytes) -> tuple[bytes, ...] | None:
    """
    Pulls the DQT segments out of a JPEG.

    The quantisation tables are the encoder's fingerprint: they are chosen
    by the quality setting and the encoder implementation, so two images
    compressed in the same pass carry identical tables and an image
    compressed elsewhere almost never does. Comparing them is far more
    reliable than comparing file sizes, which vary with content.

    Returns None for anything that is not a parseable JPEG.
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != _JPEG_SOI:
        return None

    tables: list[bytes] = []
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in _JPEG_STANDALONE:
            i += 2
            continue
        if marker == _JPEG_SOS:  # entropy-coded data follows; stop
            break
        if i + 4 > len(data):
            break
        segment_length = int.from_bytes(data[i + 2:i + 4], "big")
        if segment_length < 2:
            break
        if marker == _JPEG_DQT:
            tables.append(bytes(data[i + 4:i + 2 + segment_length]))
        i += 2 + segment_length

    return tuple(tables) or None


def _quantisation_signature(data: bytes) -> str | None:
    tables = _jpeg_quantisation_tables(data)
    if tables is None:
        return None
    digest = hashlib.sha256(b"".join(tables)).hexdigest()[:12]
    return digest


def _resample_residual(image: Image.Image, factor: int) -> float | None:
    """
    How much of the image lives at scales finer than 1/factor: the RMS
    difference between the image and itself after being shrunk by `factor`
    and interpolated back up. A sharp image loses a lot in that round trip;
    one that was already enlarged loses almost nothing, because there was
    nothing at those scales to lose.
    """
    width, height = image.size
    if width // factor < 4 or height // factor < 4:
        return None
    reduced = image.resize((width // factor, height // factor), Image.LANCZOS)
    restored = reduced.resize((width, height), Image.LANCZOS)
    # rms[0] is the single band's root-mean-square, 0-255. Normalised so the
    # figure is comparable between images of different bit depths.
    return ImageStat.Stat(ImageChops.difference(image, restored)).rms[0] / 255.0


def _detail_ratio(data: bytes) -> float | None:
    """
    Whether a region holds as much real detail as its stored size claims.

    Resolution everywhere else in this module is geometry: stored pixels
    divided by printed inches. That number says nothing about whether the
    pixels carry information. Enlarging a small image restores the pixel
    count and leaves the detail behind, so a crest lifted off a website and
    blown up to letterhead size reports a perfectly ordinary DPI while
    looking soft to anyone who actually looks at it. This is the blind spot
    of a DPI comparison, by construction rather than by loose tolerance.

    What separates the two cases is where the detail sits. Measuring the
    finest scale alone does not work: across this repo's own assets that
    figure spans 7.8x with nothing wrong, because it tracks how contrasty
    and textured the artwork is. Dividing it by the same measurement three
    octaves coarser cancels that out — both figures scale together with
    contrast — and leaves only the shape of the falloff, which is what
    enlargement changes. The same assets then span 2x, and an enlarged
    region sits well below all of them.

    Returns None for an image too small to measure or impossible to decode.
    """
    try:
        with Image.open(io.BytesIO(data)) as opened:
            image = opened.convert("L")
    except Exception:
        # Anything pymupdf could extract but PIL cannot decode (CCITT fax,
        # JBIG2, exotic JPEG2000 variants). Not measurable, not a finding.
        return None

    if min(image.size) < _MIN_DETAIL_PIXELS:
        return None

    fine = _resample_residual(image, _DETAIL_FINE_FACTOR)
    coarse = _resample_residual(image, _DETAIL_COARSE_FACTOR)
    logger.debug(
        "detail: %sx%s px  residual@1/%s=%s  residual@1/%s=%s",
        image.size[0], image.size[1],
        _DETAIL_FINE_FACTOR, "n/a" if fine is None else f"{fine:.5f}",
        _DETAIL_COARSE_FACTOR, "n/a" if coarse is None else f"{coarse:.5f}",
    )
    if fine is None or coarse is None or coarse <= 0.0:
        # coarse == 0 means a completely flat image: no detail at any scale,
        # so there is no falloff to describe and nothing to conclude.
        return None
    return round(fine / coarse, 4)


def _hidden_placements(page: pymupdf.Page) -> list[tuple[int, BBox, int, BBox]]:
    """
    Image placements that a later image is drawn straight over, as
    (hidden xref, hidden bbox, covering xref, covering bbox).

    The commonest way to alter one image in an existing PDF is not to edit
    it but to draw a new one on top: the original stays in the file,
    painted out, and the reader only sees the replacement. That leaves the
    original sitting directly underneath — the one thing the altered region
    can be compared against with certainty, whatever its size and however
    few other images the document holds.

    get_image_info lists placements in drawing order, so "later" means
    "drawn on top". A covering image with a transparency mask is skipped:
    a signature laid over a stamp shows the stamp through it rather than
    replacing it.
    """
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        logger.debug("Page %s: image draw order unavailable", page.number, exc_info=True)
        return []

    hidden: list[tuple[int, BBox, int, BBox]] = []
    for i, below in enumerate(infos):
        below_rect = pymupdf.Rect(below["bbox"])
        if below_rect.is_empty:
            continue
        for above in infos[i + 1:]:
            above_rect = pymupdf.Rect(above["bbox"])
            if above["xref"] == below["xref"] or above_rect.is_empty or above.get("has-mask"):
                continue
            covered = (below_rect & above_rect).get_area() / below_rect.get_area()
            if (
                covered >= _MIN_HIDDEN_FRACTION
                and above_rect.get_area() <= _MAX_COVER_AREA_RATIO * below_rect.get_area()
            ):
                hidden.append((
                    below["xref"], tuple(below_rect), above["xref"], tuple(above_rect),
                ))
                break
    return hidden


def _same_bbox(a: BBox, b: BBox) -> bool:
    # get_image_rects and get_image_info compute the same box by different
    # routes, so allow for float noise.
    return all(abs(x - y) < 0.5 for x, y in zip(a, b))


def _collect_regions(doc: pymupdf.Document, tolerances: Tolerances) -> list[ImageRegion]:
    """AC1 — every embedded image, with where and how large it is drawn."""
    regions: list[ImageRegion] = []
    # Decoding and resampling an image is the expensive part of this module,
    # and a template asset reappears on every page, so measure each distinct
    # asset once.
    detail_by_xref: dict[int, float | None] = {}
    hidden: dict[int, list[tuple[int, BBox, int, BBox]]] = {}

    for page in doc:
        hidden[page.number] = _hidden_placements(page)
        for image in page.get_images(full=True):
            xref = image[0]
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                logger.warning(
                    "Page %s: image xref %s could not be extracted", page.number, xref,
                    exc_info=True,
                )
                continue
            if not extracted:
                continue

            pixel_width = int(extracted.get("width") or 0)
            pixel_height = int(extracted.get("height") or 0)
            data = extracted.get("image") or b""
            if pixel_width <= 0 or pixel_height <= 0 or not data:
                continue

            if xref not in detail_by_xref:
                detail_by_xref[xref] = _detail_ratio(data)

            try:
                rects = page.get_image_rects(xref)
            except Exception:
                rects = []

            for rect in rects or ():
                drawn_width_pt = float(rect.width)
                drawn_height_pt = float(rect.height)
                if drawn_width_pt <= 0 or drawn_height_pt <= 0:
                    continue

                # 72 points to the inch, so pixels / (points / 72) = DPI.
                # Averaged over both axes: a region stretched on one axis
                # only would otherwise read as a mismatch on its own.
                dpi_x = pixel_width / (drawn_width_pt / 72.0)
                dpi_y = pixel_height / (drawn_height_pt / 72.0)
                effective_dpi = (dpi_x + dpi_y) / 2.0
                area_pt2 = drawn_width_pt * drawn_height_pt

                logger.debug(
                    "region: page=%s xref=%s %sx%s px drawn %.0fx%.0fpt "
                    "(%.0fpt2) dpi=%.1f codec=%s bpp=%.5f qsig=%s detail=%s "
                    "comparable=%s (floor %.0fpt2)",
                    page.number, xref, pixel_width, pixel_height,
                    drawn_width_pt, drawn_height_pt, area_pt2, effective_dpi,
                    extracted.get("ext"), len(data) / (pixel_width * pixel_height),
                    _quantisation_signature(data), detail_by_xref[xref],
                    area_pt2 >= tolerances.min_region_area_pt2,
                    tolerances.min_region_area_pt2,
                )
                regions.append(
                    ImageRegion(
                        xref=xref,
                        page=page.number,
                        bbox=(rect.x0, rect.y0, rect.x1, rect.y1),
                        pixel_width=pixel_width,
                        pixel_height=pixel_height,
                        effective_dpi=round(effective_dpi, 1),
                        codec=str(extracted.get("ext") or "unknown"),
                        stored_bytes=len(data),
                        bytes_per_pixel=round(len(data) / (pixel_width * pixel_height), 5),
                        quantisation_signature=_quantisation_signature(data),
                        detail_ratio=detail_by_xref[xref],
                        pages_used_on=1,  # corrected below, once all pages are seen
                        comparable=area_pt2 >= tolerances.min_region_area_pt2,
                    )
                )

    # Second pass: count how many pages each asset is drawn on. An asset on
    # more than one page is the issuer's template furniture — a letterhead
    # logo, a page-corner icon, a watermark. It still informs the baseline
    # (see _baseline), but is never itself flagged (see _flaggable): such
    # assets legitimately differ from each other in resolution, since a
    # square icon and a wide banner are authored separately, and because
    # they repeat they arrive in equal-sized clusters that drag a median
    # between them and make each cluster look like the other's outlier.
    # A pasted region, by contrast, appears once.
    pages_by_xref: dict[int, set[int]] = {}
    for region in regions:
        pages_by_xref.setdefault(region.xref, set()).add(region.page)
    for region in regions:
        region.pages_used_on = len(pages_by_xref[region.xref])

    # Third pass: mark placements painted over by a later image. They are
    # not what the reader sees, so they leave the comparison pool.
    for region in regions:
        for xref, bbox, above_xref, above_bbox in hidden.get(region.page, ()):
            if region.xref == xref and _same_bbox(region.bbox, bbox):
                logger.debug(
                    "region: page=%s xref=%s is hidden under xref=%s", region.page,
                    region.xref, above_xref,
                )
                region.covered_by_xref = above_xref
                region.covered_by_bbox = above_bbox
                region.comparable = False
                break

    return regions


def _baseline(regions: list[ImageRegion]) -> list[ImageRegion]:
    """
    One entry per distinct asset, so a logo repeated on twenty pages counts
    once rather than twenty times and cannot dominate the median.
    """
    seen: set[int] = set()
    unique: list[ImageRegion] = []
    for region in regions:
        if region.xref in seen:
            continue
        seen.add(region.xref)
        unique.append(region)
    return unique


def _flaggable(regions: list[ImageRegion]) -> list[ImageRegion]:
    """Regions eligible to be reported: sized, and not template furniture."""
    return [region for region in regions if region.comparable and region.pages_used_on == 1]


def _dedupe(
    indicators: list[ImageManipulationIndicator],
) -> list[ImageManipulationIndicator]:
    """
    One row per region per indicator type. An enlarged region is often also
    a resolution outlier against its neighbours, and an officer should see
    one finding about it, not two saying the same thing in different words.
    Order is preserved, so the check that ran first wins.
    """
    seen: set[tuple[str, int, BBox]] = set()
    unique: list[ImageManipulationIndicator] = []
    for indicator in indicators:
        key = (indicator.indicator, indicator.page, indicator.bbox)
        if key in seen:
            continue
        seen.add(key)
        unique.append(indicator)
    return unique


# --- checks ------------------------------------------------------------------

def _ratio_against(value: float, baseline: float) -> float:
    """How many times apart two positive values are, in either direction."""
    if value <= 0 or baseline <= 0:
        return 1.0
    return max(value / baseline, baseline / value)


def _check_intrinsic_resolution(
    regions: list[ImageRegion], tolerances: Tolerances
) -> list[ImageManipulationIndicator]:
    """
    AC2, measured without a comparison — regions enlarged from something
    smaller than they claim to be.

    Every other check in this module asks whether a region disagrees with
    its neighbours. This one asks whether a region disagrees with ITSELF:
    the resolution it is stored at against the resolution its detail shows
    it was really produced at. Nothing else in the document is involved,
    which makes it the only check here that works on a document holding a
    single image — and on this pipeline's corpus most documents hold one or
    none, so it is also where most of the reach comes from.

    Recorded as a Resolution Mismatch (AC5): the mismatch is between stored
    and actual resolution rather than between two regions.
    """
    indicators: list[ImageManipulationIndicator] = []

    # One row per distinct asset, so a letterhead crest drawn on twenty
    # pages is reported once rather than twenty times. Template furniture is
    # deliberately NOT excluded here: the repetition exclusion elsewhere
    # exists because repeated assets distort a median, and this check has no
    # median to distort. A forged letterhead appears on every page, and
    # excluding it would exempt exactly the case worth catching.
    for region in _baseline([region for region in regions if region.comparable]):
        ratio = region.detail_ratio
        if ratio is None:
            logger.debug(
                "intrinsic: xref=%s not measurable (too small, or undecodable)", region.xref
            )
            continue
        logger.debug(
            "intrinsic: xref=%s detail=%.4f vs floor %.2f -> %s",
            region.xref, ratio, tolerances.min_detail_ratio,
            "ENLARGED" if ratio < tolerances.min_detail_ratio else "ok",
        )
        if ratio >= tolerances.min_detail_ratio:
            continue

        # Far below the threshold rather than just under it. A region with
        # almost no fine detail at all was enlarged by a large factor, or
        # from a thumbnail.
        severity: Severity = (
            "high" if ratio < tolerances.min_detail_ratio / 2 else "medium"
        )
        indicators.append(
            ImageManipulationIndicator(
                indicator="Resolution Mismatch",
                page=region.page,
                bbox=region.bbox,
                detected=(
                    f"stored at {region.pixel_width}x{region.pixel_height} px "
                    f"({region.effective_dpi:.0f} DPI as drawn) but carrying a "
                    f"detail ratio of only {ratio:.3f}"
                ),
                expected=(
                    f"at least {tolerances.min_detail_ratio:.2f} for artwork "
                    "genuinely produced at this pixel size"
                ),
                deviation="enlarged from a smaller original",
                severity=severity,
                summary=(
                    f"Page {region.page + 1}: an image region stores "
                    f"{region.pixel_width}x{region.pixel_height} pixels but holds "
                    "almost none of the fine detail that size implies, meaning it "
                    "was enlarged from a smaller image rather than produced at this "
                    "resolution. On a letterhead mark this is the signature of a "
                    "logo copied from elsewhere and scaled to fit."
                ),
                evidence={
                    "xref": region.xref,
                    "detail_ratio": ratio,
                    "threshold": tolerances.min_detail_ratio,
                    "pixel_dimensions": f"{region.pixel_width}x{region.pixel_height}",
                    "effective_dpi": region.effective_dpi,
                    "pages_used_on": region.pages_used_on,
                },
            )
        )

    return indicators


def _check_covered_regions(
    regions: list[ImageRegion], tolerances: Tolerances
) -> list[ImageManipulationIndicator]:
    """
    AC2/AC3, measured against the original — a visible image compared with
    the image it was drawn over (see _hidden_placements).

    The comparative checks judge a region against its neighbours and have
    to hedge when there are few of them. Here the comparison partner is the
    region's own predecessor in the same slot, so there is no majority to
    find and no ambiguity about which one changed: the visible one is the
    replacement. That also makes it the one check that reaches small marks
    — a crest or a seal below the size floor — which are exactly what gets
    swapped on an official document.

    Runs regardless of size and repetition, like the intrinsic check. A
    replacement can differ from its original in resolution, compression or
    both; each is reported as its own indicator (AC5), since they are
    separate facts about how the replacement was made.
    """
    indicators: list[ImageManipulationIndicator] = []
    for hidden in regions:
        if hidden.covered_by_xref is None or hidden.covered_by_bbox is None:
            continue
        visible = next(
            (
                region for region in regions
                if region.page == hidden.page
                and region.xref == hidden.covered_by_xref
                and _same_bbox(region.bbox, hidden.covered_by_bbox)
            ),
            None,
        )
        if visible is None:
            continue

        base_evidence = {
            "xref": visible.xref,
            "hidden_xref": hidden.xref,
            "hidden_bbox": hidden.bbox,
            "pixel_dimensions": f"{visible.pixel_width}x{visible.pixel_height}",
            "hidden_pixel_dimensions": f"{hidden.pixel_width}x{hidden.pixel_height}",
        }
        ratio = _ratio_against(visible.effective_dpi, hidden.effective_dpi)
        logger.debug(
            "covered: page=%s xref=%s (%.1f DPI, %s) over hidden xref=%s (%.1f DPI, %s): "
            "%.2fx apart vs tolerance %.2fx",
            visible.page, visible.xref, visible.effective_dpi, visible.codec,
            hidden.xref, hidden.effective_dpi, hidden.codec, ratio, tolerances.dpi_ratio,
        )

        if ratio >= tolerances.dpi_ratio:
            direction = "higher" if visible.effective_dpi > hidden.effective_dpi else "lower"
            indicators.append(
                ImageManipulationIndicator(
                    indicator="Resolution Mismatch",
                    page=visible.page,
                    bbox=visible.bbox,
                    detected=f"{visible.effective_dpi:.0f} DPI",
                    expected=(
                        f"{hidden.effective_dpi:.0f} DPI (the original image hidden "
                        "underneath it)"
                    ),
                    deviation=f"{ratio:.1f}x {direction}; drawn over the original",
                    severity="high",
                    summary=(
                        f"Page {visible.page + 1}: an image is drawn directly over another "
                        f"image in the same position, hiding it. The visible image is "
                        f"{visible.effective_dpi:.0f} DPI; the hidden original underneath is "
                        f"{hidden.effective_dpi:.0f} DPI, {ratio:.1f}x apart. The region has "
                        "been replaced after the document was produced."
                    ),
                    evidence={
                        **base_evidence,
                        "effective_dpi": visible.effective_dpi,
                        "hidden_effective_dpi": hidden.effective_dpi,
                        "ratio": round(ratio, 2),
                        "tolerance_ratio": tolerances.dpi_ratio,
                    },
                )
            )

        codec_differs = visible.codec != hidden.codec
        tables_differ = (
            visible.quantisation_signature is not None
            and hidden.quantisation_signature is not None
            and visible.quantisation_signature != hidden.quantisation_signature
        )
        if not (codec_differs or tables_differ):
            # Same compression as the original. If the resolution matched
            # too, the overlay itself is still unusual, but judging it is
            # SCRUM-65's Image Overlay indicator, not a resolution or
            # compression one.
            continue
        detected, expected = (
            (f"{visible.codec} encoding", f"{hidden.codec} (the original hidden underneath it)")
            if codec_differs else
            (f"JPEG quantisation table {visible.quantisation_signature}",
             f"{hidden.quantisation_signature} (the original hidden underneath it)")
        )
        indicators.append(
            ImageManipulationIndicator(
                indicator="Compression Mismatch",
                page=visible.page,
                bbox=visible.bbox,
                detected=detected,
                expected=expected,
                deviation="compressed differently from the original it was drawn over",
                severity="high",
                summary=(
                    f"Page {visible.page + 1}: an image is drawn directly over another image "
                    "in the same position, hiding it, and was compressed differently from "
                    "the original underneath. The region has been replaced after the "
                    "document was produced."
                ),
                evidence={
                    **base_evidence,
                    "codec": visible.codec,
                    "hidden_codec": hidden.codec,
                    "quantisation_signature": visible.quantisation_signature,
                    "hidden_quantisation_signature": hidden.quantisation_signature,
                },
            )
        )
    return indicators


def _check_resolution(
    regions: list[ImageRegion], tolerances: Tolerances
) -> list[ImageManipulationIndicator]:
    """AC2 — regions drawn at a resolution unlike the rest of the document."""
    comparable = _baseline([region for region in regions if region.comparable])
    if len(comparable) < tolerances.min_comparable_regions:
        return []
    candidates = _flaggable(comparable)
    if not candidates:
        return []

    # With exactly two regions the median is their midpoint, so each sits
    # the SAME distance from it and neither can ever read as an outlier —
    # two regions 2x apart both score ~1.4x and nothing fires. Comparing
    # them against each other instead is the only thing that means anything
    # at that count. Which of the two is the altered one is genuinely
    # unknowable from resolution alone, so both are reported.
    if len(comparable) == 2:
        first, second = comparable
        ratio = _ratio_against(first.effective_dpi, second.effective_dpi)
        logger.debug(
            "resolution: only 2 assets, comparing pairwise: %.1f vs %.1f DPI = "
            "%.2fx apart vs tolerance %.2fx -> %s",
            first.effective_dpi, second.effective_dpi, ratio, tolerances.dpi_ratio,
            "MISMATCH" if ratio >= tolerances.dpi_ratio else "within tolerance",
        )
        if ratio < tolerances.dpi_ratio:
            return []
        return [
            ImageManipulationIndicator(
                indicator="Resolution Mismatch",
                page=region.page,
                bbox=region.bbox,
                detected=f"{region.effective_dpi:.0f} DPI",
                expected=f"{other.effective_dpi:.0f} DPI (the only other image region)",
                deviation=f"{ratio:.1f}x apart",
                severity="medium",
                summary=(
                    f"Page {region.page + 1}: this document's two image regions are drawn "
                    f"{ratio:.1f}x apart in resolution ({first.effective_dpi:.0f} vs "
                    f"{second.effective_dpi:.0f} DPI). With only two regions there is no "
                    "majority to say which one is out of place."
                ),
                evidence={
                    "xref": region.xref,
                    "effective_dpi": region.effective_dpi,
                    "other_effective_dpi": other.effective_dpi,
                    "ratio": round(ratio, 2),
                    "comparable_regions": 2,
                    "tolerance_ratio": tolerances.dpi_ratio,
                },
            )
            for region, other in ((first, second), (second, first))
            if region in candidates
        ]

    baseline = median(region.effective_dpi for region in comparable)
    logger.debug(
        "resolution: baseline median = %.1f DPI over %d assets %s",
        baseline, len(comparable), [round(r.effective_dpi) for r in comparable],
    )
    if baseline <= 0:
        return []

    indicators: list[ImageManipulationIndicator] = []
    for region in candidates:
        ratio = _ratio_against(region.effective_dpi, baseline)
        logger.debug(
            "resolution: xref=%s %.1f DPI is %.2fx from baseline vs tolerance %.2fx -> %s",
            region.xref, region.effective_dpi, ratio, tolerances.dpi_ratio,
            "MISMATCH" if ratio >= tolerances.dpi_ratio else "ok",
        )
        if ratio < tolerances.dpi_ratio:
            continue

        direction = "higher" if region.effective_dpi > baseline else "lower"

        # With too few regions to form a majority, say so rather than naming
        # this one as the altered region. An officer who is sent to the wrong
        # image and finds nothing wrong with it learns to distrust the check.
        confident = len(comparable) >= _MIN_CONFIDENT_ATTRIBUTION
        spread = ", ".join(
            f"{other.effective_dpi:.0f} DPI (xref {other.xref}"
            f"{', this one' if other.xref == region.xref else ''})"
            for other in sorted(comparable, key=lambda r: -r.effective_dpi)
        )
        caveat = (
            "" if confident else
            f" With only {len(comparable)} comparable regions there is no majority to "
            "establish which of them is out of place, so this reports an inconsistency "
            f"across the set rather than a verdict on one region. The full spread is: "
            f"{spread}. Each should be checked against the document it is supposed to "
            "have come from."
        )
        indicators.append(
            ImageManipulationIndicator(
                indicator="Resolution Mismatch",
                page=region.page,
                bbox=region.bbox,
                detected=f"{region.effective_dpi:.0f} DPI",
                expected=f"~{baseline:.0f} DPI (median of {len(comparable)} regions)",
                deviation=(
                    f"{ratio:.1f}x {direction}" if confident
                    else f"{ratio:.1f}x {direction}; which region is altered is ambiguous"
                ),
                # Less evidence, so a lower claim. A three-region spread is worth
                # a look, not the confidence of a clear majority.
                severity=(
                    ("high" if ratio >= tolerances.dpi_ratio * 2 else "medium")
                    if confident else "medium"
                ),
                summary=(
                    f"Page {region.page + 1}: an image region is drawn at "
                    f"{region.effective_dpi:.0f} DPI, {ratio:.1f}x {direction} than the "
                    f"~{baseline:.0f} DPI of the other images in this document."
                    + caveat
                ),
                evidence={
                    "xref": region.xref,
                    "pixel_dimensions": f"{region.pixel_width}x{region.pixel_height}",
                    "baseline_dpi": round(baseline, 1),
                    "ratio": round(ratio, 2),
                    "comparable_regions": len(comparable),
                    "tolerance_ratio": tolerances.dpi_ratio,
                    "attribution_confident": confident,
                    # Every candidate, so a reviewer can see the whole spread
                    # instead of only the region this row happens to name.
                    "all_region_dpi": {
                        str(other.xref): other.effective_dpi for other in comparable
                    },
                },
            )
        )
    return indicators


def _check_compression(
    regions: list[ImageRegion], tolerances: Tolerances
) -> list[ImageManipulationIndicator]:
    """
    AC3 — regions whose compression does not match the rest.

    Three signals, strongest first:
      1. A JPEG whose quantisation tables differ from the other JPEGs. This
         is the encoder's fingerprint, so a difference means the region was
         compressed somewhere else.
      2. A codec nobody else on the page uses (one JPEG photo dropped into
         an otherwise PNG/Flate document, or the reverse).
      3. Stored bytes per pixel far from the document's norm.

    Only one indicator is raised per region — the strongest that applies —
    so a pasted photo does not produce three rows describing one paste.
    """
    comparable = _baseline([region for region in regions if region.comparable])
    if len(comparable) < tolerances.min_comparable_regions:
        return []
    candidates = _flaggable(comparable)
    if not candidates:
        return []

    indicators: list[ImageManipulationIndicator] = []

    # 1. Quantisation-table mismatch among JPEGs.
    jpegs = [region for region in comparable if region.quantisation_signature]
    logger.debug(
        "compression: %d of %d assets are JPEG; quantisation comparison needs %d",
        len(jpegs), len(comparable), tolerances.min_comparable_regions,
    )
    majority_signature: str | None = None
    if len(jpegs) >= tolerances.min_comparable_regions:
        counts: dict[str, int] = {}
        for region in jpegs:
            signature = region.quantisation_signature
            if signature is not None:  # always true here; narrows the type
                counts[signature] = counts.get(signature, 0) + 1
        majority_signature, majority_count = max(counts.items(), key=lambda item: item[1])
        # A clean split (two of each) is not a majority and not evidence.
        if majority_count <= len(jpegs) / 2:
            majority_signature = None

    flagged: set[tuple[int, BBox]] = set()

    # With exactly two JPEGs there is no majority to find — one of each is a
    # 1-1 split, so the loop below never fires and a genuinely different
    # encoder passes unnoticed. The tables either match or they do not, which
    # is a fact about the pair rather than about either one, so both are
    # reported and the attribution left open. Mirrors the two-region path in
    # _check_resolution, for the same reason.
    if (
        majority_signature is None
        and len(jpegs) == 2
        and jpegs[0].quantisation_signature != jpegs[1].quantisation_signature
    ):
        logger.debug(
            "compression: 2 JPEGs with differing tables (%s vs %s), comparing pairwise",
            jpegs[0].quantisation_signature, jpegs[1].quantisation_signature,
        )
        for region, other in ((jpegs[0], jpegs[1]), (jpegs[1], jpegs[0])):
            if region not in candidates:
                continue
            flagged.add((region.xref, region.bbox))
            indicators.append(
                ImageManipulationIndicator(
                    indicator="Compression Mismatch",
                    page=region.page,
                    bbox=region.bbox,
                    detected=f"JPEG quantisation table {region.quantisation_signature}",
                    expected=(
                        f"{other.quantisation_signature} (the only other JPEG region)"
                    ),
                    deviation="different encoder or quality setting; which region is "
                              "altered is ambiguous",
                    severity="medium",
                    summary=(
                        f"Page {region.page + 1}: this document's two JPEG regions were "
                        "compressed with different quantisation tables, so they did not "
                        "come out of the same pass. With only two there is no majority "
                        "to say which one is out of place; both should be checked "
                        "against the document they are supposed to have come from."
                    ),
                    evidence={
                        "xref": region.xref,
                        "quantisation_signature": region.quantisation_signature,
                        "other_quantisation_signature": other.quantisation_signature,
                        "jpeg_regions": 2,
                        "attribution_confident": False,
                    },
                )
            )

    if majority_signature is not None:
        for region in jpegs:
            if region.quantisation_signature == majority_signature or region not in candidates:
                continue
            flagged.add((region.xref, region.bbox))
            indicators.append(
                ImageManipulationIndicator(
                    indicator="Compression Mismatch",
                    page=region.page,
                    bbox=region.bbox,
                    detected=f"JPEG quantisation table {region.quantisation_signature}",
                    expected=f"{majority_signature} (used by the other JPEG regions)",
                    deviation="different encoder or quality setting",
                    severity="high",
                    summary=(
                        f"Page {region.page + 1}: an image region was JPEG-compressed with "
                        "different quantisation tables from the rest of the document — it was "
                        "not produced in the same pass as the surrounding content."
                    ),
                    evidence={
                        "xref": region.xref,
                        "quantisation_signature": region.quantisation_signature,
                        "document_signature": majority_signature,
                        "jpeg_regions": len(jpegs),
                    },
                )
            )

    # 2. Odd codec out.
    codec_counts: dict[str, int] = {}
    for region in comparable:
        codec_counts[region.codec] = codec_counts.get(region.codec, 0) + 1
    logger.debug(
        "compression: codec spread %s -> %s", codec_counts,
        "no majority, nothing to conclude" if len(codec_counts) > 1
        and max(codec_counts.values()) <= len(comparable) / 2
        else ("single codec, nothing to compare" if len(codec_counts) == 1 else "majority found"),
    )
    if len(codec_counts) > 1:
        majority_codec, majority_count = max(codec_counts.items(), key=lambda item: item[1])
        if majority_count > len(comparable) / 2:
            for region in candidates:
                if region.codec == majority_codec or (region.xref, region.bbox) in flagged:
                    continue
                flagged.add((region.xref, region.bbox))
                indicators.append(
                    ImageManipulationIndicator(
                        indicator="Compression Mismatch",
                        page=region.page,
                        bbox=region.bbox,
                        detected=f"{region.codec} encoding",
                        expected=f"{majority_codec} ({majority_count} of {len(comparable)} regions)",
                        deviation="different image format from the surrounding content",
                        severity="medium",
                        summary=(
                            f"Page {region.page + 1}: an image region is stored as "
                            f"{region.codec} while the rest of the document uses "
                            f"{majority_codec} — consistent with a region inserted separately."
                        ),
                        evidence={
                            "xref": region.xref,
                            "codec": region.codec,
                            "codec_counts": codec_counts,
                        },
                    )
                )

    # 3. Bytes-per-pixel outlier — LOSSY codecs only.
    #
    # For a lossy format, stored bytes per pixel tracks the quality setting,
    # which is what this check is after. For a lossless one (PNG/Flate) it
    # tracks image CONTENT instead: a flat logo compresses to almost
    # nothing, a portrait photograph barely compresses at all. A clean NRIC
    # card in this repo's own corpus spans 0.13 to 1.95 bytes/pixel across
    # three legitimate PNGs — a 15x spread with nothing wrong with it — so
    # applying this to lossless images produces false positives and no
    # signal. The quantisation-table and codec checks above already cover
    # what is knowable for those.
    lossy = [region for region in comparable if region.codec in _LOSSY_CODECS]
    baseline_bpp = median(region.bytes_per_pixel for region in lossy) if lossy else 0.0
    logger.debug(
        "compression: %d of %d assets use a lossy codec (bytes/pixel needs %d); "
        "baseline %.5f bytes/pixel",
        len(lossy), len(comparable), tolerances.min_comparable_regions, baseline_bpp,
    )
    # Same degeneracy as above: the median of two values is their midpoint, so
    # each sits the same distance from it and neither can cross the tolerance.
    # Two regions 3.3x apart each score only 1.8x against their own midpoint.
    if len(lossy) == 2 and baseline_bpp > 0:
        first, second = lossy
        ratio = _ratio_against(first.bytes_per_pixel, second.bytes_per_pixel)
        logger.debug(
            "compression: only 2 lossy assets, comparing pairwise: %.5f vs %.5f "
            "bytes/pixel = %.2fx apart vs tolerance %.2fx -> %s",
            first.bytes_per_pixel, second.bytes_per_pixel, ratio,
            tolerances.bytes_per_pixel_ratio,
            "MISMATCH" if ratio >= tolerances.bytes_per_pixel_ratio else "within tolerance",
        )
        if ratio >= tolerances.bytes_per_pixel_ratio:
            for region, other in ((first, second), (second, first)):
                if region not in candidates or (region.xref, region.bbox) in flagged:
                    continue
                direction = (
                    "more" if region.bytes_per_pixel > other.bytes_per_pixel else "less"
                )
                flagged.add((region.xref, region.bbox))
                indicators.append(
                    ImageManipulationIndicator(
                        indicator="Compression Mismatch",
                        page=region.page,
                        bbox=region.bbox,
                        detected=f"{region.bytes_per_pixel:.4f} bytes/pixel",
                        expected=(
                            f"{other.bytes_per_pixel:.4f} bytes/pixel "
                            "(the only other lossy region)"
                        ),
                        deviation=f"{ratio:.1f}x {direction} data per pixel; which "
                                  "region is altered is ambiguous",
                        severity="medium",
                        summary=(
                            f"Page {region.page + 1}: this document's two lossy image "
                            f"regions store {ratio:.1f}x different amounts of data per "
                            "pixel, meaning they were compressed at different qualities. "
                            "With only two there is no majority to say which one is out "
                            "of place."
                        ),
                        evidence={
                            "xref": region.xref,
                            "bytes_per_pixel": region.bytes_per_pixel,
                            "other_bytes_per_pixel": other.bytes_per_pixel,
                            "ratio": round(ratio, 2),
                            "tolerance_ratio": tolerances.bytes_per_pixel_ratio,
                            "attribution_confident": False,
                        },
                    )
                )

    elif len(lossy) >= tolerances.min_comparable_regions and baseline_bpp > 0:
        for region in lossy:
            if region not in candidates:
                continue
            if (region.xref, region.bbox) in flagged:
                continue
            ratio = _ratio_against(region.bytes_per_pixel, baseline_bpp)
            if ratio < tolerances.bytes_per_pixel_ratio:
                continue
            direction = "more" if region.bytes_per_pixel > baseline_bpp else "less"
            flagged.add((region.xref, region.bbox))
            indicators.append(
                ImageManipulationIndicator(
                    indicator="Compression Mismatch",
                    page=region.page,
                    bbox=region.bbox,
                    detected=f"{region.bytes_per_pixel:.4f} bytes/pixel",
                    expected=f"~{baseline_bpp:.4f} bytes/pixel",
                    deviation=f"{ratio:.1f}x {direction} data per pixel",
                    severity="medium",
                    summary=(
                        f"Page {region.page + 1}: an image region stores {ratio:.1f}x "
                        f"{direction} data per pixel than the rest of the document, "
                        "indicating it was compressed at a different quality."
                    ),
                    evidence={
                        "xref": region.xref,
                        "bytes_per_pixel": region.bytes_per_pixel,
                        "baseline_bytes_per_pixel": round(baseline_bpp, 5),
                        "ratio": round(ratio, 2),
                        "tolerance_ratio": tolerances.bytes_per_pixel_ratio,
                    },
                )
            )

    return indicators


# --- standalone PNG / JPEG uploads --------------------------------------------
#
# A photographed or scanned NRIC arrives as one flat image, with no embedded
# objects to compare against each other. The comparison has to happen inside
# the picture instead: split it into overlapping windows, measure each, and
# report the windows that behave unlike genuine content.

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

_RASTER_WINDOW = 64
_RASTER_STRIDE = 32
# Grey-level thresholds for _step_isolation: a "hard step" between two
# neighbouring pixels, and a "flat" neighbour either side of it.
_HARD_STEP = 24
_FLAT_NEIGHBOUR = 3
# A window needs this many hard steps on EACH axis before its isolation
# share means anything; fewer and one stroke decides the answer.
_MIN_STEPS_PER_AXIS = 12
# ...and this many pixels with any visible change (over _EDGE grey levels)
# at all. A window of plain card with one pixel-sharp border has enough
# hard steps along the border to pass the count above and nothing else to
# balance them.
_EDGE = 8
_MIN_EDGE_PIXELS = 60
# Below this on either side, a window grid cannot be formed.
_MIN_RASTER_SIDE = 2 * _RASTER_WINDOW


def _is_raster(file_bytes: bytes) -> bool:
    return file_bytes.startswith(_PNG_MAGIC) or file_bytes.startswith(_JPEG_MAGIC)


def _window_sums(mask: np.ndarray, ys: list[int], xs: list[int]) -> np.ndarray:
    """Count of True pixels in every WxW window at the given origins."""
    integral = np.pad(mask.astype(np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    top = np.array(ys)[:, None]
    left = np.array(xs)[None, :]
    w = _RASTER_WINDOW
    return (
        integral[top + w, left + w] - integral[top, left + w]
        - integral[top + w, left] + integral[top, left]
    )


def _step_isolation(gray: np.ndarray) -> tuple[np.ndarray, list[int], list[int]]:
    """
    Per window, the share of hard pixel steps that stand alone — a jump
    between two pixels with no movement on either side of it — taking the
    lower of the horizontal and vertical shares. NaN where a window holds
    too few steps to judge.

    Genuine content moves gradually at its edges: anti-aliased text ramps
    over two or three pixels, a photograph never sits perfectly still.
    A region enlarged by repeating pixels (or shrunk and blown back up
    without smoothing) is made of flat blocks, so every change happens in
    one jump with nothing either side. Requiring this on BOTH axes is what
    keeps a pixel-sharp ruled line or card border — isolated steps along
    one axis only — from reading as an enlarged region.
    """
    height, width = gray.shape
    ys = list(range(0, height - _RASTER_WINDOW + 1, _RASTER_STRIDE))
    xs = list(range(0, width - _RASTER_WINDOW + 1, _RASTER_STRIDE))

    dx = np.zeros_like(gray)
    dx[:, :-1] = np.abs(np.diff(gray, axis=1))
    dy = np.zeros_like(gray)
    dy[:-1, :] = np.abs(np.diff(gray, axis=0))
    busy = _window_sums((dx > _EDGE) | (dy > _EDGE), ys, xs) >= _MIN_EDGE_PIXELS

    shares = []
    for axis in (1, 0):
        diff = np.abs(np.diff(gray, axis=axis))
        pad = [(0, 0), (0, 0)]
        pad[axis] = (1, 1)
        padded = np.pad(diff, pad, constant_values=_HARD_STEP)  # edges never "flat"
        if axis == 1:
            before, step, after = padded[:, :-2], padded[:, 1:-1], padded[:, 2:]
        else:
            before, step, after = padded[:-2, :], padded[1:-1, :], padded[2:, :]
        hard = step > _HARD_STEP
        isolated = hard & (before <= _FLAT_NEIGHBOUR) & (after <= _FLAT_NEIGHBOUR)
        # Back to image shape so windows line up between the two axes.
        full = [(0, 0), (0, 0)]
        full[axis] = (0, 1)
        hard_count = _window_sums(np.pad(hard, full), ys, xs)
        isolated_count = _window_sums(np.pad(isolated, full), ys, xs)
        shares.append(np.where(
            busy & (hard_count >= _MIN_STEPS_PER_AXIS),
            isolated_count / np.maximum(hard_count, 1),
            np.nan,
        ))
    # np.minimum, not np.fmin: a window too sparse on EITHER axis stays NaN
    # rather than being judged on the other axis alone.
    return np.minimum(shares[0], shares[1]), ys, xs


def _connected(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """8-connected groups of True cells in a window grid."""
    seen = np.zeros_like(mask, dtype=bool)
    groups: list[list[tuple[int, int]]] = []
    rows, cols = mask.shape
    for start in zip(*np.nonzero(mask)):
        if seen[start]:
            continue
        seen[start] = True
        stack, cells = [start], []
        while stack:
            r, c = stack.pop()
            cells.append((int(r), int(c)))
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < rows and 0 <= nc < cols and mask[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
        groups.append(cells)
    return groups


def _check_raster_resolution(
    gray: np.ndarray, tolerances: Tolerances
) -> tuple[list[ImageManipulationIndicator], int]:
    """
    AC2 for a flat image: regions enlarged from a lower resolution than the
    rest of the picture. Returns the indicators and how many windows held
    enough content to be judged at all.
    """
    scores, ys, xs = _step_isolation(gray)
    assessed = int(np.isfinite(scores).sum())
    flagged = np.isfinite(scores) & (scores >= tolerances.min_step_isolation)
    logger.debug(
        "raster resolution: %d window(s) assessable, %d over %.2f",
        assessed, int(flagged.sum()), tolerances.min_step_isolation,
    )

    indicators: list[ImageManipulationIndicator] = []
    for cells in _connected(flagged):
        if len(cells) < tolerances.min_flagged_windows:
            continue
        bbox: BBox = (
            float(min(xs[c] for _, c in cells)),
            float(min(ys[r] for r, _ in cells)),
            float(max(xs[c] for _, c in cells) + _RASTER_WINDOW),
            float(max(ys[r] for r, _ in cells) + _RASTER_WINDOW),
        )
        score = float(np.mean([scores[r, c] for r, c in cells]))
        baseline = float(np.nanmedian(scores)) if assessed else 0.0
        indicators.append(
            ImageManipulationIndicator(
                indicator="Resolution Mismatch",
                page=0,
                bbox=bbox,
                detected=f"{score:.0%} of edges are single-pixel jumps",
                expected=(
                    f"under {tolerances.min_step_isolation:.0%} for content produced at "
                    f"this resolution (this image's median is {baseline:.0%})"
                ),
                deviation="enlarged from a lower resolution than its surroundings",
                # Nearly every edge a bare jump is pixel repetition with no
                # plausible innocent reading.
                severity="high" if score >= 0.7 else "medium",
                summary=(
                    f"A {int(bbox[2] - bbox[0])}x{int(bbox[3] - bbox[1])} px region of "
                    "this image is built from flat blocks of repeated pixels, while the "
                    "rest of the image has the gradual edges of content produced at its "
                    "full resolution. The region was enlarged from a smaller source and "
                    "placed into the image."
                ),
                evidence={
                    "step_isolation": round(score, 3),
                    "image_median_step_isolation": round(baseline, 3),
                    "threshold": tolerances.min_step_isolation,
                    "windows": len(cells),
                    "window_px": _RASTER_WINDOW,
                },
            )
        )
    return indicators, assessed


def _check_raster(
    document_id: str, file_bytes: bytes, tolerances: Tolerances
) -> ImageManipulationResult:
    """A standalone PNG or JPEG upload, compared against itself (AC1-AC7)."""
    try:
        with Image.open(io.BytesIO(file_bytes)) as opened:
            codec = (opened.format or "unknown").lower()
            dpi_info = opened.info.get("dpi")
            gray = np.asarray(opened.convert("L"), dtype=np.float32)
    except Exception as exc:
        reason = f"Failed to decode the image: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _unable(document_id, reason, tolerances)

    height, width = gray.shape
    try:
        effective_dpi = float(sum(dpi_info) / 2) if dpi_info else 0.0
    except TypeError:
        effective_dpi = 0.0
    region = ImageRegion(
        xref=0,
        page=0,
        bbox=(0.0, 0.0, float(width), float(height)),
        pixel_width=width,
        pixel_height=height,
        # A flat image has no drawn size, only whatever DPI its file claims
        # (0 when it claims none). Recorded for reference; nothing compares it.
        effective_dpi=round(effective_dpi, 1),
        codec="jpeg" if codec == "jpeg" else codec,
        stored_bytes=len(file_bytes),
        bytes_per_pixel=round(len(file_bytes) / (width * height), 5),
        quantisation_signature=_quantisation_signature(file_bytes),
        detail_ratio=_detail_ratio(file_bytes),
        pages_used_on=1,
        comparable=True,
    )

    def result(indicators, unable_reason: str | None = None) -> ImageManipulationResult:
        return ImageManipulationResult(
            document_id=document_id,
            regions=[region],
            indicators=indicators,
            pages_checked=1,
            tolerances=tolerances,
            unable_to_assess=unable_reason is not None,
            unable_to_assess_reason=unable_reason,
            bbox_units="px",
        )

    if min(width, height) < _MIN_RASTER_SIDE:
        return result([], (
            f"Image is {width}x{height} px; at least {_MIN_RASTER_SIDE} px on each side "
            "is needed to compare one part of it with another."
        ))

    indicators, assessed = _check_raster_resolution(gray, tolerances)
    logger.debug(
        "document %s: raster %sx%s %s, %d assessable window(s), %d indicator(s)",
        document_id, width, height, codec, assessed, len(indicators),
    )
    # AC7 — a blank page, or one so smooth nothing in it has an edge.
    if assessed < 2 and not indicators:
        return result([], (
            "The image holds too little edge detail (text, lines or artwork) to "
            "compare one region against another."
        ))
    return result(_dedupe(indicators))


# --- entry point -------------------------------------------------------------

def check_image_manipulation(
    document_id: str,
    file_bytes: bytes,
    tolerances: Tolerances | None = None,
) -> ImageManipulationResult:
    """
    Inspects a PDF's embedded images, or a standalone PNG/JPEG upload, for
    resolution and compression that disagree with the rest of the same
    document. No LLM tokens.
    Synchronous/CPU-bound — callers should run this via asyncio.to_thread.
    """
    tolerances = tolerances or Tolerances()

    if _is_raster(file_bytes):
        return _check_raster(document_id, file_bytes, tolerances)

    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:
        reason = f"Failed to open document as a PDF: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _unable(document_id, reason, tolerances)

    try:
        logger.debug(
            "document %s: opened, %d page(s), tolerances %s",
            document_id, doc.page_count, tolerances.model_dump(),
        )
        regions = _collect_regions(doc, tolerances)
        pages = doc.page_count
        logger.debug(
            "document %s: %d placement(s) of %d distinct asset(s); %d placement(s) "
            "clear the %.0fpt2 size floor",
            document_id, len(regions), len({r.xref for r in regions}),
            sum(1 for r in regions if r.comparable), tolerances.min_region_area_pt2,
        )

        # AC7 — nothing to measure.
        if not regions:
            return _unable(
                document_id,
                f"No embedded images found across {pages} page(s), so there are no "
                "image regions to compare.",
                tolerances, regions, pages,
            )

        # Counted as DISTINCT ASSETS, not placements. Every comparative check
        # below dedupes by xref before comparing, so a single logo drawn on
        # four pages gives them one value and nothing to compare it with.
        # Counting placements here instead let such a document through the
        # gate and out the other side as "clean", reporting a pass for a
        # comparison that never happened.
        comparable = _baseline([region for region in regions if region.comparable])

        # These run first and unconditionally: neither needs the rest of the
        # document to compare against, so they still work on a document the
        # comparative checks cannot touch. Running them before those also
        # means that where both would fire on one region, the self-contained
        # explanation is the one kept (see _dedupe). A replacement measured
        # against the original it hides is the strongest evidence here, so
        # it goes first of all.
        intrinsic = _check_covered_regions(regions, tolerances)
        intrinsic += _check_intrinsic_resolution(regions, tolerances)
        logger.debug(
            "document %s: gate -> %d distinct comparable asset(s), %d needed for the "
            "comparative checks; self-contained checks found %d",
            document_id, len(comparable), tolerances.min_comparable_regions, len(intrinsic),
        )

        # AC7 — something to measure, but nothing to measure it against.
        # A single full-page scan lands here: its resolution is whatever it
        # is, with no surrounding content to disagree with.
        if len(comparable) < tolerances.min_comparable_regions:
            if not intrinsic:
                return _unable(
                    document_id,
                    f"Only {len(comparable)} distinct image asset(s) large enough to "
                    f"compare (of {len(regions)} placement(s) found); at least "
                    f"{tolerances.min_comparable_regions} are needed to establish what "
                    "the surrounding content looks like.",
                    tolerances, regions, pages,
                )
            # The comparative checks could not run, but a self-contained one
            # did and found something. The document was assessed, so reporting
            # Unable to Assess next to a finding would tell an officer to
            # disregard it.
            return ImageManipulationResult(
                document_id=document_id,
                regions=regions,
                indicators=_dedupe(intrinsic),
                pages_checked=pages,
                tolerances=tolerances,
                unable_to_assess=False,
            )

        indicators = list(intrinsic)
        indicators += _check_resolution(comparable, tolerances)
        indicators += _check_compression(comparable, tolerances)
        before_dedupe = len(indicators)
        indicators = _dedupe(indicators)
        logger.debug(
            "document %s: %d raw indicator(s) -> %d after collapsing duplicates "
            "describing the same region",
            document_id, before_dedupe, len(indicators),
        )

        return ImageManipulationResult(
            document_id=document_id,
            regions=regions,
            indicators=indicators,
            pages_checked=pages,
            tolerances=tolerances,
            unable_to_assess=False,
        )
    finally:
        doc.close()
