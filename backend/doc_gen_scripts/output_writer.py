"""
Shared helper for emitting a generated document as PDF, PNG, JPG, or JPEG,
based on the extension of the requested output path.

Used by generate_from_mastersheet.py's end-of-run normalization pass: every
individual document generator (COI, NRIC, payslip, bank statement, etc.)
still only knows how to write a PDF. This module is what turns that PDF into
whatever the mastersheet's Filename column actually asked for.

Requires: pip install pymupdf
"""
from __future__ import annotations

import io
from pathlib import Path

SUPPORTED_IMAGE_EXTS = {"png", "jpg", "jpeg"}
DEFAULT_DPI = 200  # ~200dpi reads as a clean phone-scan/photo; bump for print quality


def write_document(pdf_bytes: bytes, output_path: str | Path, dpi: int = DEFAULT_DPI) -> Path:
    """
    Write `pdf_bytes` to `output_path`.

    - output_path ending in .pdf  -> bytes written as-is.
    - output_path ending in .png/.jpg/.jpeg -> page 1 of the PDF is
      rasterized to that image format at `dpi`.

    Returns the Path written, so callers can log/verify it.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower().lstrip(".")

    if ext == "pdf":
        out.write_bytes(pdf_bytes)
        return out

    if ext not in SUPPORTED_IMAGE_EXTS:
        raise ValueError(
            f"Unsupported output extension '.{ext}' for {out.name}; "
            f"expected one of: pdf, png, jpg, jpeg"
        )

    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            "Rendering to PNG/JPG/JPEG requires PyMuPDF.\n    pip install pymupdf"
        ) from e

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[0]  # every letter/certificate here is a single page
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        # alpha=False: JPEG has no alpha channel, and it also gives PNGs a
        # plain white background instead of a checkerboard/transparent one,
        # which is what a real scan or camera photo would look like.
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        if ext == "png":
            img_bytes = pix.tobytes("png")
        else:  # jpg / jpeg
            img_bytes = pix.tobytes("jpeg", jpg_quality=88)
        out.write_bytes(img_bytes)
    finally:
        doc.close()

    return out


def normalize_extension(path: Path, dpi: int = DEFAULT_DPI) -> Path:
    """
    Fix a file that a generator wrote as raw PDF bytes under a non-.pdf name
    (e.g. "ChiaWM_Passport_scan.jpg" that's actually a PDF inside), by
    rasterizing it in place to match its own extension.

    No-ops for: .pdf files, and files that aren't actually PDF-in-disguise
    (so a real .jpg produced by some other path is left untouched).
    """
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf" or ext not in SUPPORTED_IMAGE_EXTS:
        return path

    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return path

    if not data.startswith(b"%PDF"):
        return path  # already a real image; nothing to do

    write_document(data, path, dpi=dpi)  # overwrites path in place
    return path
