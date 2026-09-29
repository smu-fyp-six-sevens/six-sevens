from __future__ import annotations

import io
import os
import re
from collections import deque
from pathlib import Path
from typing import Iterable, cast

import pymupdf
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, PngImagePlugin

from .field_text import split_label_and_value
from .schemas import FieldValue

# PIL's FreeTypeFont and (bitmap-default) ImageFont don't share a base class,
# but callers here treat either as "a loaded PIL font" interchangeably.
PilFont = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "docker-compose.yml").exists():
            return parent
    return Path("/app")


def _dir_from_env(name: str, default: str) -> Path:
    env = os.environ.get(name, "").strip()
    if env:
        return Path(env)
    return _project_root() / default


TEMPLATES_DIR = _dir_from_env("INJECTION_DOCUMENTS_DIR", "document_templates")
DOCUMENTS_GENERATED_DIR = _dir_from_env("INJECTION_GENERATED_DIR", "documents_generated")

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
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_JPEG_SUFFIXES = {".jpg", ".jpeg"}
_JPEG_SAVE_KWARGS = {"quality": 95, "subsampling": 0, "optimize": True}

_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

_ocr_engine = None
_ocr_cache: dict[tuple[str, float, int], dict[str, FieldValue]] = {}


def ensure_data_dirs() -> None:
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    DOCUMENTS_GENERATED_DIR.mkdir(parents=True, exist_ok=True)


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
    """Resolve a template by id for PDF, PNG, or JPEG."""
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


def _save_raster_image(image: Image.Image, image_path: Path) -> None:
    suffix = image_path.suffix.lower()
    save_kwargs: dict = {}
    if suffix in _JPEG_SUFFIXES:
        if image.mode != "RGB":
            image = image.convert("RGB")
        save_kwargs.update(_JPEG_SAVE_KWARGS)
    image.save(image_path, **save_kwargs)


def _open_raster_image(image_path: Path) -> Image.Image:
    image = Image.open(image_path)
    if image.mode not in {"RGB", "RGBA", "L"}:
        return image.convert("RGB")
    return image


def _clamp_pixel_box(
    rect: list[float] | tuple[float, float, float, float],
    image: Image.Image,
    *,
    pad: float = 0.0,
) -> tuple[int, int, int, int]:
    x0 = int(round(float(rect[0]) - pad))
    y0 = int(round(float(rect[1]) - pad))
    x1 = int(round(float(rect[2]) + pad))
    y1 = int(round(float(rect[3]) + pad))
    x0, y0 = max(0, x0), max(0, y0)
    x1 = min(image.width, max(x0 + 1, x1))
    y1 = min(image.height, max(y0 + 1, y1))
    return x0, y0, x1, y1


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

    if suffix in _JPEG_SUFFIXES:
        if working.mode != "RGB":
            working = working.convert("RGB")
        exif = working.getexif()
        if metadata.get("author"):
            exif[_EXIF_ARTIST] = metadata["author"]
        if metadata.get("title"):
            exif[_EXIF_IMAGE_DESCRIPTION] = metadata["title"]
        if metadata.get("creator"):
            exif[_EXIF_SOFTWARE] = metadata["creator"]
        working.save(file_path, exif=exif, **_JPEG_SAVE_KWARGS)
        return

    raise ValueError(f"unsupported image type for metadata: {suffix}")


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


# Backwards-compatible alias
def read_pdf_metadata(pdf_path: Path) -> dict[str, str]:
    return read_document_metadata(pdf_path)


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
    return None


def _load_truetype(path: str, size: float, index: int = 0) -> PilFont:
    size_i = max(8, int(round(size)))
    try:
        return ImageFont.truetype(path, size_i, index=index)
    except OSError:
        if index:
            raise
        return ImageFont.truetype(path, size_i)


def _font_path_index(font: PilFont) -> tuple[str | None, int]:
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
) -> PilFont:
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
    for path, index in _iter_system_font_faces():
        try:
            return _load_truetype(path, size_i, index)
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
    ("/System/Library/Fonts/HelveticaNeue.ttc", (0, 1)),
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
    "medium",
)


def _iter_system_font_faces() -> list[tuple[str, int]]:
    """Installed (path, index) faces used to approximate an OCR crop's typeface."""
    faces: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    candidates = list(_FONT_FACE_CANDIDATES)
    fonts_root = Path("/usr/share/fonts")
    if fonts_root.exists():
        extra: list[tuple[str, tuple[int, ...]]] = []
        for path in sorted(fonts_root.rglob("*.ttf")):
            extra.append((str(path), (0,)))
        candidates.extend(extra)
    for path, indices in candidates:
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
            name = Path(path).name.lower()
            blob = f"{style} {name}"
            if any(token in blob for token in _SKIP_FACE_STYLE_TOKENS):
                continue
            seen.add(key)
            faces.append(key)
    return faces


def _font_face_kind(path: str, font: PilFont) -> str:
    """sans / serif / mono / other — from file name and FreeType family."""
    family = getattr(getattr(font, "font", None), "family", "") or ""
    style = getattr(getattr(font, "font", None), "style", "") or ""
    blob = f"{Path(path).name} {family} {style}".lower()
    if any(
        token in blob
        for token in ("courier", "mono", "menlo", "consolas", "nimbus mono")
    ):
        return "mono"
    compact = blob.replace("sans-serif", "sans").replace("sans serif", "sans")
    if any(
        token in compact
        for token in (
            "times",
            "georgia",
            "cambria",
            "garamond",
            "palatino",
            "liberation serif",
            "dejavuserif",
            "dejavu serif",
            "nimbus roman",
        )
    ):
        return "serif"
    if "serif" in compact and "sans" not in compact:
        return "serif"
    if any(
        token in compact
        for token in (
            "helvetica",
            "arial",
            "neue",
            "calibri",
            "verdana",
            "tahoma",
            "geneva",
            "inter",
            "roboto",
            "liberation sans",
            "liberationsans",
            "dejavu sans",
            "dejavusans",
            "nimbus sans",
        )
    ):
        return "sans"
    if "sans" in compact:
        return "sans"
    return "other"


def _pick_matched_font(
    best_for: dict[str, tuple[float, PilFont]],
    fallback: PilFont,
) -> PilFont:
    """Keep the source family. Sans wins close races (bills are almost always sans)."""
    if not best_for:
        return fallback
    ranked = sorted(best_for.items(), key=lambda item: item[1][0])
    kind, (score, font) = ranked[0]
    sans = best_for.get("sans")
    if kind == "sans" or sans is None:
        return font
    sans_score, sans_font = sans
    if score < sans_score * 0.85:
        return font
    return sans_font


def _text_width(pil_font: PilFont, text: str) -> float:
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
    font: PilFont,
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
    width, height = gray.size
    data = list(gray.getdata())
    xs: list[int] = []
    ys: list[int] = []
    row_hits = [0] * height
    for index, value in enumerate(data):
        if value < threshold:
            x = index % width
            y = index // width
            xs.append(x)
            ys.append(y)
            row_hits[y] += 1
    if not xs:
        return None
    y0, y1 = _primary_ink_row_span(row_hits)
    band_xs = [x for x, y in zip(xs, ys) if y0 <= y < y1]
    if not band_xs:
        y0, y1 = min(ys), max(ys) + 1
        band_xs = xs
    x0, x1 = min(band_xs), max(band_xs) + 1
    area = max(1, (x1 - x0) * (y1 - y0))
    fill = sum(1 for x, y in zip(xs, ys) if x0 <= x < x1 and y0 <= y < y1)
    return (x0, y0, x1, y1), fill / area, (x1 - x0) / max(1, y1 - y0)


def _primary_ink_row_span(row_hits: list[int]) -> tuple[int, int]:
    """Prefer the largest ink band when an OCR box clips a neighboring line."""
    bands: list[tuple[int, int, int]] = []
    start = None
    total = 0
    for row, hits in enumerate(row_hits):
        if hits > 0:
            if start is None:
                start = row
                total = 0
            total += hits
            continue
        if start is not None:
            bands.append((start, row, total))
            start = None
    if start is not None:
        bands.append((start, len(row_hits), total))
    if not bands:
        return 0, len(row_hits)
    # Merge bands separated by a 1px anti-alias gap.
    merged: list[tuple[int, int, int]] = [bands[0]]
    for start, end, total in bands[1:]:
        prev_start, prev_end, prev_total = merged[-1]
        if start - prev_end <= 1:
            merged[-1] = (prev_start, end, prev_total + total)
        else:
            merged.append((start, end, total))
    start, end, _total = max(merged, key=lambda band: (band[2], band[1] - band[0]))
    return start, end


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


def _glyph_ink_size(font: PilFont, text: str) -> tuple[float, float]:
    try:
        left, top, right, bottom = font.getbbox(text, anchor="ls")
    except TypeError:
        left, top, right, bottom = font.getbbox(text)
    return float(right - left), float(bottom - top)


def _fit_truetype_to_ink(
    path: str,
    index: int,
    text: str,
    *,
    target_width: float,
    target_height: float,
    min_size: float,
    max_size: float,
) -> PilFont:
    """Scale a chosen face so sample_text matches the original ink box height."""
    seed = max(min_size, min(max_size, target_height if target_height >= 1 else 12.0))
    best = _load_truetype(path, seed, index)
    best_err = 1e9
    lo, hi = float(min_size), float(max_size)
    for _ in range(14):
        mid = (lo + hi) / 2.0
        font = _load_truetype(path, mid, index)
        _width, height = _glyph_ink_size(font, text)
        err = abs(height - target_height)
        if target_width >= 1:
            err += 0.15 * abs(_width - target_width)
        if err < best_err:
            best_err = err
            best = font
        if height > target_height:
            hi = mid
        else:
            lo = mid
    return best


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
) -> PilFont:
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

    best_for: dict[str, tuple[float, PilFont]] = {}
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
            if (target_fill >= 0.32) != is_bold:
                score += 24.0
            kind = _font_face_kind(path, font)
            previous = best_for.get(kind)
            if previous is None or score < previous[0]:
                best_for[kind] = (score, font)
    chosen = _pick_matched_font(best_for, fallback)
    path, index = _font_path_index(chosen)
    if not path:
        return chosen
    try:
        return _fit_truetype_to_ink(
            path,
            index,
            sample_text,
            target_width=ink_w,
            target_height=float(target_bbox[3] - target_bbox[1]),
            min_size=min_size,
            max_size=size_cap,
        )
    except OSError:
        return chosen


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
            # .convert("RGB") guarantees getpixel() returns a 3-int tuple,
            # though PIL's stub types it as a union covering every mode.
            pixels.append(cast(tuple[int, int, int], rgb.getpixel((x, y))))
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


def _common_prefix_len(left: str, right: str) -> int:
    index = 0
    for a, b in zip(left, right):
        if a != b:
            break
        index += 1
    return index


def _shared_prefix_len(left: str, right: str) -> int:
    label, value = split_label_and_value(left)
    if label and value and right.startswith(label):
        return len(label)

    index = _common_prefix_len(left, right)
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
        prefix = _common_prefix_len(old, new)
        prefix = prefix if prefix >= 3 else 0
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
            return span

    if not (label and value):
        return None
    if new.startswith(label):
        return label_len, len(old), label_len, len(new)
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
    probe_font: PilFont,
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
    font: PilFont,
    text: str,
    ink_rect: list[float],
    *,
    baseline: float | None = None,
    align: str = "left",
) -> tuple[float, float]:
    """Baseline origin so the drawn glyphs sit on the original ink box."""
    try:
        left, top, right, _bottom = font.getbbox(text, anchor="ls")
    except TypeError:
        left, top, right, _bottom = font.getbbox(text)
    if align == "right":
        text_x = ink_rect[2] - float(right)
    else:
        text_x = ink_rect[0] - float(left)
    if baseline is not None:
        return text_x, baseline
    return text_x, ink_rect[1] - float(top)


def _image_rewrite_font(
    matched: PilFont,
    *,
    font_name: str | None,
    requested_size: float,
    original_size: float,
    preserve_face: bool,
    size_changed: bool,
) -> PilFont:
    if not preserve_face:
        return _pil_font(font_name, requested_size, bold=False)
    if not size_changed:
        # Spelling/numerical/etc: keep the matched face and size; do not squeeze.
        return matched
    matched_size = float(getattr(matched, "size", original_size) or original_size)
    draw_size = max(6.0, matched_size + (requested_size - original_size))
    path, index = _font_path_index(matched)
    if path:
        try:
            return _load_truetype(path, draw_size, index)
        except OSError:
            pass
    return _pil_font(font_name, draw_size, bold=False)


def _glyph_box(
    font: PilFont,
    text: str,
    origin: tuple[float, float],
) -> list[float]:
    try:
        left, top, right, bottom = font.getbbox(text, anchor="ls")
    except TypeError:
        left, top, right, bottom = font.getbbox(text)
    ox, oy = origin
    return [ox + float(left), oy + float(top), ox + float(right), oy + float(bottom)]


def _union_wipe_rect(original: list[float], glyphs: list[float], pad: float = 2.0) -> list[float]:
    return [
        min(original[0], glyphs[0]) - pad,
        min(original[1], glyphs[1]) - pad,
        max(original[2], glyphs[2]) + pad,
        max(original[3], glyphs[3]) + pad,
    ]


def _ink_line_wipe_rect(
    ink: list[float],
    extra: list[float] | None = None,
    *,
    pad_x: float = 2.0,
    pad_y: float = 1.0,
) -> list[float]:
    """Wipe the original ink line. Expand horizontally for new glyphs, not into other rows."""
    x0, y0, x1, y1 = ink[0], ink[1], ink[2], ink[3]
    if extra is not None:
        x0 = min(x0, extra[0])
        x1 = max(x1, extra[2])
    return [x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y]


def _keep_pixel_span(old: str, new: str) -> tuple[int, int, int, int] | None:
    """Prefix/suffix that can stay as original pixels when rewriting `old` → `new`."""
    if not old or not new or old == new:
        return None
    prefix = _common_prefix_len(old, new)
    old_i, new_i = len(old), len(new)
    while old_i > prefix and new_i > prefix and old[old_i - 1] == new[new_i - 1]:
        old_i -= 1
        new_i -= 1
    kept = prefix + (len(old) - old_i)
    if kept < 1:
        return None
    return prefix, old_i, prefix, new_i


def _mapped_span_xs(
    ink: list[float],
    font: PilFont,
    text: str,
    start: int,
    end: int,
) -> tuple[float, float]:
    full_w = max(1.0, _text_width(font, text))
    ink_w = max(1.0, ink[2] - ink[0])
    start_w = _text_width(font, text[:start]) if start else 0.0
    end_w = _text_width(font, text[:end]) if end else full_w
    return ink[0] + ink_w * (start_w / full_w), ink[0] + ink_w * (end_w / full_w)


def _snap_cut_x(
    image: Image.Image,
    ink: list[float],
    approx_x: float,
    threshold: int,
) -> float:
    """Nudge a splice x to a nearby column with little ink (the gap between letters)."""
    x0, y0, x1, y1 = _clamp_pixel_box(ink, image)
    gray = image.crop((x0, y0, x1, y1)).convert("L")
    width, height = gray.size
    if width < 3 or height < 1:
        return approx_x
    data = list(gray.getdata())
    cols = [
        sum(1 for row in range(height) if data[row * width + col] < threshold)
        for col in range(width)
    ]
    rel = max(0, min(width - 1, int(round(approx_x)) - x0))
    window = 5
    lo, hi = max(0, rel - window), min(width - 1, rel + window)
    peak = max(cols) or 1
    best, best_count = rel, cols[rel]
    for col in range(lo, hi + 1):
        if cols[col] < best_count:
            best, best_count = col, cols[col]
    if best_count > max(1, peak * 0.12):
        return approx_x
    return float(x0 + best)


def _fill_rect(image: Image.Image, rect: list[float], fill: tuple[int, int, int]) -> None:
    box = _clamp_pixel_box(rect, image)
    ImageDraw.Draw(image).rectangle(list(box), fill=fill)


def _draw_text_ls(
    image: Image.Image,
    origin: tuple[float, float],
    text: str,
    *,
    fill: tuple[int, int, int],
    font: PilFont,
) -> None:
    draw = ImageDraw.Draw(image)
    try:
        draw.text(origin, text, fill=fill, font=font, anchor="ls")
    except TypeError:
        size = float(getattr(font, "size", 12) or 12)
        draw.text((origin[0], origin[1] - size), text, fill=fill, font=font)


def _try_splice_rewritten_text(
    image: Image.Image,
    field: FieldValue,
    new_text: str,
    *,
    ink: tuple[int, int, int],
    bg: tuple[int, int, int],
    matched: PilFont,
    full_ink: list[float],
    align: str,
) -> bool:
    """Keep original prefix/suffix pixels; TTF-draw only the changed middle."""
    span = _keep_pixel_span(field.text, new_text)
    if span is None:
        return False
    old_start, old_end, new_start, new_end = span
    prefix = field.text[:old_start]
    suffix = field.text[old_end:]
    new_mid = new_text[new_start:new_end]

    threshold = _ink_threshold(ink, bg)
    prefix_x1 = _mapped_span_xs(full_ink, matched, field.text, 0, old_start)[1]
    suffix_x0 = _mapped_span_xs(full_ink, matched, field.text, old_end, len(field.text))[0]
    if prefix:
        prefix_x1 = _snap_cut_x(image, full_ink, prefix_x1, threshold)
    if suffix:
        suffix_x0 = _snap_cut_x(image, full_ink, suffix_x0, threshold)
    if prefix and suffix and prefix_x1 >= suffix_x0 - 0.5:
        return False

    probe = [
        prefix_x1 if prefix else full_ink[0],
        full_ink[1],
        suffix_x0 if suffix else full_ink[2],
        full_ink[3],
    ]
    if probe[2] - probe[0] < 2:
        return False
    old_mid_ink = _ink_rect_in_image(image, probe, ink, bg)
    line_w = max(1.0, full_ink[2] - full_ink[0])
    if (old_mid_ink[2] - old_mid_ink[0]) > line_w * 0.85:
        return False

    old_w = max(1.0, old_mid_ink[2] - old_mid_ink[0])
    old_h = max(1.0, old_mid_ink[3] - old_mid_ink[1])
    short_swap = (
        bool(prefix)
        and bool(suffix)
        and (old_end - old_start) <= 2
        and (new_end - new_start) <= 2
    )
    path, index = _font_path_index(matched)
    fitted = matched
    new_w = 0.0
    if new_mid:
        if path:
            cap = max(
                old_h * 2.4,
                float(getattr(matched, "size", old_h) or old_h) * 1.6,
            )
            probe_w = _glyph_ink_size(matched, new_mid)[0]
            width_target = old_w
            if not short_swap and abs(probe_w - old_w) > max(2.5, old_w * 0.35):
                width_target = 0.0
            try:
                fitted = _fit_truetype_to_ink(
                    path,
                    index,
                    new_mid,
                    target_width=width_target,
                    target_height=old_h,
                    min_size=max(6.0, old_h * 0.55),
                    max_size=cap,
                )
            except OSError:
                fitted = matched
            mid_crop = image.crop(_clamp_pixel_box(old_mid_ink, image))
            metrics = _gray_ink_metrics(mid_crop, threshold)
            if metrics is not None and metrics[1] >= 0.32:
                styled = _styled_face(fitted, "bold")
                if styled:
                    try:
                        fitted = _fit_truetype_to_ink(
                            styled[0],
                            styled[1],
                            new_mid,
                            target_width=width_target,
                            target_height=old_h,
                            min_size=max(6.0, old_h * 0.55),
                            max_size=cap,
                        )
                    except OSError:
                        pass
        new_w = max(1.0, _glyph_ink_size(fitted, new_mid)[0])
    shift = new_w - old_w if new_mid else -old_w

    # Width changes need a reflow; redraw the whole line instead of a visible seam.
    if not short_swap and (abs(shift) > 2.5 or not prefix or not suffix):
        return False

    wipe = [
        old_mid_ink[0],
        max(full_ink[1], old_mid_ink[1] - 1.0),
        old_mid_ink[2],
        min(full_ink[3], old_mid_ink[3] + 1.0),
    ]
    _fill_rect(image, wipe, bg)
    if new_mid:
        origin = _origin_for_ink_box(fitted, new_mid, old_mid_ink)
        _draw_text_ls(image, origin, new_mid, fill=ink, font=fitted)
    return True


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
    image = _open_raster_image(image_path)
    draw = ImageDraw.Draw(image)

    font_name = font or field.font
    requested_size = float(font_size or field.font_size)
    preserve_face = font is None
    dx = 0.0 if x is None else (x - field.x)
    dy = 0.0 if y is None else (y - field.y)

    if (
        new_text == field.text
        and font is None
        and font_size is None
        and (dx != 0.0 or dy != 0.0)
        and len(field.rect) == 4
    ):
        _shift_field_on_image(image, field, dx, dy)
        _save_raster_image(image, image_path)
        return

    if (
        new_text == field.text
        and font is None
        and font_size is not None
        and dx == 0.0
        and dy == 0.0
        and len(field.rect) == 4
    ):
        _scale_field_on_image(image, field, requested_size)
        _save_raster_image(image, image_path)
        return

    if (
        new_text != field.text
        and font is None
        and font_size is None
        and dx == 0.0
        and dy == 0.0
        and len(field.rect) == 4
    ):
        _mimic_rewritten_text_on_image(image, field, new_text)
        _save_raster_image(image, image_path)
        return

    if len(field.rect) != 4:
        pil_font = _pil_font(font_name, requested_size, bold=False)
        text_x = field.x + dx
        text_y = field.y + dy
        try:
            draw.text((text_x, text_y), new_text, fill=(0, 0, 0), font=pil_font, anchor="ls")
        except TypeError:
            draw.text((text_x, text_y - requested_size), new_text, fill=(0, 0, 0), font=pil_font)
        _save_raster_image(image, image_path)
        return

    x0, y0, x1, y1 = field.rect
    box_h = max(1.0, y1 - y0)
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    match_size = min(float(field.font_size), box_h * 0.95)

    span = _image_redraw_span(field.text, new_text)
    probe = _pil_font(font_name, match_size, bold=False)
    original_wipe = [x0, y0, x1, y1]

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
        original_wipe = list(value_rect)
        pil_font = _image_rewrite_font(
            matched,
            font_name=font_name,
            requested_size=requested_size,
            original_size=float(field.font_size),
            preserve_face=preserve_face,
            size_changed=font_size is not None,
        )
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
            font_name=font_name,
            requested_size=requested_size,
            original_size=float(field.font_size),
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
    wipe = _union_wipe_rect(
        original_wipe,
        _glyph_box(pil_font, draw_text, (text_x, text_y)),
    )
    draw.rectangle(
        [
            max(0, wipe[0]),
            max(0, wipe[1]),
            min(image.width, wipe[2]),
            min(image.height, wipe[3]),
        ],
        fill=bg,
    )

    try:
        draw.text((text_x, text_y), draw_text, fill=ink, font=pil_font, anchor="ls")
    except TypeError:
        draw.text((text_x, text_y - requested_size), draw_text, fill=ink, font=pil_font)

    _save_raster_image(image, image_path)


def _shift_field_on_image(
    image: Image.Image,
    field: FieldValue,
    dx: float,
    dy: float,
) -> None:
    """Move the original raster glyphs. Do not redraw with a new TTF."""
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    ink_box = _ink_rect_in_image(image, field.rect, ink, bg)
    box = _clamp_pixel_box(ink_box, image, pad=2.0)
    crop = image.crop(box).convert("RGB")
    ImageDraw.Draw(image).rectangle(list(box), fill=bg)
    dest = (int(round(box[0] + dx)), int(round(box[1] + dy)))
    image.paste(crop, dest)


def _scale_field_on_image(
    image: Image.Image,
    field: FieldValue,
    new_size: float,
) -> None:
    """Resize the original raster glyphs. Do not redraw with a new TTF."""
    original = max(6.0, float(field.font_size) or 12.0)
    scale = max(0.35, min(3.5, float(new_size) / original))
    if abs(scale - 1.0) < 0.02:
        return
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    ink_box = _ink_rect_in_image(image, field.rect, ink, bg)
    box = _clamp_pixel_box(ink_box, image, pad=2.0)
    crop = image.crop(box).convert("RGB")
    new_w = max(1, int(round(crop.width * scale)))
    new_h = max(1, int(round(crop.height * scale)))
    resized = crop.resize((new_w, new_h), Image.Resampling.LANCZOS)
    ImageDraw.Draw(image).rectangle(list(box), fill=bg)
    mid_x = (box[0] + box[2]) / 2.0
    dest_x = box[2] - new_w if mid_x > image.width * 0.55 else box[0]
    dest_y = box[3] - new_h
    image.paste(resized, (dest_x, dest_y))


def _mimic_rewritten_text_on_image(
    image: Image.Image,
    field: FieldValue,
    new_text: str,
) -> None:
    """Keep original glyphs where the text is unchanged; lookalike-TTF the rest."""
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    box_h = max(1.0, field.rect[3] - field.rect[1])
    match_size = min(float(field.font_size), box_h * 0.95)
    matched = _match_font_to_crop(
        image,
        field.rect,
        field.text,
        ink=ink,
        bg=bg,
        max_size=match_size,
    )
    full_ink = _ink_rect_in_image(image, field.rect, ink, bg)
    mid_x = (full_ink[0] + full_ink[2]) / 2.0
    align = "right" if mid_x > image.width * 0.55 else "left"
    if _try_splice_rewritten_text(
        image,
        field,
        new_text,
        ink=ink,
        bg=bg,
        matched=matched,
        full_ink=full_ink,
        align=align,
    ):
        return
    origin = _origin_for_ink_box(matched, new_text, full_ink, align=align)
    wipe = _ink_line_wipe_rect(full_ink, _glyph_box(matched, new_text, origin))
    _fill_rect(image, wipe, bg)
    _draw_text_ls(image, origin, new_text, fill=ink, font=matched)


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
    for old, new in replacements:
        if old not in name:
            continue
        candidate = path.with_name(name.replace(old, new, 1))
        if candidate.exists():
            return str(candidate)
    return None


def _styled_face(font: PilFont, style: str) -> tuple[str, int] | None:
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


def _is_sans_font_name(font_name: str) -> bool:
    lowered = font_name.lower()
    return any(
        token in lowered
        for token in (
            "helvetica",
            "neue",
            "arial",
            "calibri",
            "geneva",
            "verdana",
            "tahoma",
            "sans",
            "inter",
            "roboto",
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
    if _is_sans_font_name(font_name):
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
    image = _open_raster_image(image_path)
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

    _save_raster_image(image, image_path)


def extract_pdf_text(pdf_path: Path) -> str:
    doc = pymupdf.open(pdf_path)
    # get_text()'s return type is a union over every "mode" it supports;
    # the default mode ("text") always returns a plain string.
    text = "\n".join(cast(str, page.get_text()) for page in doc)
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

    with Image.open(image_path) as img:
        raster = img.convert("RGB")
    fields.update(
        _discover_raster_image_fields(raster, fields, metadata=meta, page=page)
    )

    if not fields:
        width = min(220, raster.width - 10)
        fields["document_image"] = _placeholder_image_field(meta, width=width)

    _separate_stacked_ocr_boxes(fields)
    _ocr_cache[cache_key] = {name: field.model_copy(deep=True) for name, field in fields.items()}
    return fields


def _separate_stacked_ocr_boxes(fields: dict[str, FieldValue]) -> None:
    """Stop stacked OCR boxes from sharing pixels (amount vs the label above it)."""
    items = [
        field
        for field in fields.values()
        if len(field.rect) == 4 and field.kind != "image"
    ]
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            ax0, ay0, ax1, ay1 = left.rect
            bx0, by0, bx1, by1 = right.rect
            overlap_x = min(ax1, bx1) - max(ax0, bx0)
            overlap_y = min(ay1, by1) - max(ay0, by0)
            if overlap_x <= 1.0 or overlap_y <= 1.0:
                continue
            left_h = max(1.0, ay1 - ay0)
            right_h = max(1.0, by1 - by0)
            left_mid = (ay0 + ay1) / 2.0
            right_mid = (by0 + by1) / 2.0
            if abs(left_mid - right_mid) < min(left_h, right_h) * 0.35:
                continue
            upper, lower = (left, right) if left_mid < right_mid else (right, left)
            mid = (upper.rect[3] + lower.rect[1]) / 2.0
            if mid <= upper.rect[1] + 1.0 or mid >= lower.rect[3] - 1.0:
                continue
            upper.rect[3] = mid
            lower.rect[1] = mid
            upper.y = upper.rect[3] - 1.0
            lower.y = lower.rect[3] - 1.0


_RASTER_IMAGE_MIN_PX = 28
_RASTER_IMAGE_MAX_PAGE_FRAC = 0.40
_RASTER_IMAGE_MIN_FILL = 0.12
_RASTER_IMAGE_MAX_OCR_OVERLAP = 0.50
_RASTER_IMAGE_MAX_ASPECT = 12.0
_RASTER_IMAGE_DETECT_MAX_SIDE = 480
_RASTER_IMAGE_COLOR_DIST = 42.0


def _dominant_paper_color(image: Image.Image) -> tuple[int, int, int]:
    """Most common coarse colour — usually the page/card background."""
    sample = image.resize((80, 50), Image.Resampling.BOX)
    counts: dict[tuple[int, int, int], int] = {}
    for pixel in sample.getdata():
        key = (pixel[0] // 16, pixel[1] // 16, pixel[2] // 16)
        counts[key] = counts.get(key, 0) + 1
    quantized = max(counts, key=lambda k: counts[k])
    return (
        quantized[0] * 16 + 8,
        quantized[1] * 16 + 8,
        quantized[2] * 16 + 8,
    )


def _rect_overlap_area(
    left: list[float] | tuple[float, float, float, float],
    right: list[float] | tuple[float, float, float, float],
) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _ocr_overlap_fraction(
    box: list[float],
    text_rects: list[list[float]],
) -> float:
    area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
    covered = sum(_rect_overlap_area(box, rect) for rect in text_rects)
    return covered / area


def _discover_raster_image_fields(
    image: Image.Image,
    text_fields: dict[str, FieldValue],
    *,
    metadata: dict[str, str],
    page: int,
) -> dict[str, FieldValue]:
    """
    Find logos/photos on a PNG/JPEG: blobs that are not page colour and not OCR text.

    Flat rasters have no embedded-image objects, so this is the equivalent of
    PDF `kind="image"` discovery.
    """
    width, height = image.size
    if width < _RASTER_IMAGE_MIN_PX or height < _RASTER_IMAGE_MIN_PX:
        return {}

    paper = _dominant_paper_color(image)
    scale = min(1.0, _RASTER_IMAGE_DETECT_MAX_SIDE / max(width, height))
    small_w = max(1, int(round(width * scale)))
    small_h = max(1, int(round(height * scale)))
    work = image.resize((small_w, small_h), Image.Resampling.BOX)
    mask = Image.new("L", (small_w, small_h), 0)
    mask_px = mask.load()
    work_px = work.load()
    assert mask_px is not None and work_px is not None
    for y in range(small_h):
        for x in range(small_w):
            red, green, blue = work_px[x, y]
            dist = (
                (red - paper[0]) ** 2
                + (green - paper[1]) ** 2
                + (blue - paper[2]) ** 2
            ) ** 0.5
            if dist >= _RASTER_IMAGE_COLOR_DIST:
                mask_px[x, y] = 255

    draw = ImageDraw.Draw(mask)
    text_rects = [list(field.rect) for field in text_fields.values() if len(field.rect) == 4]
    pad = 2.0 / max(scale, 1e-6)
    for rect in text_rects:
        draw.rectangle(
            [
                int(rect[0] * scale - pad),
                int(rect[1] * scale - pad),
                int(rect[2] * scale + pad),
                int(rect[3] * scale + pad),
            ],
            fill=0,
        )
    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))

    fields: dict[str, FieldValue] = {}
    page_area = float(width * height)
    for x0, y0, x1, y1 in _connected_pixel_boxes(mask, min_pixels=80):
        fill = _mask_fill_ratio(mask, x0, y0, x1, y1)
        orig = [
            x0 / scale,
            y0 / scale,
            x1 / scale,
            y1 / scale,
        ]
        orig_w = orig[2] - orig[0]
        orig_h = orig[3] - orig[1]
        if orig_w < _RASTER_IMAGE_MIN_PX or orig_h < _RASTER_IMAGE_MIN_PX:
            continue
        if fill < _RASTER_IMAGE_MIN_FILL:
            continue
        if (orig_w * orig_h) / page_area > _RASTER_IMAGE_MAX_PAGE_FRAC:
            continue
        aspect = max(orig_w, orig_h) / max(1.0, min(orig_w, orig_h))
        if aspect > _RASTER_IMAGE_MAX_ASPECT:
            continue
        if _ocr_overlap_fraction(orig, text_rects) > _RASTER_IMAGE_MAX_OCR_OVERLAP:
            continue
        name = _unique_field_name(
            f"image_{page}_{int(round(orig[0]))}_{int(round(orig[1]))}",
            {**text_fields, **fields},
        )
        fields[name] = FieldValue(
            text=f"[image {int(round(orig_w))}x{int(round(orig_h))}]",
            font=BUILTIN_FONT_HELVETICA,
            font_size=1.0,
            metadata=dict(metadata),
            x=orig[0],
            y=orig[1],
            page=page,
            rect=orig,
            kind="image",
        )
    return fields


def _mask_fill_ratio(
    mask: Image.Image, x0: int, y0: int, x1: int, y1: int
) -> float:
    crop = mask.crop((x0, y0, x1, y1))
    if crop.width < 1 or crop.height < 1:
        return 0.0
    ink = sum(1 for value in crop.getdata() if value >= 128)
    return ink / float(crop.width * crop.height)


def _connected_pixel_boxes(
    mask: Image.Image, *, min_pixels: int
) -> list[tuple[int, int, int, int]]:
    """Inclusive-start exclusive-end boxes of white blobs on an L mask."""
    width, height = mask.size
    pixels = mask.load()
    assert pixels is not None
    seen = [[False] * width for _ in range(height)]
    boxes: list[tuple[int, int, int, int]] = []
    for start_y in range(height):
        for start_x in range(width):
            if pixels[start_x, start_y] < 128 or seen[start_y][start_x]:
                continue
            queue: deque[tuple[int, int]] = deque([(start_x, start_y)])
            seen[start_y][start_x] = True
            min_x = max_x = start_x
            min_y = max_y = start_y
            count = 0
            while queue:
                x, y = queue.popleft()
                count += 1
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    if seen[ny][nx] or pixels[nx, ny] < 128:
                        continue
                    seen[ny][nx] = True
                    queue.append((nx, ny))
            if count < min_pixels:
                continue
            boxes.append((min_x, min_y, max_x + 1, max_y + 1))
    return boxes


def _discover_native_pdf_fields(
    doc: pymupdf.Document,
    metadata: dict[str, str],
) -> dict[str, FieldValue]:
    fields: dict[str, FieldValue] = {}
    # pymupdf.Document is iterable at runtime even though pyright's
    # enumerate() overload resolution doesn't recognise it as Iterable.
    for page_index, page in enumerate(cast(Iterable, doc)):
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
                        kind="text",
                    )
    return fields


_MIN_IMAGE_PT = 8.0
_MIN_IMAGE_PX = 8


def _discover_pdf_image_fields(
    doc: pymupdf.Document,
    metadata: dict[str, str],
    existing: dict[str, FieldValue],
) -> dict[str, FieldValue]:
    """Register embedded bitmap placements from PyMuPDF (not vector drawings)."""
    fields = dict(existing)
    seen: set[tuple] = set()
    for page_index, page in enumerate(cast(Iterable, doc)):
        for info in page.get_image_info() or []:
            bbox = info.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            rect = pymupdf.Rect(bbox) & page.rect
            if rect.is_empty or rect.width < _MIN_IMAGE_PT or rect.height < _MIN_IMAGE_PT:
                continue
            pixel_w = int(info.get("width") or 0)
            pixel_h = int(info.get("height") or 0)
            if pixel_w < _MIN_IMAGE_PX or pixel_h < _MIN_IMAGE_PX:
                continue
            key = (
                page_index,
                round(rect.x0, 1),
                round(rect.y0, 1),
                round(rect.x1, 1),
                round(rect.y1, 1),
            )
            if key in seen:
                continue
            seen.add(key)
            base = f"image_{page_index}_{int(round(rect.x0))}_{int(round(rect.y0))}"
            name = _unique_field_name(base, fields)
            fields[name] = FieldValue(
                text=f"[image {pixel_w}x{pixel_h}]",
                font=BUILTIN_FONT_HELVETICA,
                font_size=1.0,
                metadata=dict(metadata),
                x=float(rect.x0),
                y=float(rect.y0),
                page=page_index,
                rect=[float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)],
                kind="image",
            )
    return fields


def discover_fields_from_pdf(pdf_path: Path) -> dict[str, FieldValue]:
    """Extract injectable text spans and embedded image boxes from a PDF."""
    doc = pymupdf.open(pdf_path)
    metadata = {key: value for key, value in (doc.metadata or {}).items() if value}
    fields = _discover_native_pdf_fields(doc, metadata)
    fields = _discover_pdf_image_fields(doc, metadata, fields)
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
            kind="text",
        )
    doc.close()
    return fields


MANIPULATION_INDICATORS = (
    "resolution_mismatch",
    "compression_mismatch",
    "visible_boundary",
    "scaling_anomaly",
    "image_overlay",
    "quality_mismatch",
)

# Wide and short enough to read as a wrong aspect, not as blur.
SCALING_ANOMALY_WIDTH = 1.85
SCALING_ANOMALY_HEIGHT = 0.45


def overlay_field_text(
    file_path: Path,
    field: FieldValue,
    text: str,
    *,
    x: float | None = None,
    y: float | None = None,
) -> None:
    """Draw extra text over a field using the local paper, ink, typeface, and alignment."""
    if not text.strip():
        raise ValueError("overlay text must not be empty")
    if is_image_template(file_path):
        _overlay_field_text_on_image(file_path, field, text, x=x, y=y)
        return
    _overlay_field_text_on_pdf(file_path, field, text, x=x, y=y)


def manipulate_field_region(
    file_path: Path,
    field: FieldValue,
    indicator: str,
    *,
    scale_x: float | None = None,
    scale_y: float | None = None,
) -> None:
    """Apply a targeted image-forensic artefact to one field's bounding box."""
    indicator = indicator.strip().lower()
    if indicator not in MANIPULATION_INDICATORS:
        raise ValueError(
            f"Unknown image-manipulation indicator {indicator!r}. "
            f"Use one of: {', '.join(MANIPULATION_INDICATORS)}"
        )
    if len(field.rect) != 4:
        raise ValueError("image_manipulation requires a field bounding box")
    if is_image_template(file_path):
        _manipulate_region_on_image(
            file_path,
            field,
            indicator,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        return
    _manipulate_region_on_pdf(
        file_path,
        field,
        indicator,
        scale_x=scale_x,
        scale_y=scale_y,
    )


def _longest_true_run(values: list[bool]) -> int:
    longest = current = 0
    for flag in values:
        if flag:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _overlay_origin(
    field: FieldValue,
    *,
    x: float | None,
    y: float | None,
) -> tuple[float, float]:
    """Same baseline-left origin as the existing field value."""
    return (
        float(x) if x is not None else float(field.x),
        float(y) if y is not None else float(field.y),
    )


def _pdf_text_length(text: str, fontsize: float, insert_kwargs: dict) -> float:
    fontfile = insert_kwargs.get("fontfile")
    fontname = insert_kwargs.get("fontname") or "helv"
    try:
        font = pymupdf.Font(fontfile=fontfile) if fontfile else pymupdf.Font(fontname)
        # text_length's stub declares fontsize: int, but the C API accepts
        # (and this caller relies on) a float; cast is type-only, no coercion.
        return float(font.text_length(text, fontsize=cast(int, fontsize)))
    except Exception:
        return float(fontsize) * max(1, len(text)) * 0.5


def _pdf_glyph_ink_rect(page: pymupdf.Page, field: FieldValue) -> pymupdf.Rect | None:
    """Tight box around the field's glyphs, ignoring nearby table rules."""
    if len(field.rect) != 4:
        return None
    clip = pymupdf.Rect(field.rect) & page.rect
    if clip.is_empty or clip.width < 0.5 or clip.height < 0.5:
        return None
    scale = 3.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
    if pix.n not in {1, 3, 4}:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    pixels = _pixmap_rgb_pixels(pix)
    width, height = pix.width, pix.height
    if width < 2 or height < 2 or len(pixels) < width * height:
        return None
    dark = [_luminance(pixel) <= 110 for pixel in pixels[: width * height]]
    rule_row = [False] * height
    for row in range(height):
        flags = dark[row * width : (row + 1) * width]
        if _longest_true_run(flags) >= max(4, int(width * 0.55)):
            rule_row[row] = True
    xs: list[int] = []
    ys: list[int] = []
    for row in range(height):
        if rule_row[row]:
            continue
        offset = row * width
        for col in range(width):
            if dark[offset + col]:
                xs.append(col)
                ys.append(row)
    if not xs:
        return None
    return pymupdf.Rect(
        clip.x0 + min(xs) / scale,
        clip.y0 + min(ys) / scale,
        clip.x0 + (max(xs) + 1) / scale,
        clip.y0 + (max(ys) + 1) / scale,
    )


def _horizontal_rule_ys(page: pymupdf.Page, clip: pymupdf.Rect) -> list[float]:
    """Y positions of near-horizontal strokes that cross the field's column."""
    ys: list[float] = []
    x0, x1 = clip.x0 - 6, clip.x1 + 28
    y0, y1 = clip.y0 - 16, clip.y1 + 16
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if not item:
                continue
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                start, end = item[1], item[2]
                if abs(start.y - end.y) > 0.7:
                    continue
                left, right = min(start.x, end.x), max(start.x, end.x)
                y = (start.y + end.y) / 2
                if right < x0 or left > x1 or y < y0 or y > y1:
                    continue
                ys.append(float(y))
            elif kind == "re" and len(item) >= 2:
                rect = item[1]
                if rect.height > 1.4 or rect.width < 8:
                    continue
                if rect.x1 < x0 or rect.x0 > x1:
                    continue
                y = (rect.y0 + rect.y1) / 2
                if y0 <= y <= y1:
                    ys.append(float(y))
    return ys


def _overlay_cover_rect(
    page: pymupdf.Page,
    field: FieldValue,
    text: str,
    size: float,
    origin: tuple[float, float],
    insert_kwargs: dict,
) -> pymupdf.Rect:
    """Paper patch over the original glyphs only — not table rules around them."""
    origin_x, origin_y = origin
    glyphs = _pdf_glyph_ink_rect(page, field)
    if glyphs is None or glyphs.is_empty:
        if len(field.rect) == 4:
            glyphs = pymupdf.Rect(field.rect)
        else:
            glyphs = pymupdf.Rect(
                origin_x,
                origin_y - size * 0.72,
                origin_x + _pdf_text_length(field.text or text, size, insert_kwargs),
                origin_y + size * 0.22,
            )
    overlay_w = _pdf_text_length(text, size, insert_kwargs)
    x0, y0, x1, y1 = glyphs.x0, glyphs.y0, max(glyphs.x1, origin_x + overlay_w), glyphs.y1
    if len(field.rect) == 4:
        box = pymupdf.Rect(field.rect)
        x0 = min(x0, box.x0)
        y0 = min(y0, box.y0)
        x1 = max(x1, box.x1)
        y1 = max(y1, box.y1)
    pad = 0.4
    cover = pymupdf.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    probe = pymupdf.Rect(field.rect) if len(field.rect) == 4 else cover
    for rule_y in _horizontal_rule_ys(page, probe):
        if rule_y < origin_y:
            cover.y0 = max(cover.y0, rule_y + 0.85)
        elif rule_y > origin_y:
            cover.y1 = min(cover.y1, rule_y - 0.85)
    if cover.y1 <= cover.y0:
        cover.y0 = origin_y - size * 0.72
        cover.y1 = origin_y + size * 0.22
    return cover & page.rect


def _overlay_field_text_on_pdf(
    pdf_path: Path,
    field: FieldValue,
    text: str,
    *,
    x: float | None,
    y: float | None,
) -> None:
    """Paint matching paper over the glyphs, then redraw at the original origin.

    Original text operators are left in the file (unlike spelling, which redacts).
    Nearby drawings such as total underlines are left alone.
    """
    doc = pymupdf.open(pdf_path)
    page = doc[field.page]
    size = float(field.font_size or 12.0)
    insert_kwargs = _font_insert_kwargs(field.font, preserve_face=True)
    insert_kwargs["fontsize"] = size
    sample_rect = field.rect if len(field.rect) == 4 else [
        field.x,
        field.y - size,
        field.x + 40,
        field.y,
    ]
    ink, bg = _sample_pdf_field_colors(page, sample_rect)
    insert_kwargs["color"] = _rgb01(ink)
    if insert_kwargs.get("fontfile"):
        insert_kwargs["set_simple"] = True

    origin = _overlay_origin(field, x=x, y=y)
    cover = _overlay_cover_rect(page, field, text, size, origin, insert_kwargs)
    if not cover.is_empty:
        page.draw_rect(cover, color=None, fill=_rgb01(bg), width=0, overlay=True)
    page.insert_text(origin, text, **insert_kwargs)
    _save_pdf(doc, pdf_path)


def _horizontal_rule_ys_in_image(
    image: Image.Image,
    clip: list[float],
) -> list[float]:
    """Y positions of near-horizontal dark strokes that cross the field box."""
    x0, y0, x1, y1 = _clamp_pixel_box(clip, image, pad=2.0)
    width, height = x1 - x0, y1 - y0
    if width < 4 or height < 2:
        return []
    crop = image.crop((x0, y0, x1, y1)).convert("RGB")
    pixels = list(crop.getdata())
    dark = [_luminance(pixel) <= 110 for pixel in pixels]
    ys: list[float] = []
    min_run = max(4, int(width * 0.55))
    for row in range(height):
        flags = dark[row * width : (row + 1) * width]
        if _longest_true_run(flags) >= min_run:
            ys.append(y0 + row + 0.5)
    return ys


def _overlay_cover_rect_on_image(
    image: Image.Image,
    field: FieldValue,
    text: str,
    font: PilFont,
    origin: tuple[float, float],
    ink: tuple[int, int, int],
    bg: tuple[int, int, int],
) -> tuple[int, int, int, int]:
    """Paper patch over the original glyphs only — not table rules around them."""
    origin_x, origin_y = origin
    glyphs = _ink_rect_in_image(image, field.rect, ink, bg) if len(field.rect) == 4 else [
        origin_x,
        origin_y - float(getattr(font, "size", 12) or 12) * 0.72,
        origin_x + 40.0,
        origin_y + float(getattr(font, "size", 12) or 12) * 0.22,
    ]
    overlay_box = _glyph_box(font, text, origin)
    cover = _union_wipe_rect(glyphs, overlay_box, pad=2.0)
    if len(field.rect) == 4:
        cover = _union_wipe_rect(cover, field.rect, pad=0.0)
    size = float(getattr(font, "size", field.font_size) or 12.0)
    probe = field.rect if len(field.rect) == 4 else cover
    for rule_y in _horizontal_rule_ys_in_image(image, probe):
        if rule_y < origin_y:
            cover[1] = max(cover[1], rule_y + 1.0)
        elif rule_y > origin_y:
            cover[3] = min(cover[3], rule_y - 1.0)
    if cover[3] <= cover[1]:
        cover[1] = origin_y - size * 0.72
        cover[3] = origin_y + size * 0.22
    return _clamp_pixel_box(cover, image)


def _overlay_field_text_on_image(
    image_path: Path,
    field: FieldValue,
    text: str,
    *,
    x: float | None,
    y: float | None,
) -> None:
    """Keep original pixels for unchanged letters; match a TTF to this field for new ones."""
    if len(field.rect) != 4:
        raise ValueError("text_overlay requires a field bounding box")
    image = _open_raster_image(image_path)
    if image.mode != "RGB":
        image = image.convert("RGB")
    origin_override = None if x is None and y is None else _overlay_origin(field, x=x, y=y)
    if origin_override is None:
        _mimic_rewritten_text_on_image(image, field, text)
        _save_raster_image(image, image_path)
        return

    draw = ImageDraw.Draw(image)
    box_h = max(1.0, field.rect[3] - field.rect[1])
    ink = _sample_ink_color(image, field.rect)
    bg = _sample_bg_color(image, field.rect)
    pil_font = _match_font_to_crop(
        image,
        field.rect,
        field.text or text,
        ink=ink,
        bg=bg,
        max_size=box_h,
    )
    cover = _overlay_cover_rect_on_image(
        image, field, text, pil_font, origin_override, ink, bg
    )
    draw.rectangle(cover, fill=bg)
    try:
        draw.text(origin_override, text, fill=ink, font=pil_font, anchor="ls")
    except TypeError:
        size = float(getattr(pil_font, "size", box_h) or box_h)
        draw.text(
            (origin_override[0], origin_override[1] - size),
            text,
            fill=ink,
            font=pil_font,
        )
    _save_raster_image(image, image_path)


def _resolved_scale_factors(
    scale_x: float | None,
    scale_y: float | None,
) -> tuple[float, float]:
    width = SCALING_ANOMALY_WIDTH if scale_x is None else float(scale_x)
    height = SCALING_ANOMALY_HEIGHT if scale_y is None else float(scale_y)
    for name, value in (("scale_x", width), ("scale_y", height)):
        if value <= 0 or value > 5:
            raise ValueError(f"{name} must be between 0.05 and 5")
    return width, height


def _apply_manipulation(
    crop: Image.Image,
    indicator: str,
    *,
    scale_x: float | None = None,
    scale_y: float | None = None,
) -> Image.Image:
    width, height = crop.size
    if width < 2 or height < 2:
        return crop

    if indicator == "scaling_anomaly":
        spread, compress = _resolved_scale_factors(scale_x, scale_y)
        return crop.resize(
            (max(1, int(width * spread)), max(1, int(height * compress))),
            Image.Resampling.BICUBIC,
        )

    crop = crop.convert("RGB")
    if indicator == "resolution_mismatch":
        small_w = max(1, width // 3)
        small_h = max(1, height // 3)
        return crop.resize((small_w, small_h), Image.Resampling.BILINEAR).resize(
            (width, height), Image.Resampling.NEAREST
        )

    if indicator == "compression_mismatch":
        buffer = io.BytesIO()
        crop.save(buffer, format="JPEG", quality=16)
        buffer.seek(0)
        return Image.open(buffer).convert("RGB")

    if indicator == "visible_boundary":
        out = crop.convert("RGB")
        # crop was already .convert("RGB")'d above, so getpixel() always
        # returns a 3-int tuple here, though PIL's stub types it as a union.
        paper = cast(tuple[int, int, int], crop.getpixel((0, 0)))
        halo = tuple(max(0, min(255, channel - 22)) for channel in paper)
        thickness = max(3, min(width, height) // 16)
        ring = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(ring)
        draw.rectangle([0, 0, width - 1, height - 1], outline=170, width=thickness)
        if width > thickness * 2 + 2 and height > thickness * 2 + 2:
            draw.rectangle(
                [thickness, thickness, width - 1 - thickness, height - 1 - thickness],
                outline=70,
                width=max(1, thickness // 3),
            )
        ring = ring.filter(ImageFilter.GaussianBlur(radius=max(1.6, thickness * 0.45)))
        wash = Image.new("RGB", (width, height), halo)
        return Image.composite(wash, out, ring)

    if indicator == "image_overlay":
        ghost = ImageEnhance.Brightness(crop).enhance(1.06)
        ghost = ImageEnhance.Contrast(ghost).enhance(0.94)
        enlarged = ghost.resize(
            (max(width + 1, int(round(width * 1.08))), max(height + 1, int(round(height * 1.08)))),
            Image.Resampling.BICUBIC,
        )
        return enlarged.resize((width, height), Image.Resampling.BICUBIC)

    # quality_mismatch: brightness / contrast / colour / sharpness / noise
    out = ImageEnhance.Brightness(crop).enhance(1.18)
    out = ImageEnhance.Contrast(out).enhance(1.32)
    out = ImageEnhance.Color(out).enhance(0.72)
    out = ImageEnhance.Sharpness(out).enhance(2.4)
    out = out.filter(ImageFilter.UnsharpMask(radius=1, percent=140, threshold=2))
    noise = Image.effect_noise(out.size, 22).convert("RGB")
    return Image.blend(out, noise, 0.12)


def _pdf_clip_to_pil(page: pymupdf.Page, box: pymupdf.Rect) -> Image.Image:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=box, alpha=False)
    if pix.n == 1:
        image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    elif pix.n == 4:
        image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    else:
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return image.convert("RGB")


def _insert_pil_on_page(
    page: pymupdf.Page,
    image: Image.Image,
    box: pymupdf.Rect,
    *,
    jpeg: bool = False,
) -> None:
    buffer = io.BytesIO()
    if jpeg:
        image.convert("RGB").save(buffer, format="JPEG", quality=16)
    else:
        image.convert("RGB").save(buffer, format="PNG")
    page.insert_image(box, stream=buffer.getvalue(), keep_proportion=False)


def _flatten_rgba_on_paper(image: Image.Image, paper: Image.Image) -> Image.Image:
    """Bake transparency into local paper so viewers don't need a PDF soft mask."""
    if image.mode != "RGBA":
        return image.convert("RGB")
    base = paper.convert("RGB").resize(image.size, Image.Resampling.NEAREST).convert("RGBA")
    return Image.alpha_composite(base, image).convert("RGB")


def _insert_rect_for_treated(
    cover: pymupdf.Rect,
    crop: Image.Image,
    treated: Image.Image,
) -> pymupdf.Rect:
    """Place a resized crop at its new aspect, from the original top-left."""
    if crop.size == treated.size or crop.width < 1 or crop.height < 1:
        return cover
    return pymupdf.Rect(
        cover.x0,
        cover.y0,
        cover.x0 + cover.width * (treated.width / crop.width),
        cover.y0 + cover.height * (treated.height / crop.height),
    )


def _host_image_rect(page: pymupdf.Page, box: pymupdf.Rect) -> pymupdf.Rect | None:
    """Largest embedded image that contains this box (the card photo, if any)."""
    best: pymupdf.Rect | None = None
    for info in page.get_image_info() or []:
        bbox = info.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        rect = pymupdf.Rect(bbox)
        if rect.width < box.width * 1.3 or rect.height < box.height * 1.3:
            continue
        if (rect & box).get_area() < box.get_area() * 0.85:
            continue
        if best is None or rect.get_area() > best.get_area():
            best = rect
    if best is None:
        return None
    # Stay inside rounded card corners / drop shadows in the host photo.
    margin = min(14.0, best.width * 0.03, best.height * 0.03)
    inset = pymupdf.Rect(
        best.x0 + margin,
        best.y0 + margin,
        best.x1 - margin,
        best.y1 - margin,
    )
    return inset if inset.width > box.width and inset.height > box.height else best


def _clip_treated_to_bounds(
    treated: Image.Image,
    insert_box: pymupdf.Rect,
    bounds: pymupdf.Rect,
) -> tuple[Image.Image, pymupdf.Rect]:
    """Keep the stretched aspect; cut pixels that would paint outside the host."""
    visible = insert_box & bounds
    if visible.is_empty or insert_box.width < 0.5 or insert_box.height < 0.5:
        return treated, insert_box
    if (
        abs(visible.x0 - insert_box.x0) < 0.05
        and abs(visible.y0 - insert_box.y0) < 0.05
        and abs(visible.x1 - insert_box.x1) < 0.05
        and abs(visible.y1 - insert_box.y1) < 0.05
    ):
        return treated, insert_box
    scale_x = treated.width / insert_box.width
    scale_y = treated.height / insert_box.height
    left = max(0, int(round((visible.x0 - insert_box.x0) * scale_x)))
    top = max(0, int(round((visible.y0 - insert_box.y0) * scale_y)))
    right = min(treated.width, max(left + 1, int(round((visible.x1 - insert_box.x0) * scale_x))))
    bottom = min(treated.height, max(top + 1, int(round((visible.y1 - insert_box.y0) * scale_y))))
    if right - left < 2 or bottom - top < 2:
        return treated, insert_box
    return treated.crop((left, top, right, bottom)), visible


def _cover_slot_with_nearby_paper(page: pymupdf.Page, box: pymupdf.Rect) -> bool:
    """Hide the original mark by stretching a same-height neighbour strip horizontally."""
    pad = 12.0
    candidates = (
        pymupdf.Rect(box.x0 - pad, box.y0, box.x0 - 0.4, box.y1),
        pymupdf.Rect(box.x1 + 0.4, box.y0, box.x1 + pad, box.y1),
    )
    strip = None
    for candidate in candidates:
        candidate = candidate & page.rect
        if candidate.width >= 1.5 and candidate.height >= box.height * 0.8:
            strip = candidate
            break
    if strip is None:
        return False
    matrix = pymupdf.Matrix(2, 2)
    target = page.get_pixmap(matrix=matrix, clip=box, alpha=False)
    source = page.get_pixmap(matrix=matrix, clip=strip, alpha=False)
    paper = Image.frombytes("RGB", (source.width, source.height), source.samples)
    filled = paper.resize((target.width, target.height), Image.Resampling.NEAREST)
    _insert_pil_on_page(page, filled, box)
    return True


def _pdf_image_info_for_field(page: pymupdf.Page, field: FieldValue) -> dict | None:
    box = pymupdf.Rect(field.rect)
    best = None
    best_iou = 0.0
    for info in page.get_image_info(xrefs=True) or []:
        bbox = info.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        other = pymupdf.Rect(bbox)
        overlap = (box & other).get_area()
        union = (box | other).get_area()
        if union <= 0:
            continue
        iou = overlap / union
        if iou > best_iou:
            best_iou = iou
            best = info
    if best is None or best_iou < 0.4:
        return None
    return best


def _pil_from_pdf_image(doc: pymupdf.Document, xref: int) -> Image.Image | None:
    """Native bitmap (and mask) so scaling stays sharp instead of a 2x page raster."""
    if xref < 1:
        return None
    try:
        extracted = doc.extract_image(xref)
    except Exception:
        return None
    blob = extracted.get("image") if extracted else None
    if not blob:
        return None
    image = Image.open(io.BytesIO(blob))
    smask = extracted.get("smask") or 0
    if smask:
        try:
            mask_blob = doc.extract_image(int(smask)).get("image")
            assert mask_blob is not None
            mask = Image.open(io.BytesIO(mask_blob)).convert("L")
            image = image.convert("RGBA")
            if mask.size != image.size:
                mask = mask.resize(image.size, Image.Resampling.BILINEAR)
            image.putalpha(mask)
            return image
        except Exception:
            pass
    return image.convert("RGB")


def _manipulate_region_on_pdf(
    pdf_path: Path,
    field: FieldValue,
    indicator: str,
    *,
    scale_x: float | None = None,
    scale_y: float | None = None,
) -> None:
    doc = pymupdf.open(pdf_path)
    page = doc[field.page]
    box = pymupdf.Rect(field.rect) & page.rect
    if box.is_empty or box.width < 0.5 or box.height < 0.5:
        doc.close()
        raise ValueError("image_manipulation field box is empty")
    pad = 0.8
    cover = pymupdf.Rect(box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad) & page.rect
    paper_sample = _pdf_clip_to_pil(page, cover)
    # _pdf_clip_to_pil always returns an RGB image, so getpixel() always
    # returns a 3-int tuple here, though PIL's stub types it as a union.
    paper = _rgb01(cast(tuple[int, int, int], paper_sample.getpixel((0, 0))))
    crop = paper_sample
    insert_box = cover
    if indicator == "scaling_anomaly":
        info = _pdf_image_info_for_field(page, field)
        xref = int(info.get("xref") or 0) if info else 0
        extracted = _pil_from_pdf_image(doc, xref) if xref else None
        host = _host_image_rect(page, box)
        if extracted is not None:
            crop = extracted
        if not _cover_slot_with_nearby_paper(page, box):
            page.draw_rect(cover, color=None, fill=paper, width=0, overlay=True)
        treated = _apply_manipulation(
            crop,
            indicator,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        insert_box = _insert_rect_for_treated(box, crop, treated)
        bounds = (host & page.rect) if host is not None else page.rect
        treated, insert_box = _clip_treated_to_bounds(treated, insert_box, bounds)
        if treated.mode == "RGBA":
            treated = _flatten_rgba_on_paper(treated, _pdf_clip_to_pil(page, insert_box))
        _insert_pil_on_page(page, treated, insert_box)
        _save_pdf(doc, pdf_path)
        return
    treated = _apply_manipulation(crop, indicator)
    page.draw_rect(cover, color=None, fill=paper, width=0, overlay=True)
    _insert_pil_on_page(
        page,
        treated,
        insert_box,
        jpeg=indicator == "compression_mismatch",
    )
    _save_pdf(doc, pdf_path)


def _cover_slot_with_nearby_paper_image(
    image: Image.Image,
    box: tuple[int, int, int, int],
) -> bool:
    """Hide the original mark by stretching a same-height neighbour strip horizontally."""
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    if width < 2 or height < 2:
        return False
    pad = 12
    candidates = (
        (max(0, x0 - pad), y0, max(0, x0), y1),
        (min(image.width, x1), y0, min(image.width, x1 + pad), y1),
    )
    strip = None
    for cx0, cy0, cx1, cy1 in candidates:
        if cx1 - cx0 >= 2 and cy1 - cy0 >= height * 0.8:
            strip = (cx0, cy0, cx1, cy1)
            break
    if strip is None:
        return False
    paper = image.crop(strip).convert("RGB")
    filled = paper.resize((width, height), Image.Resampling.NEAREST)
    image.paste(filled, (x0, y0))
    return True


def _paste_treated_region(
    image: Image.Image,
    treated: Image.Image,
    insert_box: pymupdf.Rect,
) -> None:
    bounds = pymupdf.Rect(0, 0, image.width, image.height)
    treated, insert_box = _clip_treated_to_bounds(treated, insert_box, bounds)
    dest_w = max(1, int(round(insert_box.width)))
    dest_h = max(1, int(round(insert_box.height)))
    if treated.size != (dest_w, dest_h):
        treated = treated.resize((dest_w, dest_h), Image.Resampling.NEAREST)
    if treated.mode == "RGBA":
        paper = image.crop(
            _clamp_pixel_box(
                [insert_box.x0, insert_box.y0, insert_box.x1, insert_box.y1],
                image,
            )
        )
        treated = _flatten_rgba_on_paper(treated, paper)
    image.paste(treated.convert("RGB"), (int(round(insert_box.x0)), int(round(insert_box.y0))))


def _manipulate_region_on_image(
    image_path: Path,
    field: FieldValue,
    indicator: str,
    *,
    scale_x: float | None = None,
    scale_y: float | None = None,
) -> None:
    image = _open_raster_image(image_path).convert("RGB")
    box_px = _clamp_pixel_box(field.rect, image)
    cover_px = _clamp_pixel_box(field.rect, image, pad=0.8)
    if box_px[2] - box_px[0] < 2 or box_px[3] - box_px[1] < 2:
        raise ValueError("image_manipulation field box is empty")
    crop = image.crop(cover_px)
    box = pymupdf.Rect(*box_px)
    cover = pymupdf.Rect(*cover_px)
    if indicator == "scaling_anomaly":
        crop = image.crop(box_px)
        if not _cover_slot_with_nearby_paper_image(image, box_px):
            paper = crop.getpixel((0, 0))
            ImageDraw.Draw(image).rectangle(cover_px, fill=paper)
        treated = _apply_manipulation(
            crop,
            indicator,
            scale_x=scale_x,
            scale_y=scale_y,
        )
        insert_box = _insert_rect_for_treated(box, crop, treated)
        _paste_treated_region(image, treated, insert_box)
        _save_raster_image(image, image_path)
        return
    treated = _apply_manipulation(crop, indicator)
    paper = crop.getpixel((0, 0))
    ImageDraw.Draw(image).rectangle(cover_px, fill=paper)
    _paste_treated_region(image, treated, cover)
    _save_raster_image(image, image_path)
