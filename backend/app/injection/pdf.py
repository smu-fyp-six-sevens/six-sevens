from __future__ import annotations

import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from .schemas import FieldValue

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
TEMPLATES_DIR = DATA_DIR / "templates"
WORKING_DIR = DATA_DIR / "working"

BUILTIN_FONT_HELVETICA = "helv"
BUILTIN_FONT_TIMES = "tiro"
_BUILTIN_FONTS = {BUILTIN_FONT_HELVETICA, BUILTIN_FONT_TIMES, "cour", "symb", "zadb"}

# Logical font names usable by injectors (map to system TTF/OTF files).
_CUSTOM_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "arial": (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Microsoft/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ),
}

_EMPHASIS_FONT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "bold": (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Microsoft/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ),
    "italic": (
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        "/Library/Fonts/Arial Italic.ttf",
        "/Library/Fonts/Microsoft/Arial Italic.ttf",
        "C:/Windows/Fonts/ariali.ttf",
        "/usr/share/fonts/truetype/msttcorefonts/Arial_Italic.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    ),
}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_ocr_engine = None
_ocr_cache: dict[tuple[str, float, int], dict[str, FieldValue]] = {}


def ensure_data_dirs() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    WORKING_DIR.mkdir(parents=True, exist_ok=True)


def template_pdf_path(document_id: str) -> Path:
    return TEMPLATES_DIR / f"{document_id}.pdf"


def working_file_path(filename: str) -> Path:
    """Path to the working copy; filename keeps the original extension."""
    return WORKING_DIR / filename


def working_pdf_path(document_id: str) -> Path:
    """Backwards-compatible helper for PDF-only callers."""
    return working_file_path(f"{document_id}.pdf")


def media_type_for(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def is_image_template(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def find_template_path(document_id: str) -> Path | None:
    """Resolve a template by id for PDF or common image extensions."""
    stem = document_id
    for suffix in (".pdf",) + tuple(IMAGE_SUFFIXES):
        candidate = TEMPLATES_DIR / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _save_pdf(doc: pymupdf.Document, pdf_path: Path) -> None:
    temp_path = pdf_path.with_suffix(".tmp.pdf")
    doc.save(temp_path, garbage=4, deflate=True)
    doc.close()
    temp_path.replace(pdf_path)


def _default_image_metadata(file_path: Path) -> dict[str, str]:
    return {
        "title": file_path.stem,
        "author": "Greenlink Digital Bank",
        "subject": "image-template",
        "creator": "six-sevens fraud injection",
    }


# EXIF tags used for JPEG metadata (TIFF/EXIF standard).
_EXIF_IMAGE_DESCRIPTION = 270
_EXIF_SOFTWARE = 305
_EXIF_ARTIST = 315


def _read_image_metadata(file_path: Path) -> dict[str, str]:
    """Read metadata embedded in the image (PNG tEXt / JPEG EXIF)."""
    metadata = _default_image_metadata(file_path)
    with Image.open(file_path) as image:
        info = image.info or {}
        # PNG text chunks (and some JPEG info keys)
        for src, dest in (
            ("Author", "author"),
            ("Title", "title"),
            ("Description", "subject"),
            ("Software", "creator"),
        ):
            value = info.get(src)
            if isinstance(value, str) and value.strip():
                metadata[dest] = value

        try:
            exif = image.getexif()
        except Exception:
            exif = None
        if exif:
            artist = exif.get(_EXIF_ARTIST)
            description = exif.get(_EXIF_IMAGE_DESCRIPTION)
            software = exif.get(_EXIF_SOFTWARE)
            if artist:
                metadata["author"] = str(artist)
            if description:
                metadata["title"] = str(description)
            if software:
                metadata["creator"] = str(software)
    return metadata


def _set_image_metadata(file_path: Path, metadata: dict[str, str]) -> None:
    """Write metadata into the image file itself (no sidecar)."""
    suffix = file_path.suffix.lower()
    with Image.open(file_path) as image:
        image.load()
        working = image.copy()

    if suffix == ".png":
        pnginfo = PngImagePlugin.PngInfo()
        if metadata.get("author"):
            pnginfo.add_text("Author", metadata["author"])
        if metadata.get("title"):
            pnginfo.add_text("Title", metadata["title"])
        if metadata.get("subject"):
            pnginfo.add_text("Description", metadata["subject"])
        if metadata.get("creator"):
            pnginfo.add_text("Software", metadata["creator"])
        working.save(file_path, pnginfo=pnginfo)
        return

    if suffix in {".jpg", ".jpeg"}:
        if working.mode != "RGB":
            working = working.convert("RGB")
        exif = working.getexif()
        if metadata.get("author"):
            exif[_EXIF_ARTIST] = metadata["author"]
        if metadata.get("title"):
            exif[_EXIF_IMAGE_DESCRIPTION] = metadata["title"]
        if metadata.get("creator"):
            exif[_EXIF_SOFTWARE] = metadata["creator"]
        working.save(file_path, quality=95, exif=exif)
        return

    # Other image types: leave pixels unchanged; metadata stays in memory only.
    working.save(file_path)


def read_document_metadata(file_path: Path) -> dict[str, str]:
    if file_path.suffix.lower() == ".pdf":
        doc = pymupdf.open(file_path)
        metadata = {key: value for key, value in (doc.metadata or {}).items() if value}
        doc.close()
        return metadata
    return _read_image_metadata(file_path)


def set_document_metadata(file_path: Path, metadata: dict[str, str]) -> None:
    """Persist metadata inside the working file (PDF info / image EXIF or PNG text)."""
    if file_path.suffix.lower() == ".pdf":
        doc = pymupdf.open(file_path)
        doc.set_metadata(metadata)
        _save_pdf(doc, file_path)
        return
    _set_image_metadata(file_path, metadata)


# Backwards-compatible aliases
def read_pdf_metadata(pdf_path: Path) -> dict[str, str]:
    return read_document_metadata(pdf_path)


def set_pdf_metadata(pdf_path: Path, metadata: dict[str, str]) -> None:
    set_document_metadata(pdf_path, metadata)


def resolve_font_file(font_name: str | None) -> str | None:
    """Return a filesystem path for a custom font name, if available."""
    if not font_name:
        return None
    key = font_name.strip().lower()
    for candidate in _CUSTOM_FONT_CANDIDATES.get(key, ()):
        if Path(candidate).exists():
            return candidate
    return None


def resolve_emphasis_font_file(style: str) -> str | None:
    """Return a bold/italic TTF path for emphasis redraws."""
    key = style.strip().lower()
    for candidate in _EMPHASIS_FONT_CANDIDATES.get(key, ()):
        if Path(candidate).exists():
            return candidate
    return None


def available_custom_fonts() -> list[str]:
    """Logical custom font names that resolve to a real file on this machine."""
    return [name for name in _CUSTOM_FONT_CANDIDATES if resolve_font_file(name)]


def _pil_font(
    font_name: str | None,
    size: float,
    *,
    bold: bool = True,
    font_path: str | None = None,
) -> ImageFont.ImageFont:
    """Load a system TTF close to the requested PDF built-in / custom font."""
    size_i = max(8, int(round(size)))
    if font_path and Path(font_path).exists():
        try:
            return ImageFont.truetype(font_path, size_i)
        except OSError:
            pass

    name = (font_name or BUILTIN_FONT_HELVETICA).lower()
    custom = resolve_font_file(name)
    if custom:
        try:
            return ImageFont.truetype(custom, size_i)
        except OSError:
            pass

    if name in {BUILTIN_FONT_TIMES, "tiro", "times"}:
        candidates = [
            "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf" if bold else None,
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/Library/Fonts/Times New Roman Bold.ttf" if bold else None,
            "/Library/Fonts/Times New Roman.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf" if bold else None,
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf" if bold else None,
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        ]
    elif name in {"cour", "courier"}:
        candidates = [
            "/System/Library/Fonts/Supplemental/Courier New Bold.ttf" if bold else None,
            "/System/Library/Fonts/Supplemental/Courier New.ttf",
            "/Library/Fonts/Courier New.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else None,
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
    else:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else None,
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf" if bold else None,
            "/Library/Fonts/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttc",
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else None,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else None,
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size_i)
            except OSError:
                continue
    return ImageFont.load_default()


def _system_font_paths() -> list[str]:
    """Candidate typefaces to match against an OCR crop (regular + bold)."""
    paths = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Narrow.ttf",
        "/System/Library/Fonts/Supplemental/Arial Narrow Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Neue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    seen: set[str] = set()
    existing: list[str] = []
    for path in paths:
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        existing.append(path)
    return existing


def _text_width(pil_font: ImageFont.ImageFont, text: str) -> float:
    try:
        return float(pil_font.getlength(text))
    except AttributeError:
        box = pil_font.getbbox(text)
        return float(box[2] - box[0]) if box else float(len(text) * 8)


def _to_gray_bytes(image: Image.Image) -> list[int]:
    return list(image.convert("L").getdata())


def _mean_abs_diff(left: list[int], right: list[int]) -> float:
    if not left or not right or len(left) != len(right):
        return 1e9
    total = sum(abs(a - b) for a, b in zip(left, right))
    return total / len(left)


def _render_text_on_bg(
    text: str,
    *,
    font: ImageFont.ImageFont,
    width: int,
    height: int,
    bg: tuple[int, int, int],
    ink: tuple[int, int, int],
) -> Image.Image:
    canvas = Image.new("RGB", (max(1, width), max(1, height)), bg)
    draw = ImageDraw.Draw(canvas)
    # Vertically center on baseline-ish middle of the crop.
    try:
        draw.text((0, height - 1), text, fill=ink, font=font, anchor="ls")
    except TypeError:
        draw.text((0, max(0, height // 8)), text, fill=ink, font=font)
    return canvas


def _match_font_to_crop(
    image: Image.Image,
    rect: list[float],
    sample_text: str,
    *,
    ink: tuple[int, int, int],
    bg: tuple[int, int, int],
    max_size: float,
) -> ImageFont.ImageFont:
    """
    Pick the system font/size whose rendering of sample_text best matches the crop.

    OCR cannot read the real typeface; this approximates it from pixels.
    """
    if not sample_text.strip() or len(rect) != 4:
        return _pil_font(BUILTIN_FONT_HELVETICA, max_size, bold=False)

    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, max(x0 + 1, x1)), min(image.height, max(y0 + 1, y1))
    width, height = x1 - x0, y1 - y0
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    target = _to_gray_bytes(crop)

    best_font = _pil_font(BUILTIN_FONT_HELVETICA, max_size, bold=False)
    best_score = 1e9
    min_size = max(7.0, height * 0.45)
    size_cap = max(min_size, min(max_size, height * 1.15))

    for path in _system_font_paths():
        size = size_cap
        while size >= min_size:
            try:
                font = ImageFont.truetype(path, max(8, int(round(size))))
            except OSError:
                break
            # Prefer sizes that roughly fill the crop width.
            text_w = _text_width(font, sample_text)
            if text_w > width * 1.15:
                size -= 0.75
                continue
            if text_w < width * 0.55 and size > min_size + 1:
                size -= 0.75
                continue
            rendered = _render_text_on_bg(
                sample_text,
                font=font,
                width=width,
                height=height,
                bg=bg if len(bg) == 3 else (255, 255, 255),
                ink=ink,
            )
            score = _mean_abs_diff(target, _to_gray_bytes(rendered))
            # Small bonus when width is close to the OCR box.
            width_penalty = abs(text_w - width) / max(1.0, width) * 12.0
            score += width_penalty
            if score < best_score:
                best_score = score
                best_font = font
            size -= 0.75
    return best_font


def _fit_font_path_to_width(
    text: str,
    *,
    template_font: ImageFont.ImageFont,
    max_width: float,
    max_size: float,
    min_size: float = 7.0,
) -> ImageFont.ImageFont:
    """Reuse the matched typeface file, only adjusting size to fit."""
    path = getattr(template_font, "path", None)
    size = max(min_size, max_size)
    while size > min_size:
        if path:
            try:
                font = ImageFont.truetype(path, max(8, int(round(size))))
            except OSError:
                font = _pil_font(BUILTIN_FONT_HELVETICA, size, bold=False)
        else:
            font = _pil_font(BUILTIN_FONT_HELVETICA, size, bold=False)
        if _text_width(font, text) <= max_width or max_width <= 0:
            return font
        size -= 0.5
    if path:
        try:
            return ImageFont.truetype(path, max(8, int(round(min_size))))
        except OSError:
            pass
    return _pil_font(BUILTIN_FONT_HELVETICA, min_size, bold=False)


def _sample_ink_color(image: Image.Image, rect: list[float]) -> tuple[int, int, int]:
    """Pick a dark ink color from the OCR box (fallback black)."""
    if len(rect) != 4:
        return (0, 0, 0)
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, max(x0 + 1, x1)), min(image.height, max(y0 + 1, y1))
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    pixels = list(crop.getdata())
    dark = [px for px in pixels if (px[0] + px[1] + px[2]) < 500]
    if not dark:
        return (0, 0, 0)
    dark.sort(key=lambda px: px[0] + px[1] + px[2])
    return dark[len(dark) // 4]


def _sample_bg_color(image: Image.Image, rect: list[float]) -> tuple[int, int, int]:
    x0 = max(0, int(rect[0]) - 2) if len(rect) == 4 else 0
    y0 = max(0, int(rect[1]) - 2) if len(rect) == 4 else 0
    bg = image.getpixel((x0, y0))
    if isinstance(bg, int):
        return (bg, bg, bg)
    return (int(bg[0]), int(bg[1]), int(bg[2]))


def _shared_prefix_len(left: str, right: str) -> int:
    limit = min(len(left), len(right))
    index = 0
    while index < limit and left[index] == right[index]:
        index += 1
    best = 0
    for sep in (":", ". ", " No. ", "NO.", "."):
        pos = left.rfind(sep, 0, index)
        if pos >= 0:
            end = pos + len(sep)
            if end >= 3 and end > best:
                best = end
    if best:
        return best
    return index if index >= 3 else 0


def _changed_middle(old: str, new: str) -> tuple[int, int, int, int] | None:
    """
    Return (old_start, old_end, new_start, new_end) for the rewritten span.

    Keeps identical leading labels and trailing clutter as original pixels.
    """
    prefix = _shared_prefix_len(old, new)
    if prefix <= 0 and (not old or not new or old[:1] != new[:1]):
        # Still allow full-string value fields (names) — caller treats None as full replace.
        return None

    # If strings share no delimiter-based prefix but share a long head, use it.
    if prefix <= 0:
        index = 0
        limit = min(len(old), len(new))
        while index < limit and old[index] == new[index]:
            index += 1
        prefix = index if index >= 3 else 0
        if prefix <= 0:
            return None

    old_i, new_i = len(old), len(new)
    while old_i > prefix and new_i > prefix and old[old_i - 1] == new[new_i - 1]:
        old_i -= 1
        new_i -= 1

    if old_i <= prefix and new_i <= prefix:
        return None
    return prefix, old_i, prefix, new_i


def _value_subrect(
    field: FieldValue,
    old_text: str,
    old_start: int,
    old_end: int,
    probe_font: ImageFont.ImageFont,
) -> list[float]:
    """Map a character span onto the OCR pixel box using font metrics."""
    x0, y0, x1, y1 = field.rect
    box_w = max(1.0, x1 - x0)
    full_w = max(1.0, _text_width(probe_font, old_text))
    start_w = _text_width(probe_font, old_text[:old_start]) if old_start else 0.0
    end_w = _text_width(probe_font, old_text[:old_end]) if old_end else full_w
    return [
        x0 + box_w * (start_w / full_w),
        y0,
        x0 + box_w * (end_w / full_w),
        y1,
    ]


def _replace_field_text_on_image(
    image_path: Path,
    field: FieldValue,
    new_text: str,
    *,
    font: str | None = None,
    font_size: float | None = None,
    x: float | None = None,
    y: float | None = None,
) -> None:
    image = Image.open(image_path)
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")
    draw = ImageDraw.Draw(image)

    font_name = font or field.font
    requested_size = float(font_size or field.font_size)
    intentional_style = font is not None or font_size is not None or x is not None or y is not None

    if len(field.rect) != 4:
        pil_font = _pil_font(font_name, requested_size, bold=not intentional_style)
        text_x = field.x if x is None else x
        text_y = field.y if y is None else y
        try:
            draw.text((text_x, text_y), new_text, fill=(0, 0, 0), font=pil_font, anchor="ls")
        except TypeError:
            draw.text((text_x, text_y - requested_size), new_text, fill=(0, 0, 0), font=pil_font)
        image.save(image_path)
        return

    x0, y0, x1, y1 = field.rect
    box_w = max(1.0, x1 - x0)
    box_h = max(1.0, y1 - y0)
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)

    span = None if intentional_style else _changed_middle(field.text, new_text)
    probe = _pil_font(font_name, min(requested_size, box_h * 0.95), bold=False)

    if span is not None:
        old_start, old_end, new_start, new_end = span
        old_value = field.text[old_start:old_end]
        draw_text = new_text[new_start:new_end]
        value_rect = _value_subrect(field, field.text, old_start, old_end, probe)
        # Match typeface to the original VALUE pixels only (leave label untouched).
        matched = _match_font_to_crop(
            image,
            value_rect,
            old_value,
            ink=ink,
            bg=bg,
            max_size=min(requested_size, box_h * 0.95),
        )
        # Recompute subrect with the matched metrics, then wipe + redraw value only.
        value_rect = _value_subrect(field, field.text, old_start, old_end, matched)
        vx0, vy0, vx1, vy1 = value_rect
        draw.rectangle(
            [vx0 - 1, max(0, vy0 - 1), vx1 + 1, vy1 + 1],
            fill=bg,
        )
        pil_font = _fit_font_path_to_width(
            draw_text,
            template_font=matched,
            max_width=max(4.0, vx1 - vx0),
            max_size=min(requested_size, box_h * 0.95),
        )
        text_x = vx0
        text_y = vy1 - 1.0
    else:
        draw_text = new_text
        if intentional_style:
            pil_font = _pil_font(font_name, requested_size, bold=False)
        else:
            matched = _match_font_to_crop(
                image,
                field.rect,
                field.text,
                ink=ink,
                bg=bg,
                max_size=min(requested_size, box_h * 0.95),
            )
            pil_font = _fit_font_path_to_width(
                draw_text,
                template_font=matched,
                max_width=box_w,
                max_size=min(requested_size, box_h * 0.95),
            )
        draw.rectangle(
            [max(0, x0 - 1), max(0, y0 - 1), x1 + 1, y1 + 1],
            fill=bg,
        )
        text_x = field.x if x is None else x
        text_y = field.y if y is None else y

    try:
        draw.text((text_x, text_y), draw_text, fill=ink, font=pil_font, anchor="ls")
    except TypeError:
        draw.text((text_x, text_y - requested_size), draw_text, fill=ink, font=pil_font)

    suffix = image_path.suffix.lower()
    save_kwargs: dict = {}
    if suffix in {".jpg", ".jpeg"}:
        if image.mode != "RGB":
            image = image.convert("RGB")
        save_kwargs["quality"] = 95
    image.save(image_path, **save_kwargs)


def _replace_field_text_on_pdf(
    pdf_path: Path,
    field: FieldValue,
    new_text: str,
    *,
    font: str | None = None,
    font_size: float | None = None,
    x: float | None = None,
    y: float | None = None,
) -> None:
    doc = pymupdf.open(pdf_path)
    page = doc[field.page]
    page.add_redact_annot(pymupdf.Rect(field.rect), fill=(1, 1, 1))
    page.apply_redactions()

    font_name = font or field.font
    font_file = resolve_font_file(font_name)
    insert_kwargs: dict = {
        "fontsize": font_size or field.font_size,
    }
    if font_file:
        # Embed the TTF under a stable alias (Base-14 names can't carry Arial/Calibri).
        insert_kwargs["fontname"] = (font_name or "custom").lower()
        insert_kwargs["fontfile"] = font_file
    else:
        insert_kwargs["fontname"] = font_name or BUILTIN_FONT_HELVETICA

    page.insert_text(
        (field.x if x is None else x, field.y if y is None else y),
        new_text,
        **insert_kwargs,
    )
    _save_pdf(doc, pdf_path)


def replace_field_text(
    file_path: Path,
    field: FieldValue,
    new_text: str,
    *,
    font: str | None = None,
    font_size: float | None = None,
    x: float | None = None,
    y: float | None = None,
) -> None:
    """Replace text on a working PDF or image, preserving the original format."""
    if is_image_template(file_path):
        _replace_field_text_on_image(
            file_path,
            field,
            new_text,
            font=font,
            font_size=font_size,
            x=x,
            y=y,
        )
        return
    _replace_field_text_on_pdf(
        file_path,
        field,
        new_text,
        font=font,
        font_size=font_size,
        x=x,
        y=y,
    )


_PDF_EMPHASIS_BUILTIN = {
    ("helv", "bold"): "hebo",
    ("helv", "italic"): "heit",
    ("tiro", "bold"): "tibo",
    ("tiro", "italic"): "tiit",
    ("cour", "bold"): "cobo",
    ("cour", "italic"): "coit",
}


def apply_text_emphasis(file_path: Path, field: FieldValue, style: str) -> None:
    """Redraw a field as bold, italic, or underlined (same glyphs otherwise)."""
    style = style.strip().lower()
    if style not in {"bold", "italic", "underline"}:
        raise ValueError(f"Unsupported emphasis style: {style}")

    if is_image_template(file_path):
        _apply_emphasis_on_image(file_path, field, style)
    else:
        _apply_emphasis_on_pdf(file_path, field, style)


def _apply_emphasis_on_pdf(pdf_path: Path, field: FieldValue, style: str) -> None:
    doc = pymupdf.open(pdf_path)
    page = doc[field.page]
    page.add_redact_annot(pymupdf.Rect(field.rect), fill=(1, 1, 1))
    page.apply_redactions()

    point = (field.x, field.y)
    size = field.font_size
    base = (field.font or BUILTIN_FONT_HELVETICA).lower()

    insert_kwargs: dict = {"fontsize": size}
    if style in {"bold", "italic"}:
        font_file = resolve_emphasis_font_file(style)
        if font_file:
            insert_kwargs["fontname"] = f"{base}-{style}"
            insert_kwargs["fontfile"] = font_file
        else:
            insert_kwargs["fontname"] = _PDF_EMPHASIS_BUILTIN.get(
                (base if base in {"helv", "tiro", "cour"} else "helv", style),
                "hebo" if style == "bold" else "heit",
            )
    else:
        font_file = resolve_font_file(base)
        if font_file:
            insert_kwargs["fontname"] = base
            insert_kwargs["fontfile"] = font_file
        else:
            insert_kwargs["fontname"] = base if base in _BUILTIN_FONTS else BUILTIN_FONT_HELVETICA

    page.insert_text(point, field.text, **insert_kwargs)

    if style == "underline":
        # Approximate underline just below the baseline across the field width.
        x0, _, x1, y1 = field.rect
        y_line = min(field.y + 1.5, y1)
        page.draw_line(
            pymupdf.Point(x0, y_line),
            pymupdf.Point(x1, y_line),
            color=(0, 0, 0),
            width=max(0.6, size * 0.06),
        )

    _save_pdf(doc, pdf_path)


def _apply_emphasis_on_image(image_path: Path, field: FieldValue, style: str) -> None:
    image = Image.open(image_path)
    if image.mode not in {"RGB", "RGBA", "L"}:
        image = image.convert("RGB")
    draw = ImageDraw.Draw(image)

    if len(field.rect) != 4:
        raise ValueError("Emphasis injection requires a field bounding box")

    x0, y0, x1, y1 = field.rect
    box_h = max(1.0, y1 - y0)
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    draw.rectangle([max(0, x0 - 1), max(0, y0 - 1), x1 + 1, y1 + 1], fill=bg)

    size = min(field.font_size, box_h * 0.95)
    font_file = None
    if style in {"bold", "italic"}:
        font_file = resolve_emphasis_font_file(style)
    if font_file:
        pil_font = _pil_font(field.font, size, bold=False, font_path=font_file)
    else:
        pil_font = _pil_font(field.font, size, bold=(style == "bold"))

    try:
        draw.text((field.x, field.y), field.text, fill=ink, font=pil_font, anchor="ls")
    except TypeError:
        draw.text((field.x, field.y - size), field.text, fill=ink, font=pil_font)

    if style == "underline":
        y_line = min(field.y + 2, y1)
        draw.line([(x0, y_line), (x1, y_line)], fill=ink, width=max(1, int(size * 0.08)))

    suffix = image_path.suffix.lower()
    save_kwargs: dict = {}
    if suffix in {".jpg", ".jpeg"}:
        if image.mode != "RGB":
            image = image.convert("RGB")
        save_kwargs["quality"] = 95
    image.save(image_path, **save_kwargs)


def extract_pdf_text(pdf_path: Path) -> str:
    doc = pymupdf.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return text


def _slugify_field_name(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (slug[:48] or "field")


def _unique_field_name(base: str, existing: dict[str, FieldValue]) -> str:
    if base not in existing:
        return base
    index = 2
    while f"{base}_{index}" in existing:
        index += 1
    return f"{base}_{index}"


def _normalize_font(font_name: str | None) -> str:
    if not font_name:
        return BUILTIN_FONT_HELVETICA
    lowered = font_name.lower()
    if "times" in lowered or "roman" in lowered:
        return BUILTIN_FONT_TIMES
    if "courier" in lowered:
        return "cour"
    if lowered in _BUILTIN_FONTS:
        return lowered
    return BUILTIN_FONT_HELVETICA


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def _placeholder_image_field(metadata: dict[str, str], width: int = 220) -> FieldValue:
    return FieldValue(
        text="[image document]",
        font=BUILTIN_FONT_HELVETICA,
        font_size=12.0,
        metadata=dict(metadata),
        x=12.0,
        y=24.0,
        page=0,
        rect=[10.0, 10.0, float(width), 28.0],
    )


def discover_fields_from_image(
    image_path: Path,
    *,
    metadata: dict[str, str] | None = None,
    page: int = 0,
) -> dict[str, FieldValue]:
    """OCR an image template into injectable fields with pixel bounding boxes."""
    cache_key = (
        str(image_path.resolve()),
        image_path.stat().st_mtime,
        image_path.stat().st_size,
    )
    cached = _ocr_cache.get(cache_key)
    if cached is not None:
        return {name: field.model_copy(deep=True) for name, field in cached.items()}

    meta = dict(metadata or {})
    if not meta:
        meta = read_document_metadata(image_path)

    ocr = _get_ocr_engine()
    result, _ = ocr(str(image_path))
    fields: dict[str, FieldValue] = {}

    for row in result or []:
        if not row or len(row) < 2:
            continue
        box, text = row[0], (row[1] or "").strip()
        if not text or not box:
            continue
        score = float(row[2]) if len(row) > 2 else 1.0
        if score < 0.45:
            continue

        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        height = max(8.0, y1 - y0)
        font_size = max(8.0, height * 0.85)

        base_name = _slugify_field_name(text)
        field_name = _unique_field_name(base_name, fields)
        fields[field_name] = FieldValue(
            text=text,
            font=BUILTIN_FONT_HELVETICA,
            font_size=font_size,
            metadata=dict(meta),
            x=x0,
            y=y1 - 1.0,
            page=page,
            rect=[x0, y0, x1, y1],
        )

    if not fields:
        with Image.open(image_path) as img:
            width = min(220, img.width - 10)
        fields["document_image"] = _placeholder_image_field(meta, width=width)

    _ocr_cache[cache_key] = {name: field.model_copy(deep=True) for name, field in fields.items()}
    return fields


def _discover_native_pdf_fields(
    doc: pymupdf.Document,
    metadata: dict[str, str],
) -> dict[str, FieldValue]:
    fields: dict[str, FieldValue] = {}
    for page_index, page in enumerate(doc):
        page_dict = page.get_text("dict")
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = (span.get("text") or "").strip()
                    if not text:
                        continue

                    bbox = span.get("bbox")
                    if not bbox or len(bbox) != 4:
                        continue

                    origin = span.get("origin")
                    if origin and len(origin) == 2:
                        x, y = float(origin[0]), float(origin[1])
                    else:
                        x, y = float(bbox[0]), float(bbox[3]) - 1.0

                    base_name = _slugify_field_name(text)
                    field_name = _unique_field_name(base_name, fields)
                    fields[field_name] = FieldValue(
                        text=text,
                        font=_normalize_font(span.get("font")),
                        font_size=float(span.get("size") or 12.0),
                        metadata=dict(metadata),
                        x=x,
                        y=y,
                        page=page_index,
                        rect=[float(v) for v in bbox],
                    )
    return fields


def discover_fields_from_pdf(pdf_path: Path) -> dict[str, FieldValue]:
    """Extract injectable fields from a native PDF text layer."""
    doc = pymupdf.open(pdf_path)
    metadata = {key: value for key, value in (doc.metadata or {}).items() if value}
    fields = _discover_native_pdf_fields(doc, metadata)
    if not fields:
        page = doc[0]
        fields["document_image"] = FieldValue(
            text="[empty document]",
            font=BUILTIN_FONT_HELVETICA,
            font_size=12.0,
            metadata=dict(metadata),
            x=12.0,
            y=24.0,
            page=0,
            rect=[10.0, 10.0, min(page.rect.width - 10.0, 220.0), 28.0],
        )
    doc.close()
    return fields
