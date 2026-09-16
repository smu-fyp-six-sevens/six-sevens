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

from pathlib import Path

import pymupdf

SUPPORTED_IMAGE_EXTS = {"png", "jpg", "jpeg"}
DEFAULT_DPI = 200  # ~200dpi reads as a clean phone-scan/photo; bump for print quality


def write_document(
    pdf_bytes: bytes, output_path: str | Path, dpi: int = DEFAULT_DPI
) -> list[Path]:
    """
    Write `pdf_bytes` to `output_path`.

    - output_path ending in .pdf -> bytes written as-is, one file.
    - output_path ending in .png/.jpg/.jpeg -> every page of the PDF is
      rasterized to that image format at `dpi`. A single-page source keeps
      the exact requested filename; a multi-page source (e.g. a 4-page
      bank statement) instead writes one file per page, named
      "<stem>_page<N><ext>" (1-indexed) next to it, since a single image
      file can't hold more than one page.

    Returns every Path written, so callers can log/verify them.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower().lstrip(".")

    if ext == "pdf":
        out.write_bytes(pdf_bytes)
        return [out]

    if ext not in SUPPORTED_IMAGE_EXTS:
        raise ValueError(
            f"Unsupported output extension '.{ext}' for {out.name}; "
            f"expected one of: pdf, png, jpg, jpeg"
        )

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        multi_page = doc.page_count > 1
        written: list[Path] = []
        for page_number in range(doc.page_count):
            page = doc[page_number]
            # alpha=False: JPEG has no alpha channel, and it also gives PNGs a
            # plain white background instead of a checkerboard/transparent one,
            # which is what a real scan or camera photo would look like.
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img_bytes = (
                pix.tobytes("png") if ext == "png" else pix.tobytes("jpeg", jpg_quality=88)
            )
            page_path = (
                out.with_name(f"{out.stem}_page{page_number + 1}{out.suffix}") if multi_page else out
            )
            page_path.write_bytes(img_bytes)
            written.append(page_path)
    finally:
        doc.close()

    return written


RLBBox = tuple[float, float, float, float]  # reportlab convention: (x0, y0, x1, y1), origin bottom-left


def write_cropped_region(
    pdf_bytes: bytes,
    region: RLBBox,
    output_path: str | Path,
    dpi: int = DEFAULT_DPI,
    page_index: int = 0,
) -> Path:
    """
    Rasterizes just one rectangular region of one page to a standalone
    image — for a source page that deliberately packs more than one
    logical artifact onto it (e.g. generate_nric_card's front+back
    print-and-cut A4 sheet), rather than a page-per-file split.

    `region` is in reportlab's coordinate convention (origin bottom-left,
    y increases upward) — the same convention the generator that produced
    the PDF used to place things on the page — and gets converted here to
    PyMuPDF's top-left-origin convention internally.

    output_path's extension must be one of SUPPORTED_IMAGE_EXTS — there's
    no PDF case here, since cropping a region out to a standalone PDF page
    isn't something this module currently needs.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower().lstrip(".")
    if ext not in SUPPORTED_IMAGE_EXTS:
        raise ValueError(
            f"Unsupported output extension '.{ext}' for {out.name}; "
            f"expected one of: {', '.join(sorted(SUPPORTED_IMAGE_EXTS))}"
        )

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        page = doc[page_index]
        page_h = page.rect.height
        x0, y0_rl, x1, y1_rl = region
        clip = pymupdf.Rect(x0, page_h - y1_rl, x1, page_h - y0_rl)

        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        # alpha=False: see write_document — same reasoning (no transparency,
        # plain white background matching a real scan/photo).
        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        img_bytes = pix.tobytes("png") if ext == "png" else pix.tobytes("jpeg", jpg_quality=88)
        out.write_bytes(img_bytes)
    finally:
        doc.close()

    return out


def normalize_extension(path: Path, dpi: int = DEFAULT_DPI) -> list[Path]:
    """
    Fix a file that a generator wrote as raw PDF bytes under a non-.pdf name
    (e.g. "ChiaWM_Passport_scan.jpg" that's actually a PDF inside), by
    rasterizing it in place to match its own extension. A multi-page source
    is split into one numbered file per page (see write_document); the
    original single-name placeholder is then removed, since it would
    otherwise sit alongside the numbered files as a stale duplicate holding
    just page 1's raw PDF bytes under an image extension.

    No-ops for: .pdf files, and files that aren't actually PDF-in-disguise
    (so a real .jpg produced by some other path is left untouched) — both
    return the original path unchanged, wrapped in a list.
    """
    path = Path(path)
    ext = path.suffix.lower().lstrip(".")
    if ext == "pdf" or ext not in SUPPORTED_IMAGE_EXTS:
        return [path]

    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return [path]

    if not data.startswith(b"%PDF"):
        return [path]  # already a real image; nothing to do

    written = write_document(data, path, dpi=dpi)
    if written != [path]:
        # Multi-page split superseded the original single-name placeholder.
        path.unlink(missing_ok=True)
    return written
