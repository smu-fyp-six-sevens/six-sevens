from __future__ import annotations

import io
import shutil
import subprocess

import numpy as np
import pymupdf
import pytest
from PIL import Image, PngImagePlugin

from app.fraud_detection.authenticity import ai_generation
from app.fraud_detection.authenticity.ai_generation import (
    AIGenerationConfig,
    _estimate_quality,
    _ijg_table,
    _provenance,
    check_ai_generation,
)

requires_exiftool = pytest.mark.skipif(shutil.which("exiftool") is None, reason="exiftool not installed")


# --- fixtures ----------------------------------------------------------------------

def _scene(n: int = 768, noise: float = 3.0, seed: int = 0, falloff: float = 1.6) -> np.ndarray:
    """
    A photo-like RGB image: 1/f^falloff texture (no resampling of its own)
    plus sensor-like noise. A higher falloff means less fine texture.
    """
    rng = np.random.default_rng(seed)
    fy, fx = np.meshgrid(np.fft.fftfreq(n), np.fft.fftfreq(n), indexing="ij")
    base = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(n, n))) / (np.hypot(fy, fx) + 1 / n) ** falloff))
    gray = (base - base.min()) / np.ptp(base) * 200 + 20
    rgb = np.stack([gray, gray * 0.9 + 10, gray * 0.8 + 20], axis=-1)
    return np.clip(rgb + rng.normal(0, noise, rgb.shape), 0, 255)


def _with_checkerboard(rgb: np.ndarray, period: int = 2, amplitude: float = 1.0, seed: int = 1) -> np.ndarray:
    """The fixed periodic pattern a generator's upsampling layers leave behind."""
    rng = np.random.default_rng(seed)
    cell = rng.normal(0, 1, (period, period))
    cell -= cell.mean()
    yy, xx = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]]
    return np.clip(rgb + amplitude * cell[yy % period, xx % period][..., None], 0, 255)


def _png(rgb: np.ndarray, info: PngImagePlugin.PngInfo | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()


def _jpeg(rgb: np.ndarray, quality: int, then: int | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buffer, "JPEG", quality=quality)
    if then is None:
        return buffer.getvalue()
    second = io.BytesIO()
    Image.open(io.BytesIO(buffer.getvalue())).save(second, "JPEG", quality=then)
    return second.getvalue()


def _logo() -> np.ndarray:
    logo = np.full((300, 300, 3), 255.0)
    logo[50:250, 50:250] = (200, 30, 30)
    return logo


def _pdf_with_image(image_bytes: bytes | None) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Statement of Account")
    if image_bytes is not None:
        page.insert_image(pymupdf.Rect(72, 100, 372, 400), stream=image_bytes)
    data = doc.tobytes()
    doc.close()
    return data


def _indicator(result, name: str):
    return next(i for i in result.images[0].indicators if i.indicator == name)


# --- AC1: the individual indicators ------------------------------------------------

def test_spectral_peaks_flag_a_generator_checkerboard() -> None:
    clean = check_ai_generation("doc-1", _png(_scene()))
    generated = check_ai_generation("doc-1", _png(_with_checkerboard(_scene())))

    assert _indicator(clean, "Spectral Peaks").strength == 0.0
    peaks = _indicator(generated, "Spectral Peaks")
    assert peaks.strength == 1.0
    assert "2px period" in peaks.detail


def test_spectral_peaks_ignore_a_bayer_sensor_trace() -> None:
    """
    A camera's Bayer sensor leaves a diagonal 2px checkerboard in genuine
    photos; it must not read as a generator's upsampling pattern.
    """
    yy, xx = np.mgrid[0:768, 0:768]
    bayer = np.where((yy + xx) % 2 == 0, 1.0, -1.0)[..., None]
    result = check_ai_generation("doc-1", _png(np.clip(_scene() + bayer, 0, 255)))
    assert _indicator(result, "Spectral Peaks").strength == 0.0


def test_spectral_peaks_ignore_jpeg_block_grid() -> None:
    """JPEG's 8x8 blocks are periodic too; a plain JPEG must not read as upsampled."""
    result = check_ai_generation("doc-1", _jpeg(_scene(), 75))
    assert _indicator(result, "Spectral Peaks").strength == 0.0


def test_noise_level_flags_an_unnaturally_clean_photo() -> None:
    # Smooth enough that, without added noise, its smooth areas really are clean.
    noisy = check_ai_generation("doc-1", _png(_scene(noise=3.0, falloff=2.0)))
    clean = check_ai_generation("doc-1", _png(_scene(noise=0.0, falloff=2.0)))

    assert _indicator(noisy, "Noise Level").strength == 0.0
    level = _indicator(clean, "Noise Level")
    assert level.assessed and level.strength > 0.5


def test_double_compression_finds_the_earlier_quality() -> None:
    single = check_ai_generation("doc-1", _jpeg(_scene(), 90))
    double = check_ai_generation("doc-1", _jpeg(_scene(), 60, then=90))

    assert _indicator(single, "Double Compression").strength == 0.0
    ghost = _indicator(double, "Double Compression")
    assert ghost.strength == 1.0
    assert "quality 60" in ghost.detail


def test_compression_indicators_skip_formats_without_jpeg_history() -> None:
    indicator = _indicator(check_ai_generation("doc-1", _png(_scene())), "Double Compression")
    assert not indicator.assessed
    assert "PNG" in (indicator.reason or "")


def test_estimate_quality_recognises_standard_libjpeg_tables() -> None:
    image = Image.open(io.BytesIO(_jpeg(_scene(256), 83)))
    assert _estimate_quality(image) == (83, True)


def test_provenance_reads_iptc_digital_source_type() -> None:
    tags = {
        "XMP-iptcExt:DigitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
    }
    indicator = _provenance(tags, AIGenerationConfig())
    assert indicator.strength == 0.95
    assert "trainedalgorithmicmedia" in indicator.detail


def test_provenance_reads_c2pa_claim_generator() -> None:
    tags = {"JUMBF:JUMDLabel": "c2pa", "JUMBF:ClaimGenerator": "Adobe_Firefly/1.0"}
    indicator = _provenance(tags, AIGenerationConfig())
    assert indicator.strength == 0.9
    assert "C2PA manifest present" in indicator.detail


def test_provenance_absence_is_not_evidence() -> None:
    indicator = _provenance({"IFD0:Make": "Apple", "IFD0:Model": "iPhone 15"}, AIGenerationConfig())
    assert indicator.strength == 0.0


def test_provenance_unassessed_without_exiftool() -> None:
    assert not _provenance(None, AIGenerationConfig()).assessed


@requires_exiftool
def test_stable_diffusion_parameters_chunk_sets_the_score_floor() -> None:
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "a bank statement\nSteps: 20, Sampler: Euler a, CFG scale: 7, Seed: 1")
    result = check_ai_generation("doc-1", _png(_scene(), info))

    assert result.score == 0.95
    assert result.likelihood == "high"


@requires_exiftool
def test_iptc_declaration_written_by_exiftool_is_read_back(tmp_path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(_jpeg(_scene(), 90))
    subprocess.run(
        ["exiftool", "-overwrite_original",
         "-XMP-iptcExt:DigitalSourceType=http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia",
         str(path)],
        check=True, capture_output=True,
    )
    result = check_ai_generation("doc-1", path.read_bytes())
    assert result.score >= 0.95


# --- AC2/AC3: the score and its breakdown --------------------------------------

def test_contributions_add_up_to_the_score() -> None:
    for data in (_png(_with_checkerboard(_scene())), _jpeg(_scene(), 60, then=90), _png(_scene())):
        image = check_ai_generation("doc-1", data).images[0]
        assert sum(i.contribution for i in image.indicators) == pytest.approx(image.score, abs=1e-3)


def test_every_indicator_is_recorded_with_value_and_weight() -> None:
    image = check_ai_generation("doc-1", _jpeg(_with_checkerboard(_scene()), 95)).images[0]
    names = {i.indicator for i in image.indicators}
    assert names == {
        "Spectral Peaks", "Noise Level", "Noise Signal Dependence", "Error Level Analysis",
        "Double Compression", "Capture Signature", "Provenance",
    }
    for indicator in image.indicators:
        if indicator.assessed:
            assert indicator.value is not None
            assert indicator.detail
        else:
            assert indicator.reason


def test_generated_image_outscores_a_photo() -> None:
    photo = check_ai_generation("doc-1", _png(_scene()))
    generated = check_ai_generation("doc-1", _png(_with_checkerboard(_scene(noise=0.0, falloff=2.0))))

    assert generated.score > photo.score
    assert generated.likelihood in ("medium", "high")
    assert photo.likelihood == "low"


def test_indicators_are_logged(caplog) -> None:
    caplog.set_level("INFO", logger="fraud_detection.ai_generation")
    check_ai_generation("doc-1", _png(_scene()))
    logged = [r.getMessage() for r in caplog.records]
    assert any("indicator='Spectral Peaks'" in m and "contribution=" in m for m in logged)
    assert any("score=" in m for m in logged)


# --- what gets analysed -----------------------------------------------------------

def test_graphics_are_listed_but_not_scored() -> None:
    result = check_ai_generation("doc-1", _png(_logo()))

    assert result.unable_to_assess
    assert (result.unable_to_assess_reason or "").startswith("Nothing to check: This image is a flat graphic")
    assert result.images[0].photographic is False


def test_pdf_embedded_photo_is_scored_with_its_page() -> None:
    pdf = _pdf_with_image(_jpeg(_with_checkerboard(_scene()), 95))
    result = check_ai_generation("doc-1", pdf)

    assert not result.unable_to_assess
    assert result.bbox_units == "pt"
    image = result.images[0]
    assert image.source == "embedded"
    assert image.page == 0
    assert image.bbox is not None
    assert _indicator(result, "Capture Signature").assessed is False


def test_pdf_without_images_is_unable_to_assess() -> None:
    result = check_ai_generation("doc-1", _pdf_with_image(None))

    assert result.unable_to_assess
    assert result.score == 0.0
    assert (result.unable_to_assess_reason or "").startswith("Nothing to check: this document contains no")
    assert "not a sign of fraud" in (result.unable_to_assess_reason or "")


def test_small_embedded_images_are_skipped() -> None:
    pdf = _pdf_with_image(_png(_scene(64)))
    assert check_ai_generation("doc-1", pdf).unable_to_assess


def test_undecodable_upload_is_unable_to_assess() -> None:
    result = check_ai_generation("doc-1", b"not an image")
    assert result.unable_to_assess
    assert (result.unable_to_assess_reason or "").startswith("Could not run:")


def test_pdf_with_only_graphics_names_them() -> None:
    logo = io.BytesIO()
    Image.fromarray(_logo().astype(np.uint8)).save(logo, "PNG")
    result = check_ai_generation("doc-1", _pdf_with_image(logo.getvalue()))

    assert result.unable_to_assess
    assert "only image (300 × 300 px PNG) is a flat graphic" in (result.unable_to_assess_reason or "")


def test_exiftool_missing_degrades_to_pixel_only(monkeypatch) -> None:
    monkeypatch.setattr(ai_generation, "_run_exiftool", lambda blobs, config: None)
    result = check_ai_generation("doc-1", _png(_scene()))

    assert not result.unable_to_assess
    assert _indicator(result, "Provenance").assessed is False
    assert _indicator(result, "Capture Signature").assessed is False


# --- Capture Signature -------------------------------------------------------------

_IPHONE_TAGS = {"ICC_Profile:ProfileDescription": "Display P3", "ICC-header:ProfileCMMType": "Apple Computer Inc."}


def _camera_jpeg(rgb: np.ndarray) -> bytes:
    """A JPEG with custom (non-libjpeg) tables at ~q96, like an iPhone's encoder."""
    table = [int(v) + (1 if i % 7 == 0 else 0) for i, v in enumerate(_ijg_table(96))]
    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buffer, "JPEG", qtables=[table, table])
    return buffer.getvalue()


def _with_tags(monkeypatch, tags: dict) -> None:
    monkeypatch.setattr(ai_generation, "_run_exiftool", lambda blobs, config: [tags for _ in blobs])


def test_capture_signature_recognises_a_phone_photo(monkeypatch) -> None:
    _with_tags(monkeypatch, _IPHONE_TAGS)
    capture = _indicator(check_ai_generation("doc-1", _camera_jpeg(_scene())), "Capture Signature")

    assert capture.value == "camera"
    assert capture.contribution == 0.0
    assert "custom JPEG tables" in capture.detail and "Display P3" in capture.detail


def test_camera_capture_halves_noise_level(monkeypatch) -> None:
    """A clean low-light phone photo is phone noise reduction, not generation."""
    clean = _scene(noise=0.0, falloff=2.0)
    _with_tags(monkeypatch, {})
    stripped = _indicator(check_ai_generation("doc-1", _camera_jpeg(clean)), "Noise Level")
    _with_tags(monkeypatch, _IPHONE_TAGS)
    captured = _indicator(check_ai_generation("doc-1", _camera_jpeg(clean)), "Noise Level")

    assert captured.contribution == pytest.approx(stripped.contribution / 2, abs=1e-3)
    assert "camera capture" in captured.detail


def test_capture_signature_is_unknown_for_a_forwarded_photo(monkeypatch) -> None:
    """WhatsApp-style: standard tables at q80, everything stripped."""
    _with_tags(monkeypatch, {})
    # 768x700: not a generator size (768x768 is one).
    capture = _indicator(check_ai_generation("doc-1", _jpeg(_scene()[:, :700], 80)), "Capture Signature")
    assert capture.value == "unknown"
    assert capture.strength == 0.5


def test_capture_signature_flags_a_generator_output_size(monkeypatch) -> None:
    _with_tags(monkeypatch, {})
    capture = _indicator(check_ai_generation("doc-1", _png(_scene(1024))), "Capture Signature")
    assert capture.value == "generator-shaped"
    assert "1024x1024" in capture.detail


def test_capture_signature_treats_a_p3_png_as_a_screenshot(monkeypatch) -> None:
    _with_tags(monkeypatch, _IPHONE_TAGS)
    capture = _indicator(check_ai_generation("doc-1", _png(_scene()[:, :700])), "Capture Signature")
    assert capture.value == "unknown"
    assert "screenshot" in capture.detail
