from __future__ import annotations

import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from .field_text import split_label_and_value
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

_SERIF_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/Library/Fonts/Times New Roman.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "C:/Windows/Fonts/times.ttf",
    "C:/Windows/Fonts/georgia.ttf",
)
_SERIF_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
    "/Library/Fonts/Times New Roman Bold.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "C:/Windows/Fonts/timesbd.ttf",
)
_SANS_REGULAR_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/Library/Fonts/Microsoft/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_SANS_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_SERIF_ITALIC_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
    "/Library/Fonts/Times New Roman Italic.ttf",
    "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
    "C:/Windows/Fonts/timesi.ttf",
)
_SANS_ITALIC_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
    "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
    "/Library/Fonts/Arial Italic.ttf",
    "/Library/Fonts/Microsoft/Arial Italic.ttf",
    "C:/Windows/Fonts/ariali.ttf",
)
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


def slugify_document_id(name: str) -> str:
    """Turn a template stem into an id that is safe in URLs and globs."""
    stem = Path(name).stem
    slug = re.sub(r"[^\w.\-]+", "_", stem)
    slug = re.sub(r"_+", "_", slug).replace("_-", "-").strip("._-")
    return slug or "document"


def find_template_path(document_id: str) -> Path | None:
    """Resolve a template by id for PDF or common image extensions."""
    stem = document_id
    for suffix in (".pdf",) + tuple(IMAGE_SUFFIXES):
        candidate = TEMPLATES_DIR / f"{stem}{suffix}"
        if candidate.exists():
            return candidate
    if not TEMPLATES_DIR.exists():
        return None
    wanted = slugify_document_id(document_id)
    for path in TEMPLATES_DIR.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        if not (is_image_template(path) or path.suffix.lower() == ".pdf"):
            continue
        if path.stem == document_id or slugify_document_id(path.stem) == wanted:
            return path
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


def _is_builtin_pdf_font(font_name: str | None) -> bool:
    lowered = (font_name or "").lower()
    return lowered in _BUILTIN_FONTS or lowered in {"helvetica", "times-roman", "times", "courier"}


def resolve_emphasis_font_file(
    style: str,
    font_name: str | None = None,
    *,
    prefer_ttf: bool = False,
) -> str | None:
    """Bold/italic TTF in the same family as the original span font.

    Base-14 faces return None so PDF can use hebo/tibo, unless prefer_ttf
    (image OCR stores 'helv' and has no Base-14 glyphs to reuse).
    """
    key = style.strip().lower()
    if key not in {"bold", "italic"}:
        return None
    if not prefer_ttf and _is_builtin_pdf_font(font_name):
        return None
    if _is_serif_font_name(font_name or "") or (
        prefer_ttf and _builtin_fallback_font(font_name) == BUILTIN_FONT_TIMES
    ):
        return _first_existing_font(
            _SERIF_BOLD_CANDIDATES if key == "bold" else _SERIF_ITALIC_CANDIDATES
        )
    lowered = (font_name or "").lower()
    if (
        "sans" in lowered
        or "arial" in lowered
        or "calibri" in lowered
        or (prefer_ttf and _is_builtin_pdf_font(font_name))
    ):
        return _first_existing_font(
            _SANS_BOLD_CANDIDATES if key == "bold" else _SANS_ITALIC_CANDIDATES
        )
    if _builtin_fallback_font(font_name) == BUILTIN_FONT_TIMES:
        return _first_existing_font(
            _SERIF_BOLD_CANDIDATES if key == "bold" else _SERIF_ITALIC_CANDIDATES
        )
    return None


def available_custom_fonts() -> list[str]:
    """Logical custom font names that resolve to a real file on this machine."""
    return [name for name in _CUSTOM_FONT_CANDIDATES if resolve_font_file(name)]


def _load_truetype(path: str, size: float, index: int = 0) -> ImageFont.ImageFont:
    size_i = max(8, int(round(size)))
    try:
        return ImageFont.truetype(path, size_i, index=index)
    except OSError:
        if index:
            raise
        return ImageFont.truetype(path, size_i)


def _font_path_index(font: ImageFont.ImageFont) -> tuple[str | None, int]:
    path = getattr(font, "path", None)
    if not isinstance(path, str) or not Path(path).exists():
        return None, 0
    try:
        return path, int(getattr(font, "index", 0) or 0)
    except (TypeError, ValueError):
        return path, 0


def _pil_font(
    font_name: str | None,
    size: float,
    *,
    bold: bool = False,
    font_path: str | None = None,
    index: int = 0,
) -> ImageFont.ImageFont:
    """Load a system TTF close to the requested PDF built-in / custom font."""
    size_i = max(8, int(round(size)))
    if font_path and Path(font_path).exists():
        try:
            return _load_truetype(font_path, size_i, index)
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


# (path, TTC face indices). Skip light/thin/condensed; those win noisy MAE races.
_FONT_FACE_CANDIDATES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("/System/Library/Fonts/Supplemental/Arial.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Verdana.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Verdana Bold.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Tahoma.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Tahoma Bold.ttf", (0,)),
    ("/System/Library/Fonts/Helvetica.ttc", (0, 1)),
    ("/System/Library/Fonts/HelveticaNeue.ttc", (0, 1, 10)),
    ("/Library/Fonts/Arial.ttf", (0,)),
    ("/Library/Fonts/Arial Bold.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Times New Roman.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Courier New.ttf", (0,)),
    ("/System/Library/Fonts/Supplemental/Courier New Bold.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", (0,)),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", (0,)),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", (0,)),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf", (0,)),
    ("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", (0,)),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", (0,)),
)
_SKIP_FACE_STYLE_TOKENS = (
    "italic",
    "oblique",
    "light",
    "thin",
    "ultra",
    "condensed",
    "black",
)


def _iter_system_font_faces() -> list[tuple[str, int]]:
    """Installed (path, index) faces used to approximate an OCR crop's typeface."""
    faces: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    for path, indices in _FONT_FACE_CANDIDATES:
        if not Path(path).exists():
            continue
        for index in indices:
            key = (path, index)
            if key in seen:
                continue
            try:
                probe = _load_truetype(path, 12, index)
            except OSError:
                continue
            style = (getattr(getattr(probe, "font", None), "style", "") or "").lower()
            if any(token in style for token in _SKIP_FACE_STYLE_TOKENS):
                continue
            seen.add(key)
            faces.append(key)
    return faces


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


def _gray_ink_metrics(
    image: Image.Image, threshold: int
) -> tuple[tuple[int, int, int, int], float, float] | None:
    """Return (bbox, fill_ratio, aspect) of pixels darker than threshold."""
    gray = image.convert("L")
    pixels = list(gray.getdata())
    width, height = gray.size
    xs: list[int] = []
    ys: list[int] = []
    for index, value in enumerate(pixels):
        if value < threshold:
            xs.append(index % width)
            ys.append(index // width)
    if not xs:
        return None
    x0, y0, x1, y1 = min(xs), min(ys), max(xs) + 1, max(ys) + 1
    area = max(1, (x1 - x0) * (y1 - y0))
    fill = sum(1 for x, y in zip(xs, ys) if x0 <= x < x1 and y0 <= y < y1)
    return (x0, y0, x1, y1), fill / area, (x1 - x0) / max(1, y1 - y0)


def _ink_threshold(ink: tuple[int, int, int], bg: tuple[int, int, int]) -> int:
    mid = int((_luminance(ink) + _luminance(bg)) / 2)
    return max(8, min(248, mid))


def _size_for_ink_width(
    path: str,
    index: int,
    text: str,
    target_width: float,
    min_size: float,
    max_size: float,
) -> float:
    try:
        probe = _load_truetype(path, 24, index)
    except OSError:
        return min(max_size, max(min_size, 12.0))
    probe_w = _text_width(probe, text)
    if probe_w <= 1:
        return min(max_size, max(min_size, 12.0))
    size = 24.0 * target_width / probe_w
    return max(min_size, min(max_size, size))


def _aligned_crop_mae(
    target: Image.Image,
    rendered: Image.Image,
    target_bbox: tuple[int, int, int, int],
    render_bbox: tuple[int, int, int, int],
    bg: tuple[int, int, int],
) -> float:
    """Compare crops after lining up ink boxes — do not stretch glyphs to fit."""
    canvas = Image.new("RGB", target.size, bg)
    piece = rendered.crop(render_bbox)
    canvas.paste(piece, (target_bbox[0], target_bbox[1]))
    return _mean_abs_diff(_to_gray_bytes(target), _to_gray_bytes(canvas))


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
    Compares ink-box *shape* (not unaligned full-crop MAE) so Courier cannot
    win just by filling the OCR width.
    """
    fallback = _pil_font(BUILTIN_FONT_HELVETICA, max_size, bold=False)
    if not sample_text.strip() or len(rect) != 4:
        return fallback

    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, max(x0 + 1, x1)), min(image.height, max(y0 + 1, y1))
    width, height = x1 - x0, y1 - y0
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    bg3 = bg if len(bg) == 3 else (255, 255, 255)
    threshold = _ink_threshold(ink, bg3)
    target_metrics = _gray_ink_metrics(crop, threshold)
    if target_metrics is None:
        return fallback

    target_bbox, target_fill, target_aspect = target_metrics
    ink_w = float(target_bbox[2] - target_bbox[0])
    min_size = max(7.0, height * 0.4)
    size_cap = max(min_size, min(max(max_size, height * 1.35), height * 1.8))

    best_font = fallback
    best_score = 1e9
    for path, index in _iter_system_font_faces():
        base_size = _size_for_ink_width(
            path, index, sample_text, ink_w, min_size, size_cap
        )
        for size in (base_size, base_size * 0.92, base_size * 1.08):
            if size < min_size or size > size_cap * 1.05:
                continue
            try:
                font = _load_truetype(path, size, index)
            except OSError:
                break
            rendered = _render_text_on_bg(
                sample_text,
                font=font,
                width=width,
                height=height,
                bg=bg3,
                ink=ink,
            )
            render_metrics = _gray_ink_metrics(rendered, threshold)
            if render_metrics is None:
                continue
            render_bbox, render_fill, render_aspect = render_metrics
            score = _aligned_crop_mae(crop, rendered, target_bbox, render_bbox, bg3)
            score += abs(render_aspect - target_aspect) * 18.0
            score += abs(render_fill - target_fill) * 90.0
            style = (getattr(getattr(font, "font", None), "style", "") or "").lower()
            is_bold = style == "bold"
            if (target_fill >= 0.36) != is_bold:
                score += 16.0
            if score < best_score:
                best_score = score
                best_font = font
    return best_font


def _fit_font_path_to_width(
    text: str,
    *,
    template_font: ImageFont.ImageFont,
    max_width: float,
    max_size: float,
    min_size: float = 7.0,
    preferred_size: float | None = None,
) -> ImageFont.ImageFont:
    """Reuse the matched typeface file (and TTC index), only adjusting size to fit."""
    path, index = _font_path_index(template_font)
    size = preferred_size if preferred_size is not None else max(min_size, max_size)
    size = max(min_size, size)
    while size > min_size:
        if path:
            try:
                font = _load_truetype(path, size, index)
            except OSError:
                font = _pil_font(BUILTIN_FONT_HELVETICA, size, bold=False)
        else:
            font = _pil_font(BUILTIN_FONT_HELVETICA, size, bold=False)
        if _text_width(font, text) <= max_width or max_width <= 0:
            return font
        size -= 0.5
    if path:
        try:
            return _load_truetype(path, min_size, index)
        except OSError:
            pass
    return _pil_font(BUILTIN_FONT_HELVETICA, min_size, bold=False)


def _luminance(rgb: tuple[int, int, int]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _median_rgb(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    ordered = sorted(pixels, key=_luminance)
    return ordered[len(ordered) // 2]


def _ink_and_bg_from_pixels(
    pixels: list[tuple[int, int, int]],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Split a crop into dark ink and light paper/fill colours."""
    if not pixels:
        return (0, 0, 0), (255, 255, 255)
    lights = [p for p in pixels if _luminance(p) >= 140]
    # Glyph interiors only; anti-aliased edges (~80–130) look grey if included.
    darks = [p for p in pixels if _luminance(p) <= 70]
    if not darks:
        darks = [p for p in pixels if _luminance(p) <= 110]
    if lights and darks:
        darks_sorted = sorted(darks, key=_luminance)
        ink = darks_sorted[max(0, len(darks_sorted) // 5)]
        return ink, _median_rgb(lights)
    ordered = sorted(pixels, key=_luminance)
    if lights:
        return (0, 0, 0), _median_rgb(lights)
    if darks:
        bright = ordered[int(len(ordered) * 0.85) :]
        return _median_rgb(bright or ordered[-1:]), _median_rgb(darks)
    return ordered[len(ordered) // 8], ordered[(len(ordered) * 3) // 4]


def _crop_rgb_pixels(image: Image.Image, rect: list[float]) -> list[tuple[int, int, int]]:
    if len(rect) != 4:
        return []
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, max(x0 + 1, x1)), min(image.height, max(y0 + 1, y1))
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    return list(crop.getdata())


def _halo_pixels(
    image: Image.Image, rect: list[float], pad: int = 4
) -> list[tuple[int, int, int]]:
    """Pixels in a ring around the OCR box — local paper/cell fill, not glyph ink."""
    if len(rect) != 4:
        return []
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    ox0, oy0 = max(0, x0 - pad), max(0, y0 - pad)
    ox1, oy1 = min(image.width, x1 + pad), min(image.height, y1 + pad)
    rgb = image.convert("RGB")
    pixels: list[tuple[int, int, int]] = []
    for y in range(oy0, oy1):
        for x in range(ox0, ox1):
            if x0 <= x < x1 and y0 <= y < y1:
                continue
            pixels.append(rgb.getpixel((x, y)))
    return pixels


def _sample_ink_color(image: Image.Image, rect: list[float]) -> tuple[int, int, int]:
    """Pick a dark ink color from the OCR box (fallback black)."""
    return _ink_and_bg_from_pixels(_crop_rgb_pixels(image, rect))[0]


def _sample_bg_color(image: Image.Image, rect: list[float]) -> tuple[int, int, int]:
    """Local fill from a halo around the glyphs so tinted headers stay tinted."""
    halo = [
        pixel
        for pixel in _halo_pixels(image, rect)
        if _luminance(pixel) >= 80
    ]
    if halo:
        return _median_rgb(halo)
    return _ink_and_bg_from_pixels(_crop_rgb_pixels(image, rect))[1]


def _rgb01(color: tuple[int, int, int]) -> tuple[float, float, float]:
    return (color[0] / 255.0, color[1] / 255.0, color[2] / 255.0)


def _pixmap_rgb_pixels(pix: pymupdf.Pixmap) -> list[tuple[int, int, int]]:
    n = pix.n
    samples = pix.samples
    pixels: list[tuple[int, int, int]] = []
    if n == 1:
        for value in samples:
            pixels.append((value, value, value))
        return pixels
    step = n
    for i in range(0, len(samples) - 2, step):
        pixels.append((int(samples[i]), int(samples[i + 1]), int(samples[i + 2])))
    return pixels


def _sample_pdf_field_colors(
    page: pymupdf.Page, rect: list[float]
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Rasterise the field box and return (ink, background) as 0–255 RGB."""
    if len(rect) != 4:
        return (0, 0, 0), (255, 255, 255)
    box = pymupdf.Rect(rect) & page.rect
    if box.is_empty or box.width < 0.5 or box.height < 0.5:
        return (0, 0, 0), (255, 255, 255)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=box, alpha=False)
    if pix.n not in {1, 3, 4}:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    return _ink_and_bg_from_pixels(_pixmap_rgb_pixels(pix))


def _shared_prefix_len(left: str, right: str) -> int:
    label, value = split_label_and_value(left)
    if label and value and right.startswith(label):
        return len(label)

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


def _value_redraw_span(old: str, new: str) -> tuple[int, int, int, int] | None:
    """Character span to wipe/redraw, never including a leading label."""
    label, value = split_label_and_value(old)
    label_len = len(label) if label and value else 0

    if old != new:
        span = _changed_middle(old, new)
        if span is not None:
            old_start, old_end, new_start, new_end = span
            if label_len and old_start < label_len and new.startswith(label):
                old_start = label_len
                new_start = label_len
            return old_start, old_end, new_start, new_end

    if not (label and value):
        return None
    if new.startswith(label):
        return label_len, len(old), label_len, len(new)
    new_label, new_value = split_label_and_value(new)
    if new_label == label and new_value:
        return (
            label_len,
            label_len + len(value),
            len(new_label),
            len(new_label) + len(new_value),
        )
    return None


def _image_redraw_span(old: str, new: str) -> tuple[int, int, int, int] | None:
    """Image wipe/redraw span: the whole value, never a two-letter hole.

    Partial middle spans cannot be font-matched from a few glyphs, so the new
    letters land in the wrong face and sit on leftover original pixels.
    """
    if old == new:
        return _value_redraw_span(old, new)
    label, value = split_label_and_value(old)
    if label and value and new.startswith(label):
        label_len = len(label)
        return label_len, len(old), label_len, len(new)
    return None


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


def _ink_rect_in_image(
    image: Image.Image,
    rect: list[float],
    ink: tuple[int, int, int],
    bg: tuple[int, int, int],
) -> list[float]:
    """Pixel box of dark glyphs inside `rect`, in image coordinates."""
    if len(rect) != 4:
        return list(rect)
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, max(x0 + 1, x1)), min(image.height, max(y0 + 1, y1))
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    metrics = _gray_ink_metrics(crop, _ink_threshold(ink, bg))
    if metrics is None:
        return [float(x0), float(y0), float(x1), float(y1)]
    bx0, by0, bx1, by1 = metrics[0]
    return [float(x0 + bx0), float(y0 + by0), float(x0 + bx1), float(y0 + by1)]


def _origin_for_ink_box(
    font: ImageFont.ImageFont,
    text: str,
    ink_rect: list[float],
    *,
    baseline: float | None = None,
) -> tuple[float, float]:
    """Baseline-left origin so the drawn glyphs sit on the original ink box."""
    try:
        left, top, _, _ = font.getbbox(text, anchor="ls")
    except TypeError:
        left, top, _, _ = font.getbbox(text)
    text_x = ink_rect[0] - float(left)
    if baseline is not None:
        return text_x, baseline
    return text_x, ink_rect[1] - float(top)


def _image_rewrite_font(
    matched: ImageFont.ImageFont,
    draw_text: str,
    *,
    font_name: str | None,
    max_width: float,
    requested_size: float,
    original_size: float,
    box_h: float,
    preserve_face: bool,
    size_changed: bool,
) -> ImageFont.ImageFont:
    if not preserve_face:
        return _pil_font(font_name, requested_size, bold=False)
    matched_size = float(getattr(matched, "size", original_size) or original_size)
    if size_changed:
        # Apply the injector's delta to the *matched* size. OCR font_size is
        # only ~0.85 of the box, so requested_size often equals the original
        # visual size — fitting that into the ink box just looks bolder.
        draw_size = max(6.0, matched_size + (requested_size - original_size))
        path, index = _font_path_index(matched)
        if path:
            try:
                return _load_truetype(path, draw_size, index)
            except OSError:
                pass
        return _pil_font(font_name, draw_size, bold=False)
    return _fit_font_path_to_width(
        draw_text,
        template_font=matched,
        max_width=max_width,
        max_size=max(matched_size, min(requested_size, box_h * 1.15)),
        preferred_size=matched_size,
    )


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
    preserve_face = font is None
    dx = 0.0 if x is None else (x - field.x)
    dy = 0.0 if y is None else (y - field.y)

    if len(field.rect) != 4:
        pil_font = _pil_font(font_name, requested_size, bold=False)
        text_x = field.x + dx
        text_y = field.y + dy
        try:
            draw.text((text_x, text_y), new_text, fill=(0, 0, 0), font=pil_font, anchor="ls")
        except TypeError:
            draw.text((text_x, text_y - requested_size), new_text, fill=(0, 0, 0), font=pil_font)
        image.save(image_path)
        return

    x0, y0, x1, y1 = field.rect
    box_h = max(1.0, y1 - y0)
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    match_size = min(float(field.font_size), box_h * 0.95)

    span = _image_redraw_span(field.text, new_text)
    probe = _pil_font(font_name, match_size, bold=False)

    if span is not None:
        old_start, old_end, new_start, new_end = span
        old_value = field.text[old_start:old_end]
        draw_text = new_text[new_start:new_end]
        value_rect = _value_subrect(field, field.text, old_start, old_end, probe)
        ink_box = _ink_rect_in_image(image, value_rect, ink, bg)
        matched = _match_font_to_crop(
            image,
            value_rect,
            old_value,
            ink=ink,
            bg=bg,
            max_size=match_size,
        )
        value_rect = _value_subrect(field, field.text, old_start, old_end, matched)
        vx0, vy0, vx1, vy1 = value_rect
        draw.rectangle(
            [vx0 - 1, max(0, vy0 - 1), vx1 + 1, vy1 + 1],
            fill=bg,
        )
        pil_font = _image_rewrite_font(
            matched,
            draw_text,
            font_name=font_name,
            max_width=max(4.0, ink_box[2] - ink_box[0]),
            requested_size=requested_size,
            original_size=float(field.font_size),
            box_h=box_h,
            preserve_face=preserve_face,
            size_changed=font_size is not None,
        )
        if font_size is not None:
            text_x, text_y = _origin_for_ink_box(
                pil_font, draw_text, ink_box, baseline=field.y
            )
        else:
            text_x, text_y = _origin_for_ink_box(pil_font, draw_text, ink_box)
        text_x += dx
        text_y += dy
    else:
        draw_text = new_text
        ink_box = _ink_rect_in_image(image, field.rect, ink, bg)
        matched = _match_font_to_crop(
            image,
            field.rect,
            field.text,
            ink=ink,
            bg=bg,
            max_size=match_size,
        )
        pil_font = _image_rewrite_font(
            matched,
            draw_text,
            font_name=font_name,
            max_width=max(4.0, ink_box[2] - ink_box[0]),
            requested_size=requested_size,
            original_size=float(field.font_size),
            box_h=box_h,
            preserve_face=preserve_face,
            size_changed=font_size is not None,
        )
        draw.rectangle(
            [max(0, x0 - 1), max(0, y0 - 1), x1 + 1, y1 + 1],
            fill=bg,
        )
        if font_size is not None:
            text_x, text_y = _origin_for_ink_box(
                pil_font, draw_text, ink_box, baseline=field.y
            )
        else:
            text_x, text_y = _origin_for_ink_box(pil_font, draw_text, ink_box)
        text_x += dx
        text_y += dy

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


def _first_existing_font(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _styled_font_path(font_path: str | None, style: str) -> str | None:
    """Sibling bold/italic file next to a matched regular TTF."""
    if not font_path or style not in {"bold", "italic"}:
        return None
    path = Path(font_path)
    name = path.name
    if style == "bold":
        replacements = (
            ("Regular", "Bold"),
            (".ttf", " Bold.ttf"),
            (".ttf", "-Bold.ttf"),
            (".otf", " Bold.otf"),
        )
    else:
        replacements = (
            ("Regular", "Italic"),
            (".ttf", " Italic.ttf"),
            (".ttf", "-Italic.ttf"),
            (".ttf", "-Oblique.ttf"),
            (".otf", " Italic.otf"),
        )
    tried: set[str] = set()
    for old, new in replacements:
        if old not in name:
            continue
        candidate = path.with_name(name.replace(old, new, 1))
        key = str(candidate)
        if key in tried or candidate == path:
            continue
        tried.add(key)
        if candidate.exists():
            return key
    return None


def _styled_face(font: ImageFont.ImageFont, style: str) -> tuple[str, int] | None:
    """Bold/italic face in the same family as a matched crop font (TTF sibling or TTC index)."""
    if style not in {"bold", "italic"}:
        return None
    path, index = _font_path_index(font)
    if not path:
        return None
    sibling = _styled_font_path(path, style)
    if sibling and sibling != path:
        return sibling, 0
    try:
        family = _load_truetype(path, 12, index).font.family
    except (OSError, AttributeError):
        family = None
    for face_index in range(24):
        try:
            probe = _load_truetype(path, 12, face_index)
        except OSError:
            break
        face_style = (getattr(getattr(probe, "font", None), "style", "") or "").lower()
        face_family = getattr(getattr(probe, "font", None), "family", None)
        if family and face_family and face_family != family:
            continue
        if style == "bold" and face_style == "bold":
            return path, face_index
        if style == "italic" and face_style in {"italic", "oblique"}:
            return path, face_index
    return None


def _is_serif_font_name(font_name: str) -> bool:
    lowered = font_name.lower()
    if "sans" in lowered:
        return False
    return any(
        token in lowered
        for token in (
            "times",
            "roman",
            "serif",
            "georgia",
            "cambria",
            "garamond",
            "palatino",
        )
    )


def resolve_family_font_file(font_name: str | None) -> str | None:
    """A full system TTF in the same family (serif/sans, bold/regular).

    Embedded PDF subset fonts cannot be reused for new glyphs, so spelling
    and numerical redraws use a lookalike installed on the machine.
    """
    if not font_name:
        return None
    exact = resolve_font_file(font_name)
    if exact:
        return exact
    lowered = font_name.lower()
    bold = "bold" in lowered
    if _is_serif_font_name(font_name):
        return _first_existing_font(_SERIF_BOLD_CANDIDATES if bold else _SERIF_REGULAR_CANDIDATES)
    if "courier" in lowered or "mono" in lowered:
        return None
    if "sans" in lowered or "arial" in lowered or "calibri" in lowered:
        return _first_existing_font(_SANS_BOLD_CANDIDATES if bold else _SANS_REGULAR_CANDIDATES)
    return None


def _font_key(font_name: str | None) -> str:
    """Comparable font id: strip subset prefix and non-alphanumerics."""
    base = (font_name or "").split("+")[-1]
    return re.sub(r"[^a-z0-9]", "", base.lower())


def _builtin_fallback_font(font_name: str | None) -> str:
    """Map a PDF/system font name onto a Base-14 font when no TTF is available."""
    if not font_name:
        return BUILTIN_FONT_HELVETICA
    lowered = font_name.lower()
    if lowered in _BUILTIN_FONTS:
        return lowered
    if _is_serif_font_name(font_name):
        return BUILTIN_FONT_TIMES
    if "courier" in lowered or "mono" in lowered:
        return "cour"
    return BUILTIN_FONT_HELVETICA


def _font_insert_kwargs(font_name: str | None, *, preserve_face: bool) -> dict:
    """Build insert_text font args.

    When preserving the original face (spelling/numerical/etc.), use a full
    system TTF in the same family. Subset fonts embedded in the PDF cannot
    be reused for new glyphs.
    """
    if preserve_face and _is_builtin_pdf_font(font_name):
        return {"fontname": _builtin_fallback_font(font_name)}

    if preserve_face:
        family_file = resolve_family_font_file(font_name)
        if family_file:
            return {
                "fontname": "keep_" + (_font_key(font_name) or "orig")[:32],
                "fontfile": family_file,
            }
        return {"fontname": _builtin_fallback_font(font_name)}

    font_file = resolve_font_file(font_name)
    if font_file:
        return {
            "fontname": (font_name or "custom").lower(),
            "fontfile": font_file,
        }
    return {"fontname": _builtin_fallback_font(font_name)}


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
    insert_kwargs = _font_insert_kwargs(font or field.font, preserve_face=font is None)
    insert_kwargs["fontsize"] = font_size or field.font_size
    ink, bg = _sample_pdf_field_colors(page, field.rect)
    insert_kwargs["color"] = _rgb01(ink)

    page.add_redact_annot(pymupdf.Rect(field.rect), fill=_rgb01(bg))
    page.apply_redactions()

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
    point = pymupdf.Point(field.x, field.y)
    size = field.font_size
    base = field.font or BUILTIN_FONT_HELVETICA

    insert_kwargs: dict = {"fontsize": size}
    if style in {"bold", "italic"}:
        font_file = resolve_emphasis_font_file(style, base)
        if font_file:
            insert_kwargs["fontname"] = f"{_font_key(base) or 'orig'}-{style}"
            insert_kwargs["fontfile"] = font_file
            # WinAnsi TrueType renders in Preview; Type0/Identity-H often looks blank.
            insert_kwargs["set_simple"] = True
        else:
            builtin = _builtin_fallback_font(base)
            insert_kwargs["fontname"] = _PDF_EMPHASIS_BUILTIN.get(
                (builtin if builtin in {"helv", "tiro", "cour"} else "helv", style),
                "hebo" if style == "bold" else "heit",
            )
    else:
        insert_kwargs.update(_font_insert_kwargs(base, preserve_face=True))
        if insert_kwargs.get("fontfile"):
            insert_kwargs["set_simple"] = True

    ink, bg = _sample_pdf_field_colors(page, field.rect)
    insert_kwargs["color"] = _rgb01(ink)

    page.add_redact_annot(pymupdf.Rect(field.rect), fill=_rgb01(bg))
    page.apply_redactions()

    page.insert_text(point, field.text, **insert_kwargs)

    if style == "underline":
        x0, _, x1, y1 = field.rect
        y_line = min(field.y + 1.5, y1)
        page.draw_line(
            pymupdf.Point(x0, y_line),
            pymupdf.Point(x1, y_line),
            color=_rgb01(ink),
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
    match_size = min(field.font_size, box_h * 0.95)
    probe = _pil_font(field.font, match_size, bold=False)
    span = _value_redraw_span(field.text, field.text)
    if span is not None:
        old_start, old_end, _, _ = span
        wipe_rect = _value_subrect(field, field.text, old_start, old_end, probe)
        draw_text = field.text[old_start:old_end]
        match_rect = wipe_rect
    else:
        wipe_rect = [x0, y0, x1, y1]
        draw_text = field.text
        match_rect = field.rect
    ink_box = _ink_rect_in_image(image, match_rect, ink, bg)
    line_x0, line_x1 = ink_box[0], ink_box[2]

    matched = _match_font_to_crop(
        image,
        match_rect,
        draw_text,
        ink=ink,
        bg=bg,
        max_size=match_size,
    )
    size = float(getattr(matched, "size", None) or min(field.font_size, box_h * 0.95))
    styled = _styled_face(matched, style) if style in {"bold", "italic"} else None
    if styled:
        pil_font = _load_truetype(styled[0], size, styled[1])
    else:
        fallback_file = None
        if style in {"bold", "italic"}:
            fallback_file = resolve_emphasis_font_file(
                style, field.font, prefer_ttf=True
            )
        path, index = _font_path_index(matched)
        if fallback_file:
            pil_font = _pil_font(field.font, size, bold=False, font_path=fallback_file)
        elif path:
            pil_font = _pil_font(
                field.font, size, bold=(style == "bold"), font_path=path, index=index
            )
        else:
            pil_font = _pil_font(field.font, size, bold=(style == "bold"))

    text_x, text_y = _origin_for_ink_box(pil_font, draw_text, ink_box)

    wx0, wy0, wx1, wy1 = wipe_rect
    draw.rectangle(
        [max(0, wx0 - 1), max(0, wy0 - 1), wx1 + 1, wy1 + 1],
        fill=bg,
    )

    try:
        draw.text((text_x, text_y), draw_text, fill=ink, font=pil_font, anchor="ls")
    except TypeError:
        draw.text((text_x, text_y - size), draw_text, fill=ink, font=pil_font)

    if style == "underline":
        y_line = min(text_y + 2, wy1)
        draw.line([(line_x0, y_line), (line_x1, y_line)], fill=ink, width=max(1, int(size * 0.08)))

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
    """Keep the PDF's real font name so redraws can reuse the embedded face."""
    if not font_name:
        return BUILTIN_FONT_HELVETICA
    return font_name.split("+")[-1]


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def _iter_ocr_lines(result):
    if result is None:
        return

    if hasattr(result, "txts") and hasattr(result, "boxes"):
        boxes = result.boxes
        txts = result.txts
        if boxes is None or txts is None:
            return
        scores = result.scores or ()
        for index, (box, text) in enumerate(zip(boxes, txts)):
            score = float(scores[index]) if index < len(scores) else 1.0
            yield box, text, score
        return

    rows = result[0] if isinstance(result, tuple) else result
    for row in rows or []:
        if not row or len(row) < 2:
            continue
        score = float(row[2]) if len(row) > 2 else 1.0
        yield row[0], row[1], score


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
    result = ocr(str(image_path))
    fields: dict[str, FieldValue] = {}

    for box, raw_text, score in _iter_ocr_lines(result):
        text = (raw_text or "").strip()
        if not text or box is None:
            continue
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
