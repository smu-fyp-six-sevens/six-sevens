# ai_generation.py
"""
Digital Authenticity Specialist — traces of AI generation (SCRUM-67).

Estimates how likely it is that the images in a document were produced or
reworked by a generative model, from pixel- and compression-level statistics
plus whatever provenance the file declares about itself. Each image gets an
AI-generation likelihood score (AC2) built from named indicators, and every
indicator is recorded with its measured value, how strongly it points at AI
generation, and how much it contributed to the score (AC3).

Indicators (AC1):

  Spectral Peaks          Generator upsampling layers leave a fixed periodic
                          pattern (a "checkerboard") at periods of 2-8 px,
                          and resampling by a non-integer factor makes the
                          residual's magnitude periodic. Both show up as
                          isolated peaks in the averaged power spectrum.
  Noise Level             Camera sensors add noise everywhere; generated
                          images are usually far cleaner in smooth areas.
  Noise Signal Dependence Sensor noise grows with brightness; noise a model
                          synthesises has no reason to. Recorded only (weight
                          0): phone processing breaks this on real photos.
  Error Level Analysis    Re-saving at a fixed JPEG quality and comparing:
                          regions with a different compression history stand
                          out against the rest.
  Double Compression      "JPEG ghosts": recompressing at a range of qualities
                          dips at any quality the image was saved at before.
  Capture Signature       Uploads only: does the file carry a phone/camera's
                          fingerprint (custom JPEG tables, a device colour
                          profile, camera EXIF), a generator's (preset output
                          sizes), or neither (re-encoded by a messaging app or
                          stripped)? A camera capture also halves Noise
                          Level's weight, since phone noise reduction makes
                          real low-light photos look "too clean".
  Provenance              Read via ExifTool: a C2PA manifest, an IPTC
                          DigitalSourceType, or a generator's own tags (e.g.
                          Stable Diffusion's "parameters" chunk). A declared
                          AI source sets a floor under the score; absence
                          proves nothing, since metadata is trivially stripped.

What is analysed: a standalone PNG/JPEG upload, or the raster images embedded
in a PDF (above a size floor, largest first). Rendered PDF pages are not —
vector text rendered to pixels is synthetic by construction and would read as
"generated". Images that are graphics rather than photographs or scans (flat
fills, few colours: logos, crests, screenshots of rendered pages) are listed
but not scored at pixel level for the same reason.

Scoring: score = sum(weight x strength) over the pixel/compression indicators
that could be measured, so contributions add up to the score exactly. Weights
sum to 1.0 across all of them; `coverage` reports how much of that weight was
measurable for a given image. A provenance declaration then raises the score
to its floor, recorded as Provenance's own contribution. The document's score
is its most suspicious image (or the file-level provenance floor).

CALIBRATION: thresholds and weights below are a starting point, checked only
against synthetic test images (see tests/test_ai_generation.py) — not against
real AI-generated documents versus real scans. Treat scores as relative until
they have been calibrated on such a corpus.

Image/statistics based, no LLM tokens. Synchronous and CPU-bound — callers
should run this via asyncio.to_thread, the same as the other checks here.
"""
from __future__ import annotations

import io
import json
import logging
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
import pymupdf
from PIL import Image, ImageFilter
from pydantic import BaseModel, Field

logger = logging.getLogger("fraud_detection.ai_generation")

BBox = tuple[float, float, float, float]
IndicatorName = Literal[
    "Spectral Peaks",
    "Noise Level",
    "Noise Signal Dependence",
    "Error Level Analysis",
    "Double Compression",
    "Capture Signature",
    "Provenance",
]
Likelihood = Literal["low", "medium", "high"]

# Indicators that make up the weighted pixel/compression score. Provenance is
# deliberately not one of them — it acts as a floor (see _score).
_SCORED: tuple[IndicatorName, ...] = (
    "Spectral Peaks",
    "Noise Level",
    "Noise Signal Dependence",
    "Error Level Analysis",
    "Double Compression",
    "Capture Signature",
)


def _default_weights() -> dict[str, float]:
    # Strongest, most AI-specific cue first. Noise Signal Dependence is
    # measured and recorded but not scored: on two real iPhone photos it
    # read -0.44 and -0.60 (full strength), because phone noise reduction and
    # tone mapping break the "noise grows with brightness" premise. Capture
    # Signature stays modest: a missing signature mostly means a messaging
    # app re-encoded the file, not that a model made it.
    return {
        "Spectral Peaks": 0.35,
        "Noise Level": 0.30,
        "Noise Signal Dependence": 0.0,
        "Error Level Analysis": 0.10,
        "Double Compression": 0.10,
        "Capture Signature": 0.15,
    }


class AIGenerationConfig(BaseModel):
    """Thresholds and weights. Uncalibrated — see the module docstring."""

    weights: dict[str, float] = Field(default_factory=_default_weights)

    # --- which images ---
    # Below this on either side an image is an icon; its statistics are noise.
    min_image_side: int = Field(default=128, ge=16)
    # Largest N embedded images only; a PDF can embed hundreds of tiles.
    max_images: int = Field(default=8, ge=1)
    # Analysis runs on a centre crop (never a resize, which would destroy the
    # very artefacts being measured), aligned to the 8px JPEG grid.
    max_analysis_side: int = Field(default=1536, ge=256)
    # Graphic vs photographic: a share of pixels identical to both their right
    # and lower neighbour above this, or fewer distinct colours than this.
    graphic_flat_fraction: float = Field(default=0.5, gt=0.0, lt=1.0)
    graphic_min_colours: int = Field(default=64, ge=2)

    # --- Spectral Peaks ---
    spectral_tile: int = Field(default=256, ge=64)
    spectral_max_tiles: int = Field(default=16, ge=1)
    spectral_periods: tuple[int, ...] = (2, 3, 4, 5, 6, 8)
    # Peak-to-local-background power ratio. Clean synthetic photos measured
    # 1-3; a checkerboard of amplitude 0.5 grey levels measured ~430, a 1.5x
    # resample ~750. Strength ramps on a log scale between these two.
    spectral_ratio_floor: float = Field(default=5.0, gt=1.0)
    spectral_ratio_ceiling: float = Field(default=20.0, gt=1.0)

    # --- Noise ---
    noise_block: int = Field(default=32, ge=8)
    # Blocks whose denoised image curves less than this (mean |Laplacian|/4,
    # grey levels) are where the residual is mostly noise rather than edges.
    noise_smooth_max_curvature: float = Field(default=1.0, gt=0.0)
    min_noise_blocks: int = Field(default=8, ge=2)
    # Residual std (grey levels) at/below which strength is 1, at/above 0.
    noise_sigma_low: float = Field(default=0.5, ge=0.0)
    noise_sigma_high: float = Field(default=1.5, gt=0.0)
    # Spearman rho of noise vs brightness: >= high -> 0, <= low -> 1.
    noise_rho_high: float = 0.3
    noise_rho_low: float = -0.1
    # Brightness range across smooth blocks needed to measure a dependence.
    noise_min_brightness_range: float = Field(default=40.0, gt=0.0)

    # --- Error Level Analysis ---
    ela_quality: int = Field(default=90, ge=50, le=100)
    ela_block: int = Field(default=16, ge=4)
    ela_min_blocks: int = Field(default=8, ge=2)
    # 95th-percentile block error over the median block error.
    ela_ratio_floor: float = Field(default=2.0, gt=0.0)
    ela_ratio_ceiling: float = Field(default=6.0, gt=0.0)
    # Grey levels added to both sides of the ratio (see _measure_ela).
    ela_error_floor: float = Field(default=1.0, gt=0.0)

    # --- Double Compression ---
    ghost_min_quality: int = Field(default=30, ge=5)
    ghost_step: int = Field(default=2, ge=1)
    ghost_crop: int = Field(default=512, ge=64)
    # Relative depth of the dip. Single-compressed test images measured 0,
    # double-compressed 0.74-0.81.
    ghost_depth_floor: float = Field(default=0.05, ge=0.0)
    ghost_depth_ceiling: float = Field(default=0.3, gt=0.0)

    # --- Capture Signature ---
    # Camera encoders save high: both real iPhone uploads used custom tables
    # at quality 96, while web/messaging exports sit around 70-85.
    capture_min_camera_quality: int = Field(default=90, ge=1, le=100)
    # Signals needed to call a file a camera capture. One alone is too easy
    # to have by accident (any editor can embed a P3 profile).
    capture_min_camera_signals: int = Field(default=2, ge=1)
    # Noise Level's weight is multiplied by this for a camera capture: phone
    # noise reduction made a real low-light iPhone photo read as clean (0.83
    # grey levels) as a rendered fake card (0.82).
    noise_weight_when_captured: float = Field(default=0.5, ge=0.0, le=1.0)

    # --- bands ---
    medium_threshold: float = Field(default=0.35, gt=0.0, lt=1.0)
    high_threshold: float = Field(default=0.65, gt=0.0, le=1.0)

    exiftool_timeout_s: float = Field(default=30.0, gt=0.0)


class AIIndicator(BaseModel):
    """AC3 — one measurement and what it did to the score."""

    indicator: IndicatorName
    assessed: bool
    # The raw measurement (a ratio, a sigma, a quality, a tag value...).
    value: float | str | None = None
    detail: str
    # 0-1: how strongly this measurement alone points at AI generation.
    strength: float = 0.0
    weight: float = 0.0
    # Points added to the image's score. Contributions sum to the score.
    contribution: float = 0.0
    reason: str | None = None  # why it could not be assessed


class AIImageAssessment(BaseModel):
    source: Literal["upload", "embedded"]
    page: int | None = None  # 0-based, like the other image checks
    xref: int | None = None
    bbox: BBox | None = None
    pixel_width: int
    pixel_height: int
    format: str
    photographic: bool
    score: float  # AC2
    likelihood: Likelihood
    # Share of the pixel/compression weight that could be measured.
    coverage: float
    indicators: list[AIIndicator]  # AC3


class AIGenerationResult(BaseModel):
    document_id: str
    score: float  # AC2: the most suspicious image, or the file's provenance floor
    likelihood: Likelihood
    images: list[AIImageAssessment]
    # PDFs only: what the PDF file itself declares (uploads carry it per image).
    file_provenance: AIIndicator | None = None
    config: AIGenerationConfig
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
    bbox_units: Literal["pt", "px"] = "pt"


# --- helpers -------------------------------------------------------------------

def _ramp(value: float, zero_at: float, one_at: float) -> float:
    """0 at `zero_at`, 1 at `one_at`, linear between, clamped. Works either way round."""
    if one_at == zero_at:
        return 1.0 if value >= one_at else 0.0
    return float(min(1.0, max(0.0, (value - zero_at) / (one_at - zero_at))))


def _likelihood(score: float, config: AIGenerationConfig) -> Likelihood:
    if score >= config.high_threshold:
        return "high"
    if score >= config.medium_threshold:
        return "medium"
    return "low"


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"%PDF"):
        return "pdf"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    return None


def _not_assessed(name: IndicatorName, reason: str, config: AIGenerationConfig) -> AIIndicator:
    return AIIndicator(
        indicator=name, assessed=False, detail="Not measured.",
        weight=config.weights.get(name, 0.0), reason=reason,
    )


def _measured(
    name: IndicatorName, value: float | str, detail: str, strength: float, config: AIGenerationConfig
) -> AIIndicator:
    weight = config.weights.get(name, 0.0)
    return AIIndicator(
        indicator=name, assessed=True, value=value, detail=detail,
        strength=round(strength, 3), weight=weight, contribution=round(weight * strength, 4),
    )


def _blocks(array: np.ndarray, size: int) -> np.ndarray:
    """size x size tiles as rows, dropping any partial tiles at the edges."""
    h, w = (array.shape[0] // size) * size, (array.shape[1] // size) * size
    return array[:h, :w].reshape(h // size, size, w // size, size).swapaxes(1, 2).reshape(-1, size * size)


def _centre_crop(array: np.ndarray, side: int) -> np.ndarray:
    """Centre crop to at most side x side, with offsets on the 8px JPEG grid."""
    h, w = array.shape[:2]
    ch, cw = min(h, side), min(w, side)
    y0 = ((h - ch) // 2) // 8 * 8
    x0 = ((w - cw) // 2) // 8 * 8
    return array[y0:y0 + ch, x0:x0 + cw]


def _is_photographic(rgb: np.ndarray, config: AIGenerationConfig) -> tuple[bool, str]:
    gray = rgb.astype(np.int16).sum(axis=2)
    flat = (gray[:-1, :-1] == gray[:-1, 1:]) & (gray[:-1, :-1] == gray[1:, :-1])
    flat_fraction = float(flat.mean()) if flat.size else 1.0
    packed = (rgb[..., 0].astype(np.uint32) << 16) | (rgb[..., 1].astype(np.uint32) << 8) | rgb[..., 2]
    colours = int(np.unique(packed).size)
    photographic = flat_fraction <= config.graphic_flat_fraction and colours >= config.graphic_min_colours
    return photographic, f"{flat_fraction:.0%} flat pixels, {colours} distinct colours"


# --- Spectral Peaks --------------------------------------------------------------

def _laplacian(gray: np.ndarray) -> np.ndarray:
    return (
        4 * gray[1:-1, 1:-1]
        - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    )


def _averaged_spectrum(residual: np.ndarray, config: AIGenerationConfig) -> np.ndarray | None:
    """Welch-style: windowed power spectra of non-overlapping tiles, averaged."""
    t = config.spectral_tile
    h, w = residual.shape
    if h < t or w < t:
        return None
    window = np.outer(np.hanning(t), np.hanning(t))
    total = np.zeros((t, t))
    count = 0
    for y in range(0, h - t + 1, t):
        for x in range(0, w - t + 1, t):
            tile = residual[y:y + t, x:x + t]
            total += np.abs(np.fft.fft2((tile - tile.mean()) * window)) ** 2
            count += 1
            if count >= config.spectral_max_tiles:
                return np.fft.fftshift(total / count)
    return np.fft.fftshift(total / count)


def _peak_ratio(power: np.ndarray, period: int) -> float:
    """
    Power at the period's frequency against the median of its surroundings,
    on the horizontal and vertical axes only, counted as the weaker of the two:
    a lone axis is more often text lines or a scanner's banding.

    The diagonal is deliberately not measured. A camera's Bayer sensor samples
    green on a diagonal checkerboard (quad-Bayer phone sensors repeat it at
    4px), and demosaicing leaves exactly that diagonal peak in genuine photos
    — two real iPhone NRIC photos measured 73-79 there, against 6-9 on the
    axes. Generator upsampling works along rows and columns, so it shows on
    the axes: the AI-generated passport in GLDB-55 measured 18 and 122 there,
    and 1.3 on the diagonal.
    """
    t = power.shape[0]
    c = t // 2
    f = int(round(t / period))

    def ratio_at(dy: int, dx: int) -> float:
        ys = [(c + dy + i) % t for i in range(-6, 7)]
        xs = [(c + dx + j) % t for j in range(-6, 7)]
        patch = power[np.ix_(ys, xs)]
        peak = float(patch[5:8, 5:8].max())
        ring = np.ones(patch.shape, dtype=bool)
        ring[4:9, 4:9] = False
        background = float(np.median(patch[ring]))
        return peak / background if background > 0 else 0.0

    return min(ratio_at(0, f), ratio_at(f, 0))


def _measure_spectral(gray: np.ndarray, is_jpeg: bool, config: AIGenerationConfig) -> AIIndicator:
    name: IndicatorName = "Spectral Peaks"
    residual = _laplacian(gray)
    signed = _averaged_spectrum(residual, config)
    if signed is None:
        return _not_assessed(
            name, f"Image is smaller than one {config.spectral_tile}px analysis tile.", config
        )
    magnitude = _averaged_spectrum(np.abs(residual), config)
    assert magnitude is not None

    best_ratio, best_period, best_map = 0.0, None, ""
    for period in config.spectral_periods:
        candidates = [("fixed pattern", signed)]
        # JPEG's 8x8 blocks make the residual's magnitude periodic at every
        # period dividing 8 — only the signed spectrum is clean there.
        if not (is_jpeg and 8 % period == 0):
            candidates.append(("resampling", magnitude))
        for label, power in candidates:
            ratio = _peak_ratio(power, period)
            if ratio > best_ratio:
                best_ratio, best_period, best_map = ratio, period, label

    strength = _ramp(
        math.log(max(best_ratio, 1e-9)),
        math.log(config.spectral_ratio_floor),
        math.log(config.spectral_ratio_ceiling),
    )
    detail = (
        f"Strongest periodic peak: {best_ratio:.1f}x the surrounding spectrum at a "
        f"{best_period}px period ({best_map})."
    )
    return _measured(name, round(best_ratio, 2), detail, strength, config)


# --- Noise -------------------------------------------------------------------------

def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def _measure_noise(gray: np.ndarray, config: AIGenerationConfig) -> tuple[AIIndicator, AIIndicator]:
    level_name: IndicatorName = "Noise Level"
    dep_name: IndicatorName = "Noise Signal Dependence"
    b = config.noise_block
    if gray.shape[0] < b * 2 or gray.shape[1] < b * 2:
        reason = "Image too small to split into noise blocks."
        return _not_assessed(level_name, reason, config), _not_assessed(dep_name, reason, config)

    smooth = np.asarray(
        Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8)).filter(ImageFilter.MedianFilter(3)),
        dtype=float,
    )
    residual = gray - smooth

    means = _blocks(gray, b).mean(axis=1)
    # Texture as the second difference of the denoised image: zero on a flat
    # area AND on a smooth gradient (sky, shading, paper curl), large only at
    # edges and detail — which is what would leak into the noise residual.
    curvature = np.zeros_like(smooth)
    curvature[1:-1, 1:-1] = np.abs(_laplacian(smooth)) / 4
    texture = _blocks(curvature, b).mean(axis=1)
    sigmas = _blocks(residual, b).std(axis=1)
    # Clipped blocks have had their noise flattened by the sensor or encoder.
    usable = (texture <= config.noise_smooth_max_curvature) & (means > 16) & (means < 239)
    if int(usable.sum()) < config.min_noise_blocks:
        reason = (
            f"Only {int(usable.sum())} smooth, unclipped {b}px block(s); "
            f"{config.min_noise_blocks} are needed to measure noise apart from edges."
        )
        return _not_assessed(level_name, reason, config), _not_assessed(dep_name, reason, config)

    sigma = float(np.median(sigmas[usable]))
    level = _measured(
        level_name, round(sigma, 3),
        f"Median noise in {int(usable.sum())} smooth block(s): {sigma:.2f} grey levels.",
        _ramp(sigma, config.noise_sigma_high, config.noise_sigma_low), config,
    )

    spread = float(np.ptp(means[usable]))
    if spread < config.noise_min_brightness_range:
        dependence = _not_assessed(
            dep_name,
            f"Smooth blocks only span {spread:.0f} grey levels of brightness; "
            f"{config.noise_min_brightness_range:.0f} are needed.",
            config,
        )
    else:
        rho = _spearman(means[usable], sigmas[usable])
        dependence = _measured(
            dep_name, round(rho, 3),
            f"Noise vs brightness rank correlation across smooth blocks: {rho:+.2f}.",
            _ramp(rho, config.noise_rho_high, config.noise_rho_low), config,
        )
    return level, dependence


# --- Compression -----------------------------------------------------------------

# libjpeg/IJG reference tables (Annex K), natural order — what Pillow returns.
_IJG_LUMA = (
    16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55,
    14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29, 51, 87, 80, 62,
    18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
    49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99,
)


def _ijg_table(quality: int) -> np.ndarray:
    scale = 5000 / quality if quality < 50 else 200 - 2 * quality
    table = np.floor((np.array(_IJG_LUMA) * scale + 50) / 100)
    return np.clip(table, 1, 255)


def _estimate_quality(image: Image.Image) -> tuple[int, bool] | None:
    """(closest IJG quality, whether the luma table matches it exactly)."""
    tables = getattr(image, "quantization", None)
    if not tables or 0 not in tables:
        return None
    luma = np.array(tables[0], dtype=float)
    if luma.size != 64:
        return None
    errors = [(float(np.abs(_ijg_table(q) - luma).sum()), q) for q in range(1, 101)]
    error, quality = min(errors)
    return quality, error == 0


def _recompress(image: Image.Image, quality: int) -> np.ndarray:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return np.asarray(Image.open(buffer).convert(image.mode), dtype=float)


def _measure_double_compression(image: Image.Image, config: AIGenerationConfig) -> AIIndicator:
    """
    JPEG ghosts (Farid, 2009). Recompressing at quality q differs least from
    the image at any quality it has already been saved at, so a single save
    gives a curve that only falls as q rises toward the final quality, while
    an earlier save at a lower quality leaves a dip. Blind to an earlier save
    at a HIGHER quality than the last one — the coarser last save hides it.
    """
    name: IndicatorName = "Double Compression"
    estimate = _estimate_quality(image)
    if estimate is None:
        return _not_assessed(name, "No readable JPEG quantisation table.", config)
    final_quality = estimate[0]
    qualities = list(range(config.ghost_min_quality, final_quality - 3, config.ghost_step))
    if len(qualities) < 4:
        return _not_assessed(
            name, f"Final quality {final_quality} leaves too little range below it to look for an earlier save.",
            config,
        )

    gray = image.convert("L")
    gray = Image.fromarray(_centre_crop(np.asarray(gray), config.ghost_crop))
    original = np.asarray(gray, dtype=float)
    curve = [float(((original - _recompress(gray, q)) ** 2).mean()) for q in qualities]

    depth, dip_quality = 0.0, None
    for i in range(len(curve) - 1):
        later = max(curve[i + 1:])
        if later > 0 and (later - curve[i]) / later > depth:
            depth, dip_quality = (later - curve[i]) / later, qualities[i]

    strength = _ramp(depth, config.ghost_depth_floor, config.ghost_depth_ceiling)
    detail = (
        f"Recompression error dips {depth:.0%} at quality {dip_quality}, below the final quality "
        f"{final_quality} — the image was saved before at about quality {dip_quality}."
        if strength > 0
        else f"No dip below the final quality {final_quality}; consistent with a single save."
    )
    return _measured(name, round(depth, 3), detail, strength, config)


def _measure_ela(image: Image.Image, config: AIGenerationConfig) -> AIIndicator:
    name: IndicatorName = "Error Level Analysis"
    rgb = image.convert("RGB")
    original = np.asarray(rgb, dtype=float)
    error = np.abs(original - _recompress(rgb, config.ela_quality)).mean(axis=2)
    gray = original.mean(axis=2)

    b = config.ela_block
    # Flat blocks recompress perfectly whatever their history, so only
    # textured ones say anything.
    textured = _blocks(gray, b).std(axis=1) >= 4
    block_error = _blocks(error, b).mean(axis=1)[textured]
    if block_error.size < config.ela_min_blocks:
        return _not_assessed(
            name, f"Only {block_error.size} textured {b}px block(s); {config.ela_min_blocks} are needed.", config
        )
    # The floor keeps the ratio meaningful when the image was last saved at
    # about ela_quality: then nearly every block recompresses to ~0 error, and
    # a raw ratio of two near-zero numbers is noise.
    median = float(np.median(block_error))
    floor = config.ela_error_floor
    ratio = (float(np.percentile(block_error, 95)) + floor) / (median + floor)
    detail = (
        f"At quality {config.ela_quality}, the brightest 5% of textured blocks show "
        f"{ratio:.1f}x the median error level."
    )
    return _measured(
        name, round(ratio, 2), detail, _ramp(ratio, config.ela_ratio_floor, config.ela_ratio_ceiling), config
    )


# --- Provenance (ExifTool) ---------------------------------------------------------

# IPTC NewsCodes digital source types (also what C2PA actions record).
# Floors only for declarations of generation; the rest are recorded as context.
_SOURCE_TYPE_FLOORS = {
    "trainedalgorithmicmedia": 0.95,
    "compositewithtrainedalgorithmicmedia": 0.85,
    "algorithmicmedia": 0.6,
    "compositesynthetic": 0.6,
}
_CAMERA_SOURCE_TYPES = {"digitalcapture", "negativefilm", "positivefilm", "print"}

_GENERATOR_MARKERS = (
    "stable diffusion", "stablediffusion", "midjourney", "dall-e", "dall·e", "dalle",
    "firefly", "imagen", "novelai", "comfyui", "automatic1111", "invokeai",
    "leonardo.ai", "openai", "gpt-image", "ideogram", "bing image creator", "flux.1",
)
# Tag names worth searching for a generator's name. Make/Model are left out:
# a camera called something like a marker would otherwise be accused.
_GENERATOR_TAG_HINTS = (
    "software", "creatortool", "producer", "creator", "claimgenerator", "generator",
    "parameters", "prompt", "workflow", "comment", "description",
)
_GENERATOR_FLOOR = 0.9
_GENERATION_RECORD_FLOOR = 0.95


def _run_exiftool(blobs: list[tuple[str, bytes]], config: AIGenerationConfig) -> list[dict] | None:
    """Tags for every blob in one ExifTool call (-G1: group-qualified names). None if unavailable."""
    if not blobs:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, (suffix, data) in enumerate(blobs):
            path = Path(tmp) / f"{i}.{suffix}"
            path.write_bytes(data)
            paths.append(str(path))
        try:
            completed = subprocess.run(
                ["exiftool", "-json", "-G1", "-a", "-charset", "filename=utf8", "--", *paths],
                capture_output=True, text=True, timeout=config.exiftool_timeout_s,
            )
            parsed = json.loads(completed.stdout or "[]")
        except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logger.warning("ExifTool unavailable for AI provenance: %s", exc)
            return None
    by_path = {entry.get("SourceFile"): entry for entry in parsed}
    return [by_path.get(path, {}) for path in paths]


def _source_types(tags: dict) -> list[str]:
    found = []
    for key, value in tags.items():
        for text in (value if isinstance(value, list) else [value]):
            text = str(text)
            lowered = text.lower()
            if "digitalsourcetype/" in lowered:
                found.append(lowered.rsplit("/", 1)[-1].strip())
            elif "digitalsourcetype" in key.lower() and text.strip():
                found.append(lowered.strip())
    return found


def _provenance(tags: dict | None, config: AIGenerationConfig) -> AIIndicator:
    name: IndicatorName = "Provenance"
    if tags is None:
        return _not_assessed(name, "ExifTool is not available to read provenance.", config)

    evidence: list[str] = []
    floor = 0.0
    source_types = _source_types(tags)
    for source_type in source_types:
        if source_type in _SOURCE_TYPE_FLOORS:
            floor = max(floor, _SOURCE_TYPE_FLOORS[source_type])
            evidence.append(f"declares digital source type '{source_type}'")

    lowered_keys = {key: key.lower() for key in tags}
    c2pa = any(k.startswith("jumbf:") or "c2pa" in k for k in lowered_keys.values())
    claim_generators = [str(v) for k, v in tags.items() if "claimgenerator" in lowered_keys[k]]
    if c2pa:
        evidence.append(
            "C2PA manifest present" + (f" (claim generator: {', '.join(claim_generators)})" if claim_generators else "")
        )

    for key, value in tags.items():
        lowered_key = lowered_keys[key]
        if not any(hint in lowered_key.split(":")[-1] for hint in _GENERATOR_TAG_HINTS):
            continue
        lowered = str(value).lower()
        tag = lowered_key.split(":")[-1]
        marker = next((m for m in _GENERATOR_MARKERS if m in lowered), None)
        marker_floor = _GENERATOR_FLOOR
        # A generator's own record of the generation run is stronger than a
        # name in a software tag, which any editor could write.
        if tag == "parameters" and "steps:" in lowered and ("sampler:" in lowered or "cfg scale:" in lowered):
            marker, marker_floor = "Stable Diffusion generation parameters", _GENERATION_RECORD_FLOOR
        elif tag in ("prompt", "workflow") and "class_type" in lowered:
            marker, marker_floor = "ComfyUI workflow", _GENERATION_RECORD_FLOOR
        if marker:
            floor = max(floor, marker_floor)
            evidence.append(f"{key.split(':')[-1]} names '{marker}'")

    detail = "; ".join(evidence) if evidence else (
        "No AI provenance declared (absence is not evidence — metadata is easily stripped)."
    )
    return AIIndicator(
        indicator=name, assessed=True, value=round(floor, 3), detail=detail,
        strength=round(floor, 3), weight=0.0,
    )


# --- Capture Signature -------------------------------------------------------------

# Profiles every editor and exporter embeds; they say nothing about a device.
_GENERIC_PROFILES = ("srgb", "adobe rgb", "generic rgb", "gray gamma", "dot gain", "generic gray")
# Output sizes of common generators (either orientation).
_GENERATOR_SIZES = {
    frozenset(size)
    for size in (
        (512, 512), (768, 768), (1024, 1024), (1536, 1536), (2048, 2048),
        (1024, 1536), (1024, 1792), (832, 1216), (896, 1152), (768, 1344), (640, 1536),
    )
}


def _tag(tags: dict, *names: str) -> str | None:
    """First non-empty value whose tag name (ignoring the -G1 group) matches."""
    wanted = {n.lower() for n in names}
    for key, value in tags.items():
        if key.split(":")[-1].lower() in wanted and str(value).strip():
            return str(value).strip()
    return None


def _capture_signature(image: Image.Image, fmt: str, tags: dict | None, config: AIGenerationConfig) -> AIIndicator:
    """
    Camera (strength 0), generator-shaped (1) or unknown (0.5). Unknown is
    the honest middle: WhatsApp/Telegram strip and re-encode real photos into
    exactly what a stripped AI image looks like, so a missing signature is
    weak evidence. Never strong evidence either way — EXIF and profiles can
    be copied onto a fake, which is why provenance and the pixel indicators
    are scored separately.
    """
    name: IndicatorName = "Capture Signature"
    if tags is None:
        return _not_assessed(name, "ExifTool is not available to read the colour profile and EXIF.", config)

    camera: list[str] = []
    quality = _estimate_quality(image) if fmt in ("jpeg", "jpg", "mpo") else None
    if quality is not None:
        q, standard = quality
        if not standard and q >= config.capture_min_camera_quality:
            camera.append(f"custom JPEG tables at quality {q}")

    notes: list[str] = []
    profile = _tag(tags, "ProfileDescription")
    device_profile = profile is not None and not any(g in profile.lower() for g in _GENERIC_PROFILES)
    if device_profile and fmt == "png":
        # iPhone screenshots are P3-tagged PNGs: a device made the file, but
        # it captured a screen, not a scene — possibly an AI image on screen.
        notes.append(f"PNG with a device colour profile ({profile}) — a screenshot, not a capture")
    elif device_profile:
        camera.append(f"device colour profile ({profile})")

    make, model = _tag(tags, "Make"), _tag(tags, "Model", "LensModel")
    if make or model:
        camera.append(f"camera EXIF ({' '.join(v for v in (make, model) if v)})")
    elif _tag(tags, "ExposureTime") and _tag(tags, "ISO"):
        camera.append("camera exposure EXIF")
    if any(t in _CAMERA_SOURCE_TYPES for t in _source_types(tags)):
        camera.append("declares a camera capture source type")

    width, height = image.size
    generator = (
        f"{width}x{height} is a common AI generator output size"
        if frozenset({width, height}) in _GENERATOR_SIZES
        else None
    )

    if len(camera) >= config.capture_min_camera_signals:
        return _measured(name, "camera", "Camera capture: " + "; ".join(camera) + ".", 0.0, config)
    if generator:
        return _measured(name, "generator-shaped", generator + ".", 1.0, config)
    found = camera + notes
    detail = (
        "No camera signature" + (f" beyond {'; '.join(found)}" if found else "")
        + " — re-encoded (e.g. by a messaging app), stripped, or not a camera capture."
    )
    return _measured(name, "unknown", detail, 0.5, config)


# --- scoring ---------------------------------------------------------------------

def _score(indicators: list[AIIndicator]) -> tuple[float, float]:
    """
    (score, coverage). Pixel/compression contributions add up; a provenance
    floor above that becomes Provenance's own contribution, so the recorded
    contributions always sum to the score (AC3).
    """
    scored = [i for i in indicators if i.indicator in _SCORED and i.assessed]
    score = min(1.0, sum(i.contribution for i in scored))
    coverage = sum(i.weight for i in scored)
    for indicator in indicators:
        if indicator.indicator == "Provenance" and indicator.assessed and indicator.strength > score:
            indicator.contribution = round(indicator.strength - score, 4)
            score = indicator.strength
    return round(score, 4), round(coverage, 4)


def _log_indicators(document_id: str, label: str, indicators: list[AIIndicator]) -> None:
    for i in indicators:
        logger.info(
            "ai_generation document=%s image=%s indicator=%r assessed=%s value=%s "
            "strength=%.3f weight=%.2f contribution=%.4f%s",
            document_id, label, i.indicator, i.assessed, i.value, i.strength, i.weight,
            i.contribution, f" reason={i.reason!r}" if i.reason else "",
        )


# --- per image -------------------------------------------------------------------

def _assess_image(
    document_id: str,
    data: bytes,
    tags: dict | None,
    config: AIGenerationConfig,
    *,
    source: Literal["upload", "embedded"],
    page: int | None = None,
    xref: int | None = None,
    bbox: BBox | None = None,
) -> AIImageAssessment | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 — an undecodable image is skipped, not fatal
        logger.debug("document %s: could not decode image (xref %s): %s", document_id, xref, exc)
        return None

    fmt = (image.format or _sniff(data) or "unknown").lower()
    is_jpeg = fmt in ("jpeg", "jpg", "mpo")
    width, height = image.size
    label = f"page{page + 1}/xref{xref}" if source == "embedded" and page is not None else "upload"

    rgb = _centre_crop(np.asarray(image.convert("RGB")), config.max_analysis_side)
    photographic, character = _is_photographic(rgb, config)
    indicators: list[AIIndicator] = []
    if not photographic:
        reason = f"Graphic rather than a photograph or scan ({character}); pixel statistics don't apply."
        indicators += [_not_assessed(name, reason, config) for name in _SCORED]
    else:
        if source == "upload":
            capture = _capture_signature(image, fmt, tags, config)
        else:
            capture = _not_assessed(
                "Capture Signature", "PDF creation normally strips a camera's signature from embedded images.", config
            )
        gray = rgb.astype(float) @ np.array([0.299, 0.587, 0.114])
        indicators.append(_measure_spectral(gray, is_jpeg, config))
        level, dependence = _measure_noise(gray, config)
        if capture.value == "camera" and level.assessed:
            # Phone noise reduction, not generation, explains a clean camera photo.
            factor = config.noise_weight_when_captured
            level = level.model_copy(update={
                "weight": level.weight * factor,
                "contribution": round(level.contribution * factor, 4),
                "detail": f"{level.detail} Weighted x{factor:g}: a camera capture, where phone "
                          "noise reduction explains a clean image.",
            })
        indicators += [level, dependence, _measure_ela(Image.fromarray(rgb), config)]
        if is_jpeg:
            indicators.append(_measure_double_compression(image, config))
        else:
            indicators.append(_not_assessed(
                "Double Compression", f"{fmt.upper()} carries no JPEG compression history.", config
            ))
        indicators.append(capture)
    indicators.append(_provenance(tags, config))

    score, coverage = _score(indicators)
    _log_indicators(document_id, label, indicators)
    logger.info(
        "ai_generation document=%s image=%s score=%.3f coverage=%.2f photographic=%s",
        document_id, label, score, coverage, photographic,
    )
    return AIImageAssessment(
        source=source, page=page, xref=xref, bbox=bbox,
        pixel_width=width, pixel_height=height, format=fmt,
        photographic=photographic, score=score, likelihood=_likelihood(score, config),
        coverage=coverage, indicators=indicators,
    )


def _embedded_images(doc: pymupdf.Document, config: AIGenerationConfig) -> list[dict]:
    """Distinct raster images above the size floor, largest first, with where they're first drawn."""
    seen: dict[int, dict] = {}
    for page_index in range(doc.page_count):
        page = doc[page_index]
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in seen:
                continue
            try:
                extracted = doc.extract_image(xref)
            except Exception:  # noqa: BLE001 — skip a stream PyMuPDF can't extract
                continue
            if not extracted or min(extracted.get("width", 0), extracted.get("height", 0)) < config.min_image_side:
                continue
            rects = page.get_image_rects(xref)
            seen[xref] = {
                "xref": xref,
                "page": page_index,
                "bbox": tuple(rects[0]) if rects else None,
                "data": extracted["image"],
                "ext": extracted.get("ext", "png"),
                "area": extracted["width"] * extracted["height"],
            }
    return sorted(seen.values(), key=lambda e: e["area"], reverse=True)[: config.max_images]


def _unable(
    document_id: str,
    reason: str,
    config: AIGenerationConfig,
    *,
    images: list[AIImageAssessment] | None = None,
    file_provenance: AIIndicator | None = None,
    bbox_units: Literal["pt", "px"] = "pt",
) -> AIGenerationResult:
    return AIGenerationResult(
        document_id=document_id, score=0.0, likelihood="low", images=images or [],
        file_provenance=file_provenance, config=config, unable_to_assess=True,
        unable_to_assess_reason=reason, bbox_units=bbox_units,
    )


def check_ai_generation(
    document_id: str,
    file_bytes: bytes,
    config: AIGenerationConfig | None = None,
) -> AIGenerationResult:
    """
    Scores a standalone image upload, or the raster images embedded in a PDF,
    for traces of AI generation. No LLM tokens.
    Synchronous/CPU-bound — callers should run this via asyncio.to_thread.
    """
    config = config or AIGenerationConfig()
    fmt = _sniff(file_bytes)

    if fmt != "pdf":
        tags = _run_exiftool([(fmt or "bin", file_bytes)], config)
        assessment = _assess_image(
            document_id, file_bytes, tags[0] if tags else None, config, source="upload",
        )
        if assessment is None:
            return _unable(document_id, "The file could not be decoded as an image.", config, bbox_units="px")
        return _finish(document_id, [assessment], None, config, bbox_units="px")

    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        reason = f"Failed to open document as a PDF: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason)
        return _unable(document_id, reason, config)

    try:
        embedded = _embedded_images(doc, config)
    finally:
        doc.close()

    tags = _run_exiftool([("pdf", file_bytes)] + [(e["ext"], e["data"]) for e in embedded], config)
    file_provenance = _provenance(tags[0] if tags else None, config)
    _log_indicators(document_id, "file", [file_provenance])

    assessments = []
    for i, entry in enumerate(embedded):
        assessment = _assess_image(
            document_id, entry["data"], tags[i + 1] if tags else None, config,
            source="embedded", page=entry["page"], xref=entry["xref"], bbox=entry["bbox"],
        )
        if assessment is not None:
            assessments.append(assessment)
    return _finish(document_id, assessments, file_provenance, config)


def _finish(
    document_id: str,
    images: list[AIImageAssessment],
    file_provenance: AIIndicator | None,
    config: AIGenerationConfig,
    *,
    bbox_units: Literal["pt", "px"] = "pt",
) -> AIGenerationResult:
    file_floor = file_provenance.strength if file_provenance and file_provenance.assessed else 0.0
    measured = [image for image in images if image.coverage > 0 or image.score > 0]

    # Unable to Assess is never reported as a clean result: with no image
    # measured and nothing declared, there is no basis for a score at all.
    if not measured and file_floor == 0:
        if not images:
            reason = (
                f"No raster images of at least {config.min_image_side}px to analyse; pixel-level "
                "AI-generation analysis needs a photo, scan or image upload."
            )
        else:
            reason = (
                f"{len(images)} image(s) found, but all are graphics (logos, flat fills, rendered "
                "pages) rather than photographs or scans, and none declares an AI source."
            )
        return _unable(
            document_id, reason, config, images=images, file_provenance=file_provenance, bbox_units=bbox_units,
        )

    score = max([file_floor] + [image.score for image in images])
    logger.info("ai_generation document=%s score=%.3f images=%d", document_id, score, len(images))
    return AIGenerationResult(
        document_id=document_id, score=round(score, 4), likelihood=_likelihood(score, config),
        images=images, file_provenance=file_provenance, config=config,
        unable_to_assess=False, bbox_units=bbox_units,
    )
