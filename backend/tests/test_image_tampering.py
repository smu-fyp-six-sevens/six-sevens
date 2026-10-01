"""Image tampering (SCRUM-65): boundaries, scaling, overlays and quality changes."""

from __future__ import annotations

import io

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from app.fraud_detection.image_manipulation import (
    TamperingTolerances as Tolerances,
    _Placement,
    _check_geometry,
    check_image_tampering,
)

_SLOT = pymupdf.Rect(100, 100, 220, 240)  # a crest-sized mark: 120 x 140 pt


def _art(width: int = 300, height: int = 350, background=(255, 255, 255)) -> Image.Image:
    """Crest-like artwork: shapes and strokes with real edges and colour."""
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, width - 40, height - 90), fill=(220, 40, 50), outline=(30, 30, 30), width=4)
    draw.polygon([(width // 2, 70), (width // 2 + 40, 160), (width // 2 - 40, 160)], fill=(250, 250, 250))
    for k in range(6):
        draw.line((50 + k * 35, height - 70, 70 + k * 35, height - 30), fill=(40, 40, 140), width=6)
    draw.rectangle((60, height - 80, width - 60, height - 60), fill=(200, 160, 30))
    return image


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _pdf(place) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 72), "REPUBLIC OF SINGAPORE", fontsize=16)
    place(page)
    data = doc.tobytes()
    doc.close()
    return data


def _render(page, rect: pymupdf.Rect) -> Image.Image:
    """What the injection tool starts from: the slot as the page shows it, at 2x."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=rect, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _replace(page, rect: pymupdf.Rect, treat) -> None:
    """Paint the original out and draw a treated copy of it on top."""
    cover = pymupdf.Rect(rect.x0 - 0.8, rect.y0 - 0.8, rect.x1 + 0.8, rect.y1 + 0.8)
    copy = treat(_render(page, cover))
    page.draw_rect(cover, color=None, fill=(1, 1, 1), overlay=True)
    page.insert_image(cover, stream=_png(copy), keep_proportion=False)


def _names(result) -> list[str]:
    return sorted({indicator.indicator for indicator in result.indicators})


# --- clean documents ---------------------------------------------------------

def test_clean_document_has_no_indicators():
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        page.insert_image(pymupdf.Rect(300, 100, 500, 250), stream=_png(_art(400, 300)))

    result = check_image_tampering("doc", _pdf(place))
    assert result.unable_to_assess is False
    assert len(result.regions) == 2  # AC1
    assert result.indicators == []


def test_right_angle_rotation_is_not_flagged():
    """Landscape scans are rotated 90 degrees all the time."""
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()), rotate=90)

    assert check_image_tampering("doc", _pdf(place)).indicators == []


# --- replaced by drawing over the original -----------------------------------

def test_painted_over_image_is_an_image_overlay():
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: im)

    result = check_image_tampering("doc", _pdf(place))
    overlay = [i for i in result.indicators if i.indicator == "Image Overlay"]
    assert len(overlay) == 1
    assert overlay[0].severity == "high"
    assert overlay[0].page == 0  # AC5
    assert overlay[0].evidence["hidden_xref"]


def test_painted_out_with_nothing_on_top_is_still_an_overlay():
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        page.draw_rect(_SLOT, color=None, fill=(1, 1, 1), overlay=True)

    result = check_image_tampering("doc", _pdf(place))
    assert _names(result) == ["Image Overlay"]
    assert "painted out" in result.indicators[0].detected


def test_stretched_replacement_is_a_scaling_anomaly():
    """
    The IRAS-logo case: the original hidden under a paper patch, a copy
    stretched 1.85x wide and 0.45x tall drawn from its top-left corner. The
    copy's pixels are stretched too, so its DPI is even on both axes — only
    the original underneath shows what shape it should be.
    """
    def place(page):
        original = _art()
        page.insert_image(_SLOT, stream=_png(original))
        page.insert_image(_SLOT, stream=_png(Image.new("RGB", (60, 70), (255, 255, 255))))
        stretched = original.resize((int(300 * 1.85), int(350 * 0.45)), Image.BICUBIC)
        box = pymupdf.Rect(_SLOT.x0, _SLOT.y0, _SLOT.x0 + _SLOT.width * 1.85,
                           _SLOT.y0 + _SLOT.height * 0.45)
        page.insert_image(box, stream=_png(stretched), keep_proportion=False)

    result = check_image_tampering("doc", _pdf(place))
    scaling = [i for i in result.indicators if i.indicator == "Scaling Anomaly"]
    assert len(scaling) == 1
    assert scaling[0].evidence["change"] == pytest.approx(1.85 / 0.45, rel=0.05)
    assert "wider" in scaling[0].deviation
    assert "Image Overlay" in _names(result)


def test_replacement_with_a_halo_is_a_visible_boundary():
    def halo(image):
        ring = Image.new("L", image.size, 0)
        thickness = max(3, min(image.size) // 16)
        ImageDraw.Draw(ring).rectangle(
            (0, 0, image.width - 1, image.height - 1), outline=170, width=thickness,
        )
        ring = ring.filter(ImageFilter.GaussianBlur(thickness * 0.45))
        return Image.composite(Image.new("RGB", image.size, (233, 233, 233)), image, ring)

    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, halo)

    result = check_image_tampering("doc", _pdf(place))
    assert "Visible Boundary" in _names(result)
    assert "Quality Mismatch" not in _names(result)


def test_replacement_with_adjusted_quality_is_a_quality_mismatch():
    def adjust(image):
        out = ImageEnhance.Brightness(image).enhance(1.18)
        out = ImageEnhance.Contrast(out).enhance(1.32)
        out = ImageEnhance.Color(out).enhance(0.72)
        out = ImageEnhance.Sharpness(out).enhance(2.4)
        return Image.blend(out, Image.effect_noise(out.size, 22).convert("RGB"), 0.12)

    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, adjust)

    result = check_image_tampering("doc", _pdf(place))
    quality = [i for i in result.indicators if i.indicator == "Quality Mismatch"]
    assert len(quality) == 1
    assert {"sharpness", "noise"} & set(quality[0].evidence["altered"])


def test_plain_copy_over_the_original_is_not_a_quality_mismatch():
    """Re-rendering only softens; that alone is not an altered image."""
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: im.filter(ImageFilter.GaussianBlur(0.6)))

    assert "Quality Mismatch" not in _names(check_image_tampering("doc", _pdf(place)))


def test_exact_copy_over_the_original_is_not_a_quality_mismatch():
    """Regression: an untouched copy measures a noise gain of exactly 0, which
    the old "any noise increase" rule could count as altered."""
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: im)

    assert "Quality Mismatch" not in _names(check_image_tampering("doc", _pdf(place)))


def test_darkened_replacement_is_a_quality_mismatch_on_brightness_alone():
    """Darkening softens every edge and adds no grain, so only the brightness
    tolerance can catch it."""
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: ImageEnhance.Brightness(im).enhance(0.7))

    quality = [
        i for i in check_image_tampering("doc", _pdf(place)).indicators
        if i.indicator == "Quality Mismatch"
    ]
    assert len(quality) == 1
    assert quality[0].evidence["altered"] == ["brightness"]


def test_desaturated_replacement_is_a_quality_mismatch_on_colour():
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: ImageEnhance.Color(im).enhance(0.2))

    quality = [
        i for i in check_image_tampering("doc", _pdf(place)).indicators
        if i.indicator == "Quality Mismatch"
    ]
    assert len(quality) == 1
    assert "colour" in quality[0].evidence["altered"]


def test_brightness_and_colour_tolerances_are_configurable():
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art()))
        _replace(page, _SLOT, lambda im: ImageEnhance.Brightness(im).enhance(0.7))

    looser = Tolerances(min_brightness_change=200.0)
    assert "Quality Mismatch" not in _names(check_image_tampering("doc", _pdf(place), looser))


# --- no original to compare against ------------------------------------------

def test_image_drawn_stretched_by_the_page_is_a_scaling_anomaly():
    def place(page):
        page.insert_image(pymupdf.Rect(100, 100, 400, 170), stream=_png(_art()),
                          keep_proportion=False)

    result = check_image_tampering("doc", _pdf(place))
    scaling = [i for i in result.indicators if i.indicator == "Scaling Anomaly"]
    assert len(scaling) == 1
    assert scaling[0].evidence["anisotropy"] > Tolerances().max_draw_anisotropy


def test_rotated_image_is_a_scaling_anomaly():
    placement = _Placement(
        order=0, xref=5, bbox=(0.0, 0.0, 100.0, 100.0),
        # 100pt square rotated 15 degrees: [a b c d e f] of the image matrix
        transform=(96.59, 25.88, -25.88, 96.59, 0.0, 0.0),
        pixel_width=300, pixel_height=300,
    )
    found = _check_geometry(0, placement, Tolerances())
    assert [i.indicator for i in found] == ["Scaling Anomaly"]
    assert found[0].evidence["rotation_deg"] == pytest.approx(15.0, abs=0.1)


def test_pasted_image_with_mismatched_background_is_a_visible_boundary():
    """A grey-backed patch on white paper leaves a visible box."""
    def place(page):
        page.insert_image(_SLOT, stream=_png(_art(background=(228, 228, 228))))

    result = check_image_tampering("doc", _pdf(place))
    assert _names(result) == ["Visible Boundary"]
    assert "background" in result.indicators[0].deviation


def test_pasted_image_with_a_dark_rim_is_a_visible_boundary():
    def place(page):
        art = _art()
        ImageDraw.Draw(art).rectangle((0, 0, art.width - 1, art.height - 1),
                                      outline=(215, 215, 215), width=8)
        page.insert_image(_SLOT, stream=_png(art))

    assert "Visible Boundary" in _names(check_image_tampering("doc", _pdf(place)))


def test_transparent_logo_on_tinted_page_has_no_boundary():
    """Nothing flat inside the image disagrees with the page behind it."""
    def place(page):
        page.draw_rect(page.rect, color=None, fill=(0.98, 0.9, 0.92), overlay=False)
        logo = _art().convert("RGBA")
        logo.putalpha(Image.eval(logo.convert("L"), lambda v: 0 if v > 245 else 255))
        page.insert_image(_SLOT, stream=_png(logo))

    assert check_image_tampering("doc", _pdf(place)).indicators == []


# --- AC8 ---------------------------------------------------------------------

def test_document_with_no_images_is_unable_to_assess():
    result = check_image_tampering("doc", _pdf(lambda page: None))
    assert result.unable_to_assess is True
    assert "No embedded images" in result.unable_to_assess_reason


def test_non_pdf_bytes_are_unable_to_assess():
    result = check_image_tampering("doc", b"not a pdf")
    assert result.unable_to_assess is True


def test_tolerances_are_configurable():
    def place(page):
        page.insert_image(pymupdf.Rect(100, 100, 400, 170), stream=_png(_art()),
                          keep_proportion=False)

    loose = Tolerances(max_draw_anisotropy=50.0)
    assert check_image_tampering("doc", _pdf(place), loose).indicators == []


# --- standalone PNG / JPEG uploads -------------------------------------------

_PATCH = (300, 180, 620, 300)


def _card(fmt: str = "PNG", patch=None, quality: int = 92) -> bytes:
    """An NRIC-like card: tinted background, a white band, text; optionally
    a region pasted back in the way the injection tool does it."""
    doc = pymupdf.open()
    page = doc.new_page(width=450, height=290)
    page.draw_rect(page.rect, color=None, fill=(0.97, 0.89, 0.91))
    page.draw_rect(pymupdf.Rect(0, 40, 450, 75), color=None, fill=(1, 1, 1))
    page.insert_text((20, 30), "REPUBLIC OF SINGAPORE", fontsize=16)
    for row in range(5):
        page.insert_text((150, 110 + row * 30), f"FIELD {row}   NG BEE LENG {row * 7}", fontsize=12)
    pix = page.get_pixmap(dpi=150)
    doc.close()
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if patch is not None:
        image.paste(patch(image.crop(_PATCH)), _PATCH[:2])
    buffer = io.BytesIO()
    image.save(buffer, fmt, **({"quality": quality} if fmt == "JPEG" else {}))
    return buffer.getvalue()


def _brightened(crop: Image.Image) -> Image.Image:
    return ImageEnhance.Brightness(crop).enhance(1.04)


def _overlaps(bbox, box, tol=8) -> bool:
    return not (bbox[2] < box[0] - tol or bbox[0] > box[2] + tol
                or bbox[3] < box[1] - tol or bbox[1] > box[3] + tol)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_clean_image_upload_has_no_indicators(fmt):
    result = check_image_tampering("doc", _card(fmt))
    assert result.unable_to_assess is False
    assert result.bbox_units == "px"
    assert len(result.regions) == 1
    assert result.indicators == []


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_pasted_patch_in_image_upload_is_a_visible_boundary(fmt):
    result = check_image_tampering("doc", _card(fmt, patch=_brightened))
    assert _names(result) == ["Visible Boundary"]
    assert _overlaps(result.indicators[0].bbox, _PATCH)  # AC5, in pixels
    assert result.indicators[0].evidence["seams"] >= Tolerances().min_outline_seams


def test_long_design_band_is_not_an_outline():
    """The white band spans the whole card: a design line, not a patch edge."""
    assert check_image_tampering("doc", _card()).indicators == []


def test_outline_seam_count_is_configurable():
    found = check_image_tampering("doc", _card(patch=_brightened)).indicators[0]
    stricter = Tolerances(min_outline_seams=found.evidence["seams"] + 1)
    assert check_image_tampering("doc", _card(patch=_brightened), stricter).indicators == []


def test_blank_image_upload_is_unable_to_assess():
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (255, 255, 255)).save(buffer, "PNG")
    result = check_image_tampering("doc", buffer.getvalue())
    assert result.unable_to_assess is True
    assert "blank" in result.unable_to_assess_reason


def test_tiny_image_upload_is_unable_to_assess():
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buffer, "PNG")
    result = check_image_tampering("doc", buffer.getvalue())
    assert result.unable_to_assess is True
    assert "40x30" in result.unable_to_assess_reason


def test_corrupt_image_upload_is_unable_to_assess():
    result = check_image_tampering("doc", b"\xff\xd8\xff" + b"\x00" * 64)
    assert result.unable_to_assess is True
    assert "decode" in result.unable_to_assess_reason
