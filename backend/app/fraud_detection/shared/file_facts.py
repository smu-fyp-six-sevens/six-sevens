"""
What a file holds, in a few facts the lead and the specialists read before choosing checks: its kind, its
pages, whether it has selectable text, and the size of each embedded image. Nothing here decides which
check runs; it only tells the agent what the checks would have to work with.
"""

from __future__ import annotations

import io
from typing import Any, cast

import pymupdf
from PIL import Image

from .pdf_pages import pages

_MAX_IMAGES_LISTED = 10
_TEXT_PROBE_CHARS = 20


def describe_file(file_bytes: bytes) -> dict[str, Any] | None:
    """The file's facts, or None when it can't be read as a PDF or an image."""
    try:
        if file_bytes[:5] == b"%PDF-":
            return _describe_pdf(file_bytes)
        with Image.open(io.BytesIO(file_bytes)) as image:
            return {"kind": "image", "format": image.format, "width": image.width, "height": image.height}
    except Exception:
        return None


def _describe_pdf(file_bytes: bytes) -> dict[str, Any]:
    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    try:
        sizes: dict[int, tuple[int, int]] = {}
        placements = 0
        text_chars = 0
        for page in pages(doc):
            text_chars += len(cast(str, page.get_text("text")).strip())
            for image in page.get_images(full=True):
                placements += 1
                sizes.setdefault(image[0], (int(image[2]), int(image[3])))
        listed = [{"width": w, "height": h} for w, h in list(sizes.values())[:_MAX_IMAGES_LISTED]]
        return {
            "kind": "pdf",
            "pages": doc.page_count,
            "selectable_text": text_chars >= _TEXT_PROBE_CHARS,
            "image_placements": placements,
            "distinct_images": len(sizes),
            "images": listed,
        }
    finally:
        doc.close()


def render_file_facts(facts: dict[str, Any] | None) -> str:
    """One or two plain sentences for a prompt."""
    if not facts:
        return "not available"
    if facts.get("kind") == "image":
        return f"an image file ({facts.get('format') or 'unknown format'}, {facts['width']}x{facts['height']} px) with no selectable text."
    text = "has selectable text" if facts.get("selectable_text") else "has no selectable text"
    head = f"a PDF of {facts['pages']} page(s) that {text}."
    distinct = facts["distinct_images"]
    if not distinct:
        return f"{head} It has no embedded images."
    sizes = ", ".join(f"{i['width']}x{i['height']}" for i in facts["images"])
    more = "" if distinct <= len(facts["images"]) else f" (and {distinct - len(facts['images'])} more)"
    return (
        f"{head} It has {distinct} distinct embedded image(s) used {facts['image_placements']} time(s), "
        f"of {sizes} px{more}."
    )
