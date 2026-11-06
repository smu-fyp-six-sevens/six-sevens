"""
Photo-like images for the AI-generation checks (fraud_detection/authenticity/ai_generation.py): a natural scene, and
the same scene with the periodic pattern a generator's upsampling layers leave behind.
"""
from __future__ import annotations

import functools
import io

import numpy as np
from PIL import Image, PngImagePlugin


@functools.lru_cache(maxsize=None)
def scene(n: int = 768, noise: float = 3.0, seed: int = 0, falloff: float = 1.6) -> np.ndarray:
    """
    A photo-like RGB image: 1/f^falloff texture (no resampling of its own)
    plus sensor-like noise. A higher falloff means less fine texture.
    Built once per argument set (about 0.3 s each) and shared, so the array is read-only.
    """
    rng = np.random.default_rng(seed)
    fy, fx = np.meshgrid(np.fft.fftfreq(n), np.fft.fftfreq(n), indexing="ij")
    base = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(n, n))) / (np.hypot(fy, fx) + 1 / n) ** falloff))
    gray = (base - base.min()) / np.ptp(base) * 200 + 20
    rgb = np.stack([gray, gray * 0.9 + 10, gray * 0.8 + 20], axis=-1)
    scene = np.clip(rgb + rng.normal(0, noise, rgb.shape), 0, 255)
    scene.setflags(write=False)
    return scene


def with_checkerboard(rgb: np.ndarray, period: int = 2, amplitude: float = 1.0, seed: int = 1) -> np.ndarray:
    """The fixed periodic pattern a generator's upsampling layers leave behind."""
    rng = np.random.default_rng(seed)
    cell = rng.normal(0, 1, (period, period))
    cell -= cell.mean()
    yy, xx = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]]
    return np.clip(rgb + amplitude * cell[yy % period, xx % period][..., None], 0, 255)


def png(rgb: np.ndarray, info: PngImagePlugin.PngInfo | None = None) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(rgb.astype(np.uint8)).save(buffer, "PNG", pnginfo=info)
    return buffer.getvalue()
