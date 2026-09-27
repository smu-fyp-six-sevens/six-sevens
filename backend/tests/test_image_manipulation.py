"""Image manipulation: resolution/compression mismatch plus Unable to Assess."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image

from app.fraud_detection.image_manipulation_pdf import (
    Tolerances,
    _detail_ratio,
    _jpeg_quantisation_tables,
    check_image_manipulation,
)


def _jpeg(width: int, height: int, quality: int, shade: int = 180) -> bytes:
    """Textured JPEG — a flat fill compresses to almost nothing and makes
    bytes-per-pixel comparisons meaningless."""
    image = Image.new("RGB", (width, height), (shade, shade - 20, shade - 40))
    for x in range(0, width, 7):
        for y in range(0, height, 5):
            image.putpixel((x, y), ((x * 3) % 255, (y * 5) % 255, 120))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def _pdf(place) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    place(page)
    data = doc.tobytes()
    doc.close()
    return data


def _two_consistent_regions(page) -> None:
    page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=_jpeg(600, 400, 85))
    page.insert_image(pymupdf.Rect(56, 340, 356, 540), stream=_jpeg(600, 400, 85, 190))


def _indicators(result) -> list[str]:
    return [indicator.indicator for indicator in result.indicators]


# --- AC1: identification ----------------------------------------------------

def test_embedded_image_regions_are_identified():
    result = check_image_manipulation("doc", _pdf(_two_consistent_regions))
    assert len(result.regions) == 2
    region = result.regions[0]
    assert region.pixel_width == 600 and region.pixel_height == 400
    assert region.codec == "jpeg"
    assert region.effective_dpi == pytest.approx(144.0, abs=1.0)
    assert region.page == 0


def test_consistent_document_produces_no_indicators():
    result = check_image_manipulation("doc", _pdf(_two_consistent_regions))
    assert result.unable_to_assess is False
    assert result.indicators == []


# --- AC2: resolution --------------------------------------------------------

def test_resolution_mismatch_is_flagged():
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(2400, 1600, 85, 200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    assert len(found) == 1
    assert found[0].page == 0
    # AC4: a usable bounding region, not just a page number.
    assert found[0].bbox[2] > found[0].bbox[0]
    assert "144" in found[0].expected
    # Three regions cannot form a majority, so the deviation is reported but
    # not pinned on one region — see test_attribution_is_hedged_below_four_regions.
    assert found[0].severity == "medium"
    assert found[0].evidence["attribution_confident"] is False


# --- AC3: compression -------------------------------------------------------

def test_compression_mismatch_from_quantisation_tables_is_flagged():
    """Same pixel dimensions and same DPI — only the JPEG quality differs."""
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(600, 400, 25, 200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Compression Mismatch"]
    assert len(found) == 1
    assert found[0].severity == "high"
    assert "Resolution Mismatch" not in _indicators(result)


def test_odd_codec_out_is_flagged():
    def place(page):
        _two_consistent_regions(page)
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 600, 400))
        pixmap.set_rect(pixmap.irect, (30, 90, 160))
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), pixmap=pixmap)

    result = check_image_manipulation("doc", _pdf(place))
    assert "Compression Mismatch" in _indicators(result)


def test_one_indicator_per_region_not_three():
    """A pasted region trips several signals; it should report once."""
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(600, 400, 20, 200))

    result = check_image_manipulation("doc", _pdf(place))
    compression = [i for i in result.indicators if i.indicator == "Compression Mismatch"]
    assert len(compression) == 1


# --- AC6: tolerance ---------------------------------------------------------

def test_modest_resolution_spread_is_within_tolerance():
    """1.5x apart — ordinary resizing, not a paste."""
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(900, 600, 85, 200))

    assert check_image_manipulation("doc", _pdf(place)).indicators == []


def test_small_logo_is_identified_but_not_compared():
    """
    A letterhead logo is placed by the template and legitimately differs in
    both resolution and compression. AC6: not automatically flagged.
    """
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(480, 60, 530, 85), stream=_jpeg(1200, 600, 95, 210))

    result = check_image_manipulation("doc", _pdf(place))
    assert len(result.regions) == 3  # AC1: still identified
    assert sum(1 for r in result.regions if not r.comparable) == 1
    assert result.indicators == []


def test_signature_sized_paste_is_still_caught():
    """The other side of the size floor — a pasted signature must not slip through."""
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 600, 266, 670), stream=_jpeg(1800, 600, 25, 200))

    assert check_image_manipulation("doc", _pdf(place)).indicators != []


def test_tolerances_are_configurable():
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(900, 600, 85, 200))

    data = _pdf(place)
    assert check_image_manipulation("doc", data).indicators == []
    strict = check_image_manipulation("doc", data, Tolerances(dpi_ratio=1.2))
    assert "Resolution Mismatch" in _indicators(strict)


# --- AC7: unable to assess --------------------------------------------------

def test_document_with_no_images_is_unable_to_assess():
    def place(page):
        page.insert_text((72, 200), "text only document", fontsize=12)

    result = check_image_manipulation("doc", _pdf(place))
    assert result.unable_to_assess is True
    assert "No embedded images" in result.unable_to_assess_reason


def test_single_full_page_scan_is_unable_to_assess():
    """One region has no surrounding content to disagree with."""
    def place(page):
        page.insert_image(page.rect, stream=_jpeg(1200, 1700, 80))

    result = check_image_manipulation("doc", _pdf(place))
    assert result.unable_to_assess is True
    assert "at least 2" in result.unable_to_assess_reason
    assert len(result.regions) == 1  # AC1 still satisfied


def test_non_pdf_bytes_are_unable_to_assess():
    result = check_image_manipulation("doc", b"not a pdf at all")
    assert result.unable_to_assess is True
    assert "Failed to open" in result.unable_to_assess_reason


# --- JPEG parsing -----------------------------------------------------------

def test_quantisation_tables_differ_by_quality():
    assert _jpeg_quantisation_tables(_jpeg(64, 64, 90)) != _jpeg_quantisation_tables(
        _jpeg(64, 64, 20)
    )


def test_quantisation_tables_match_for_same_quality():
    assert _jpeg_quantisation_tables(_jpeg(64, 64, 85)) == _jpeg_quantisation_tables(
        _jpeg(96, 96, 85)
    )


def test_quantisation_tables_none_for_non_jpeg():
    assert _jpeg_quantisation_tables(b"\x89PNG\r\n\x1a\n rest") is None


# --- calibration against the real corpus ------------------------------------

def test_repeated_template_asset_is_not_flagged():
    """
    A logo and an icon repeated on every page are the issuer's furniture.
    They legitimately differ in resolution, and because they repeat they
    form equal-sized clusters that drag a median between them — which used
    to make each cluster look like the other's outlier. Reproduces a real
    false positive on a five-page bank statement.
    """
    doc = pymupdf.open()
    icon = _jpeg(210, 210, 85)
    banner = _jpeg(1280, 364, 85, 200)
    for _ in range(5):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(56, 56, 102, 102), stream=icon)
        page.insert_image(pymupdf.Rect(150, 56, 450, 141), stream=banner)
    data = doc.tobytes()
    doc.close()

    result = check_image_manipulation("doc", data)
    assert all(r.pages_used_on == 5 for r in result.regions)
    assert result.indicators == []


def test_two_regions_are_compared_against_each_other():
    """
    With two regions the median is their midpoint, so each sits the same
    distance from it and neither can ever be an outlier. They must be
    compared pairwise instead.
    """
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=_jpeg(600, 400, 85))
        page.insert_image(pymupdf.Rect(56, 340, 356, 540), stream=_jpeg(2400, 1600, 85, 200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    # Both are reported: with only two regions there is no majority to say
    # which one is out of place.
    assert len(found) == 2
    assert "the only other image region" in found[0].expected


def test_lossless_images_are_not_compared_by_bytes_per_pixel():
    """
    PNG is lossless, so its bytes-per-pixel reflects image CONTENT, not a
    quality setting: a flat graphic and a photograph differ enormously with
    nothing wrong. Reproduces a real false positive on a clean NRIC card.
    """
    def place(page):
        flat = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 250))
        flat.set_rect(flat.irect, (200, 200, 200))
        page.insert_image(pymupdf.Rect(56, 100, 176, 200), pixmap=flat)

        noisy = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 300, 250))
        for x in range(300):
            for y in range(0, 250, 3):
                noisy.set_pixel(x, y, ((x * 7) % 255, (y * 11) % 255, (x + y) % 255))
        page.insert_image(pymupdf.Rect(56, 250, 176, 350), pixmap=noisy)

    result = check_image_manipulation("doc", _pdf(place))
    assert all(r.codec == "png" for r in result.regions)
    assert [i for i in result.indicators if i.indicator == "Compression Mismatch"] == []


# --- AC2 via intrinsic detail: regions enlarged from something smaller -------
#
# Note on the numbers below. The `_jpeg` helper's per-pixel texture is far
# higher-frequency than real artwork, so a genuine synthetic region scores
# ~0.89 where the real assets in doc_gen_scripts score 0.25-0.50. These tests
# therefore pin the MECHANISM, not the threshold — an enlargement has to be 4x
# here to fall below 0.18, where on real artwork 2x is enough. The threshold
# itself is calibrated against the generated corpus, not against these.

def _enlarged_jpeg(width: int, height: int, factor: int, quality: int = 85,
                   shade: int = 180) -> bytes:
    """
    A JPEG at the full pixel size whose detail only goes down to 1/factor of
    it: built small, then interpolated up. Stored dimensions, and therefore
    the effective DPI the PDF reports, are indistinguishable from genuine.
    """
    small = Image.open(io.BytesIO(_jpeg(width // factor, height // factor, quality, shade)))
    buffer = io.BytesIO()
    small.convert("RGB").resize((width, height), Image.LANCZOS).save(
        buffer, "JPEG", quality=quality
    )
    return buffer.getvalue()


def test_enlarged_region_is_flagged():
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780),
                          stream=_enlarged_jpeg(600, 400, 4, shade=200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    assert len(found) == 1
    assert "enlarged" in found[0].deviation
    assert found[0].bbox[2] > found[0].bbox[0]  # AC4


def test_enlargement_is_caught_although_the_dpi_is_identical():
    """
    The whole point of the check: the enlarged region reports exactly the same
    effective DPI as a genuine one, so nothing comparative can see it.
    """
    def genuine(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(600, 400, 85, 200))

    def enlarged(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780),
                          stream=_enlarged_jpeg(600, 400, 4, shade=200))

    clean = check_image_manipulation("doc", _pdf(genuine))
    dirty = check_image_manipulation("doc", _pdf(enlarged))

    assert [r.effective_dpi for r in clean.regions] == [r.effective_dpi for r in dirty.regions]
    assert clean.indicators == []
    assert "Resolution Mismatch" in _indicators(dirty)


def test_enlarged_region_is_flagged_with_nothing_to_compare_against():
    """
    A document holding one image cannot be assessed comparatively, but an
    enlarged region is measurable on its own — so this must be a finding and
    NOT Unable to Assess, which would tell an officer to disregard it.
    """
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 300),
                          stream=_enlarged_jpeg(600, 400, 4))

    result = check_image_manipulation("doc", _pdf(place))
    assert len(result.regions) == 1
    assert result.unable_to_assess is False
    assert "Resolution Mismatch" in _indicators(result)


def test_repeated_template_asset_is_flagged_when_enlarged():
    """
    Template furniture is exempt from the COMPARATIVE checks because repeated
    assets distort a median. The intrinsic check has no median, and a forged
    letterhead appears on every page — exempting it would skip the case most
    worth catching.
    """
    doc = pymupdf.open()
    banner = _enlarged_jpeg(1280, 364, 4, shade=200)
    for _ in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(150, 56, 450, 141), stream=banner)
    data = doc.tobytes()
    doc.close()

    result = check_image_manipulation("doc", data)
    assert all(r.pages_used_on == 4 for r in result.regions)
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    assert len(found) == 1  # once, not once per page


def test_one_asset_drawn_on_many_pages_is_unable_not_clean():
    """
    Regression: the AC7 gate counted PLACEMENTS while every comparative check
    dedupes to distinct assets first. A single crisp logo drawn on four pages
    passed the gate and came out "clean", reporting a pass for a comparison
    that never happened.
    """
    doc = pymupdf.open()
    banner = _jpeg(1280, 364, 85, 200)
    for _ in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(150, 56, 450, 141), stream=banner)
    data = doc.tobytes()
    doc.close()

    result = check_image_manipulation("doc", data)
    assert len(result.regions) == 4
    assert result.unable_to_assess is True
    assert "distinct image asset" in result.unable_to_assess_reason


def test_crisp_single_region_document_stays_unable_to_assess():
    """The intrinsic check running and finding nothing is not a clean bill."""
    def place(page):
        page.insert_image(page.rect, stream=_jpeg(1200, 1700, 80))

    result = check_image_manipulation("doc", _pdf(place))
    assert result.unable_to_assess is True


def test_enlarged_outlier_is_reported_once_not_twice():
    """
    A region that is both enlarged AND a resolution outlier against its
    neighbours trips two paths to the same conclusion. One row.
    """
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780),
                          stream=_enlarged_jpeg(2400, 1600, 4, shade=200))

    result = check_image_manipulation("doc", _pdf(place))
    assert len([i for i in result.indicators if i.indicator == "Resolution Mismatch"]) == 1


def test_detail_ratio_tolerance_is_configurable():
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780),
                          stream=_enlarged_jpeg(600, 400, 2, shade=200))

    data = _pdf(place)
    # A 2x enlargement of this synthetic texture scores ~0.29, above default.
    assert check_image_manipulation("doc", data).indicators == []
    strict = check_image_manipulation("doc", data, Tolerances(min_detail_ratio=0.4))
    assert "Resolution Mismatch" in _indicators(strict)


# --- the detail measurement itself ------------------------------------------

def test_detail_ratio_collapses_under_enlargement():
    genuine = _detail_ratio(_jpeg(600, 400, 85))
    enlarged = _detail_ratio(_enlarged_jpeg(600, 400, 4))
    assert genuine > 0.5
    assert enlarged < 0.18
    assert genuine / enlarged > 4


def test_detail_ratio_survives_recompression():
    """
    Not a compression proxy. Recompressing hard degrades quality without
    removing spatial resolution, and this measurement must say so — otherwise
    every heavily-compressed legitimate scan becomes a finding.
    """
    assert _detail_ratio(_jpeg(600, 400, 25)) > 0.18


def test_detail_ratio_is_none_when_unmeasurable():
    assert _detail_ratio(_jpeg(40, 40, 85)) is None       # too small
    assert _detail_ratio(b"\x89PNG not a real image") is None  # undecodable


# --- attribution confidence at low region counts ----------------------------

def test_attribution_is_hedged_below_four_regions():
    """
    Altering one of three regions moves the median enough that a different,
    untouched region can become the most distant from it. The document IS
    inconsistent, but which region is the altered one is not knowable from
    resolution alone, so the finding must not read as a verdict on one region.
    """
    def place(page):
        _two_consistent_regions(page)
        page.insert_image(pymupdf.Rect(56, 580, 356, 780), stream=_jpeg(2400, 1600, 85, 200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    assert len(found) == 1
    assert found[0].evidence["attribution_confident"] is False
    assert found[0].severity == "medium"  # capped: less evidence, lower claim
    assert "no majority" in found[0].summary
    assert "ambiguous" in found[0].deviation
    # Every candidate's DPI travels with the finding, so a reviewer can see the
    # whole spread rather than only the region this row happens to name.
    assert len(found[0].evidence["all_region_dpi"]) == 3


def test_attribution_is_confident_from_four_regions():
    """With a real majority, the outlier can be named and severity earned."""
    def place(page):
        for n, top in enumerate((100, 240, 380, 520)):
            page.insert_image(
                pymupdf.Rect(56, top, 356, top + 120), stream=_jpeg(600, 240, 85, 180 + n * 5)
            )
        page.insert_image(pymupdf.Rect(56, 660, 356, 780), stream=_jpeg(3000, 1200, 85, 205))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Resolution Mismatch"]
    assert len(found) == 1
    assert found[0].evidence["attribution_confident"] is True
    assert found[0].severity == "high"
    assert "no majority" not in found[0].summary


# --- AC3 at exactly two regions ---------------------------------------------
#
# The same median degeneracy the resolution check handles: with two values the
# median is their midpoint, so each sits the same distance from it and neither
# can cross a tolerance. Two JPEGs also form a 1-1 split with no majority, so
# the odd-one-out logic never fires. Both paths have to compare pairwise.

def _flat_jpeg(width: int, height: int, quality: int, shade: int) -> bytes:
    """Same encoder and quality as _jpeg, but almost no content — so the
    quantisation tables match while bytes-per-pixel differs enormously."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (shade, shade, shade)).save(
        buffer, "JPEG", quality=quality
    )
    return buffer.getvalue()


def test_two_jpegs_with_different_tables_are_both_reported():
    """
    Reproduces a real miss: a logo re-saved at a different quality is visually
    identical and was passing as clean, because one JPEG against one JPEG is a
    1-1 split with no majority.
    """
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=_jpeg(600, 400, 92))
        page.insert_image(pymupdf.Rect(56, 340, 356, 540), stream=_jpeg(600, 400, 55, 190))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Compression Mismatch"]
    assert len(found) == 2  # neither can be singled out
    assert all(i.evidence["attribution_confident"] is False for i in found)
    assert all(i.severity == "medium" for i in found)
    assert "no majority" in found[0].summary


def test_two_matching_jpegs_are_not_reported():
    """The other side of it: same encoder, same quality, nothing to say."""
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=_jpeg(600, 400, 85))
        page.insert_image(pymupdf.Rect(56, 340, 356, 540), stream=_jpeg(600, 400, 85, 190))

    result = check_image_manipulation("doc", _pdf(place))
    assert [i for i in result.indicators if i.indicator == "Compression Mismatch"] == []


def test_two_lossy_regions_are_compared_pairwise_on_bytes_per_pixel():
    """
    Same quantisation tables, so only the data density differs. Against their
    own midpoint two regions 13x apart score 3.6x each — this used to sit close
    enough to the tolerance to be luck rather than logic.
    """
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 300), stream=_jpeg(600, 400, 85))
        page.insert_image(pymupdf.Rect(56, 340, 356, 540),
                          stream=_flat_jpeg(600, 400, 85, 190))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Compression Mismatch"]
    assert len(found) == 2
    assert "bytes/pixel" in found[0].detected
    assert "the only other lossy region" in found[0].expected


def test_three_jpegs_still_name_the_odd_one_out():
    """With a majority the attribution is earned, and severity with it."""
    def place(page):
        page.insert_image(pymupdf.Rect(56, 100, 356, 280), stream=_jpeg(600, 360, 92))
        page.insert_image(pymupdf.Rect(56, 300, 356, 480), stream=_jpeg(600, 360, 92, 190))
        page.insert_image(pymupdf.Rect(56, 520, 356, 700), stream=_jpeg(600, 360, 45, 200))

    result = check_image_manipulation("doc", _pdf(place))
    found = [i for i in result.indicators if i.indicator == "Compression Mismatch"]
    assert len(found) == 1
    assert found[0].severity == "high"
