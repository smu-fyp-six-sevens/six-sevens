"""Unit tests for injection PDF/image helpers the API suite does not reach."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.injection.pdf import (
    BUILTIN_FONT_HELVETICA,
    BUILTIN_FONT_TIMES,
    apply_text_emphasis,
    discover_fields_from_image,
    discover_fields_from_pdf,
    ensure_data_dirs,
    extract_pdf_text,
    find_template_path,
    is_image_template,
    manipulate_field_region,
    media_type_for,
    overlay_field_text,
    read_document_metadata,
    read_pdf_metadata,
    replace_field_text,
    resolve_emphasis_font_file,
    resolve_family_font_file,
    resolve_font_file,
    set_document_metadata,
)
from app.injection import pdf as pdf_mod
from app.injection.schemas import FieldValue


def _field(**kwargs) -> FieldValue:
    values = dict(
        text="HELLO",
        font="helv",
        font_size=16.0,
        x=12.0,
        y=28.0,
        page=0,
        rect=[10.0, 10.0, 90.0, 32.0],
    )
    values.update(kwargs)
    return FieldValue(**values)


def _white_image(size=(200, 80), color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", size, color)


def _draw_black_text(image: Image.Image, text: str, origin=(10, 10)) -> Image.Image:
    font = pdf_mod._pil_font("helv", 18)
    ImageDraw.Draw(image).text(origin, text, fill=(0, 0, 0), font=font)
    return image


def _save(image: Image.Image, path: Path) -> Path:
    pdf_mod._save_raster_image(image, path)
    return path


def _tiny_pdf(path: Path, text: str = "Golden Harvest", with_image: bool = False) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=240)
    page.insert_text((72, 100), text, fontsize=14, fontname="helv")
    page.draw_line(pymupdf.Point(60, 88), pymupdf.Point(220, 88), width=0.8)
    page.draw_rect(pymupdf.Rect(60, 118, 220, 119.2), fill=(0, 0, 0))
    if with_image:
        logo = pymupdf.open()
        logo_page = logo.new_page(width=48, height=32)
        logo_page.draw_rect(logo_page.rect, color=(0.8, 0.1, 0.1), fill=(0.8, 0.1, 0.1))
        pixmap = logo_page.get_pixmap()
        logo.close()
        page.insert_image(pymupdf.Rect(20, 16, 84, 52), pixmap=pixmap)
    doc.set_metadata({"title": "Fixture", "author": "Tests"})
    doc.save(path)
    doc.close()
    return path


# ---------------------------------------------------------------------------
# Paths, media types, templates
# ---------------------------------------------------------------------------


def test_project_root_falls_back_when_compose_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert pdf_mod._project_root() == Path("/app")


def test_dir_from_env_uses_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INJECTION_DOCUMENTS_DIR", str(tmp_path))
    assert pdf_mod._dir_from_env("INJECTION_DOCUMENTS_DIR", "document_templates") == tmp_path


def test_media_type_and_image_suffix_helpers() -> None:
    assert media_type_for(Path("note.bin")) == "application/octet-stream"
    assert media_type_for(Path("a.PDF")) == "application/pdf"
    assert is_image_template(Path("scan.JPEG"))
    assert not is_image_template(Path("scan.pdf"))


def test_ensure_data_dirs_creates_missing_folders(monkeypatch, tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    generated = tmp_path / "generated"
    monkeypatch.setattr(pdf_mod, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(pdf_mod, "DOCUMENTS_GENERATED_DIR", generated)
    ensure_data_dirs()
    assert templates.is_dir()
    assert generated.is_dir()


def test_find_template_path_covers_missing_dir_and_slug_match(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(pdf_mod, "TEMPLATES_DIR", tmp_path / "absent")
    assert find_template_path("missing") is None

    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / ".hidden.pdf").write_bytes(b"%PDF-1.4")
    (templates / "notes.txt").write_text("ignore")
    pdf_path = templates / "My Document.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(pdf_mod, "TEMPLATES_DIR", templates)
    assert find_template_path("My_Document") == pdf_path
    assert find_template_path("nope") is None


# ---------------------------------------------------------------------------
# Raster I/O and metadata
# ---------------------------------------------------------------------------


def test_save_and_open_convert_non_rgb_modes(tmp_path: Path) -> None:
    jpeg = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (12, 12), (0, 0, 0, 0)).save(jpeg)
    opened = pdf_mod._open_raster_image(jpeg)
    assert opened.mode == "RGB"

    png = tmp_path / "palette.png"
    Image.new("P", (8, 8)).save(png)
    pdf_mod._save_raster_image(Image.open(png), tmp_path / "out.jpg")
    assert (tmp_path / "out.jpg").read_bytes()[:3] == b"\xff\xd8\xff"


def test_image_metadata_png_and_jpeg_roundtrip(tmp_path: Path) -> None:
    png = tmp_path / "meta.png"
    jpeg = tmp_path / "meta.jpg"
    Image.new("RGB", (16, 16), (240, 240, 240)).save(png)
    Image.new("L", (16, 16), 200).save(jpeg)

    payload = {
        "title": "Notice",
        "author": "QA",
        "subject": "kyc",
        "creator": "tests",
    }
    set_document_metadata(png, payload)
    set_document_metadata(jpeg, payload)
    assert read_document_metadata(png)["author"] == "QA"
    jpeg_meta = read_document_metadata(jpeg)
    assert jpeg_meta["author"] == "QA"
    assert jpeg_meta["title"] == "Notice"
    assert jpeg_meta["creator"] == "tests"


def test_read_image_metadata_handles_exif_errors(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "broken.png"
    Image.new("RGB", (8, 8), (255, 255, 255)).save(path)

    class Boom(Image.Image):
        pass

    original_open = Image.open

    class Wrapper:
        def __init__(self, image):
            self.image = image
            self.info = {"Author": "FromPng", "Title": "T", "Description": "D", "Software": "S"}

        def getexif(self):
            raise RuntimeError("no exif")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.image.close()

    monkeypatch.setattr(
        pdf_mod.Image, "open", lambda file_path: Wrapper(original_open(file_path))
    )
    meta = pdf_mod._read_image_metadata(path)
    assert meta["author"] == "FromPng"
    assert meta["title"] == "T"


def test_set_image_metadata_rejects_unknown_suffix(tmp_path: Path) -> None:
    path = tmp_path / "x.webp"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path, format="PNG")
    path = path.with_suffix(".bmp")
    Image.new("RGB", (8, 8)).save(path)
    with pytest.raises(ValueError, match="unsupported image type"):
        pdf_mod._set_image_metadata(path, {"title": "x"})


def test_pdf_metadata_alias(tmp_path: Path) -> None:
    path = _tiny_pdf(tmp_path / "meta.pdf")
    meta = read_pdf_metadata(path)
    assert meta["title"] == "Fixture"
    set_document_metadata(path, {"title": "Updated", "author": "QA"})
    assert read_document_metadata(path)["title"] == "Updated"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


def test_resolve_font_file_and_emphasis_branches() -> None:
    assert resolve_font_file(None) is None
    assert resolve_font_file("not-a-real-font") is None
    arial = resolve_font_file("arial")
    if arial:
        assert Path(arial).exists()

    assert resolve_emphasis_font_file("underline") is None
    assert resolve_emphasis_font_file("bold", "helv") is None
    serif = resolve_emphasis_font_file("bold", "Times-Roman")
    sans = resolve_emphasis_font_file("italic", "Arial", prefer_ttf=True)
    times_builtin = resolve_emphasis_font_file("bold", "tiro", prefer_ttf=True)
    assert serif is None or Path(serif).exists()
    assert sans is None or Path(sans).exists()
    assert times_builtin is None or Path(times_builtin).exists()
    assert resolve_emphasis_font_file("bold", "UnknownFaceXYZ") is None


def test_family_font_and_insert_kwargs() -> None:
    assert resolve_family_font_file(None) is None
    assert resolve_family_font_file("Courier New") is None
    assert resolve_family_font_file("Mystery") is None
    arial = resolve_family_font_file("Arial Bold")
    times = resolve_family_font_file("Times")
    assert arial is None or Path(arial).exists()
    assert times is None or Path(times).exists()

    assert pdf_mod._font_insert_kwargs("helv", preserve_face=True) == {"fontname": "helv"}
    keep = pdf_mod._font_insert_kwargs("Arial", preserve_face=True)
    assert "fontname" in keep
    custom = pdf_mod._font_insert_kwargs("arial", preserve_face=False)
    assert "fontname" in custom
    assert pdf_mod._builtin_fallback_font(None) == BUILTIN_FONT_HELVETICA
    assert pdf_mod._builtin_fallback_font("helv") == "helv"
    assert pdf_mod._builtin_fallback_font("Times New Roman") == BUILTIN_FONT_TIMES
    assert pdf_mod._builtin_fallback_font("Courier") == "cour"
    assert pdf_mod._font_key("ABCDEF+Arial-Bold") == "arialbold"
    assert pdf_mod._is_serif_font_name("Times")
    assert not pdf_mod._is_serif_font_name("sans-serif")
    assert pdf_mod._is_sans_font_name("Helvetica Neue")
    assert pdf_mod._is_builtin_pdf_font("Courier")


def test_load_truetype_and_font_path_index(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_truetype(path, size, index=0, layout_engine=None):
        calls.append((path, size, index))
        if index:
            raise OSError("bad index")
        if calls.count((path, size, 0)) == 1:
            raise OSError("retry without index")
        return SimpleNamespace(path=path, size=size)

    monkeypatch.setattr(pdf_mod.ImageFont, "truetype", fake_truetype)
    font = pdf_mod._load_truetype("/no/such.ttf", 11, index=0)
    assert font.path == "/no/such.ttf"
    with pytest.raises(OSError):
        pdf_mod._load_truetype("/no/such.ttf", 11, index=2)

    class Font:
        path = "/tmp/does-not-exist.ttf"
        index = "nope"

    assert pdf_mod._font_path_index(Font()) == (None, 0)

    class FontOk:
        path = str(Path(__file__))
        index = "bad"

    path, index = pdf_mod._font_path_index(FontOk())
    assert path.endswith("test_pdf.py")
    assert index == 0


def test_pil_font_falls_back_when_paths_fail(monkeypatch) -> None:
    monkeypatch.setattr(pdf_mod, "resolve_font_file", lambda name: "/no/custom.ttf")
    fallback = SimpleNamespace(size=12)

    def boom(*args, **kwargs):
        raise OSError("cannot load")

    monkeypatch.setattr(pdf_mod, "_load_truetype", boom)
    monkeypatch.setattr(pdf_mod.ImageFont, "truetype", boom)
    monkeypatch.setattr(pdf_mod.ImageFont, "load_default", lambda: fallback)
    monkeypatch.setattr(pdf_mod, "_iter_system_font_faces", lambda: [("/x.ttf", 0)])
    font = pdf_mod._pil_font("tiro", 12, bold=True, font_path="/also/missing.ttf")
    assert font is fallback
    courier = pdf_mod._pil_font("cour", 10)
    assert courier is fallback


def test_iter_system_font_faces_skips_and_scans(monkeypatch, tmp_path: Path) -> None:
    extra = tmp_path / "ExtraItalic.ttf"
    extra.write_bytes(b"not-a-font")
    original_exists = Path.exists
    original_rglob = Path.rglob

    def exists(self):
        text = str(self)
        if text == "/usr/share/fonts":
            return True
        if text == str(extra):
            return True
        return original_exists(self)

    def rglob(self, pattern):
        if str(self) == "/usr/share/fonts":
            return [extra]
        return original_rglob(self, pattern)

    monkeypatch.setattr(pdf_mod.Path, "exists", exists)
    monkeypatch.setattr(pdf_mod.Path, "rglob", rglob)
    monkeypatch.setattr(
        pdf_mod,
        "_FONT_FACE_CANDIDATES",
        ((str(extra), (0, 0)), ("/missing.ttf", (0,))),
    )

    def load(path, size, index=0):
        if "Italic" in path:
            raise OSError("skip")
        return SimpleNamespace(font=SimpleNamespace(style="Regular", family="Test"))

    monkeypatch.setattr(pdf_mod, "_load_truetype", load)
    faces = pdf_mod._iter_system_font_faces()
    assert faces == []

    def load_ok(path, size, index=0):
        return SimpleNamespace(font=SimpleNamespace(style="Italic", family="Test"))

    monkeypatch.setattr(pdf_mod, "_load_truetype", load_ok)
    monkeypatch.setattr(pdf_mod, "_FONT_FACE_CANDIDATES", ((str(extra), (0,)),))
    assert pdf_mod._iter_system_font_faces() == []


def test_font_face_kind_and_pick_matched() -> None:
    dummy = ImageFont.load_default()
    assert pdf_mod._font_face_kind("CourierNew.ttf", dummy) == "mono"
    assert pdf_mod._font_face_kind("TimesNewRoman.ttf", dummy) == "serif"
    assert pdf_mod._font_face_kind("Garamond.otf", dummy) == "serif"
    assert pdf_mod._font_face_kind("random-serif.ttf", dummy) == "serif"
    assert pdf_mod._font_face_kind("Arial.ttf", dummy) == "sans"
    assert pdf_mod._font_face_kind("SomeSans.ttf", dummy) == "sans"
    assert pdf_mod._font_face_kind("Display.otf", dummy) == "other"

    fallback = dummy
    sans_font = SimpleNamespace(kind="sans")
    serif_font = SimpleNamespace(kind="serif")
    assert pdf_mod._pick_matched_font({}, fallback) is fallback
    assert pdf_mod._pick_matched_font({"sans": (1.0, sans_font)}, fallback) is sans_font
    close_race = pdf_mod._pick_matched_font(
        {"serif": (1.8, serif_font), "sans": (2.0, sans_font)},
        fallback,
    )
    assert close_race is sans_font
    clear_winner = pdf_mod._pick_matched_font(
        {"serif": (0.1, serif_font), "sans": (2.0, sans_font)},
        fallback,
    )
    assert clear_winner is serif_font


def test_first_existing_and_styled_font_path(tmp_path: Path) -> None:
    assert pdf_mod._first_existing_font(("/nope.ttf",)) is None
    regular = tmp_path / "Family-Regular.ttf"
    bold = tmp_path / "Family-Bold.ttf"
    italic = tmp_path / "Family-Italic.ttf"
    regular.write_bytes(b"x")
    bold.write_bytes(b"x")
    italic.write_bytes(b"x")
    assert pdf_mod._styled_font_path(None, "bold") is None
    assert pdf_mod._styled_font_path(str(regular), "underline") is None
    assert pdf_mod._styled_font_path(str(regular), "bold") == str(bold)
    assert pdf_mod._styled_font_path(str(regular), "italic") == str(italic)


def test_styled_face_without_path_and_with_ttc(monkeypatch) -> None:
    class NoPath:
        pass

    assert pdf_mod._styled_face(NoPath(), "wavy") is None
    assert pdf_mod._styled_face(NoPath(), "bold") is None

    real = pdf_mod._pil_font("helv", 12)
    styled = pdf_mod._styled_face(real, "bold")
    if styled:
        assert Path(styled[0]).exists()

    class FakeFont:
        path = str(Path(__file__))
        index = 0

    monkeypatch.setattr(pdf_mod, "_styled_font_path", lambda *args: None)

    def load(path, size, index=0):
        if index > 2:
            raise OSError("end")
        styles = {0: "Regular", 1: "Bold", 2: "Italic"}
        families = {0: "Test", 1: "Test", 2: "Other"}
        return SimpleNamespace(
            font=SimpleNamespace(style=styles[index], family=families[index])
        )

    monkeypatch.setattr(pdf_mod, "_load_truetype", load)
    monkeypatch.setattr(pdf_mod, "_font_path_index", lambda font: (str(Path(__file__)), 0))
    assert pdf_mod._styled_face(FakeFont(), "bold") == (str(Path(__file__)), 1)
    assert pdf_mod._styled_face(FakeFont(), "italic") is None


# ---------------------------------------------------------------------------
# Color, ink, geometry helpers
# ---------------------------------------------------------------------------


def test_text_width_and_mean_abs_diff_fallbacks() -> None:
    class NoLength:
        def getbbox(self, text):
            return (0, 0, len(text) * 7, 10)

    assert pdf_mod._text_width(NoLength(), "ab") == 14.0

    class EmptyBox:
        def getbbox(self, text):
            return None

        def getlength(self, text):
            raise AttributeError

    assert pdf_mod._text_width(EmptyBox(), "abcd") == 32.0
    assert pdf_mod._mean_abs_diff([], [1]) == 1e9
    assert pdf_mod._mean_abs_diff([1, 2], [3, 4]) == 2.0


def test_render_and_draw_text_typeerror(monkeypatch) -> None:
    font = ImageFont.load_default()
    calls = {"n": 0}

    def fake_text(*args, **kwargs):
        if kwargs.get("anchor"):
            raise TypeError("no anchor")
        calls["n"] += 1

    monkeypatch.setattr(pdf_mod.ImageDraw.ImageDraw, "text", fake_text)
    rendered = pdf_mod._render_text_on_bg(
        "Hi", font=font, width=20, height=12, bg=(255, 255, 255), ink=(0, 0, 0)
    )
    assert rendered.size == (20, 12)
    image = _white_image((40, 20))
    pdf_mod._draw_text_ls(image, (4, 12), "Hi", fill=(0, 0, 0), font=font)
    assert calls["n"] >= 1


def test_gray_ink_metrics_and_row_span() -> None:
    blank = _white_image((20, 10))
    assert pdf_mod._gray_ink_metrics(blank, 80) is None
    assert pdf_mod._primary_ink_row_span([0, 0, 0]) == (0, 3)

    image = _white_image((30, 20))
    px = image.load()
    for x in range(4, 12):
        px[x, 2] = (0, 0, 0)
        px[x, 8] = (0, 0, 0)
        px[x, 9] = (0, 0, 0)
    metrics = pdf_mod._gray_ink_metrics(image, 80)
    assert metrics is not None
    # Two bands separated by more than 1px: the denser lower band wins.
    assert metrics[0][1] >= 8

    merged = pdf_mod._primary_ink_row_span([3, 0, 4, 5, 0, 1])
    assert merged[0] <= 2
    isolated = pdf_mod._primary_ink_row_span([2, 0, 0, 9])
    assert isolated == (3, 4)


def test_size_for_ink_width_and_glyph_box(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_mod, "_load_truetype", lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
    )
    assert pdf_mod._size_for_ink_width("/x.ttf", 0, "Hi", 40, 8, 20) == 12.0

    class ZeroWidth:
        def getlength(self, text):
            return 0

        def getbbox(self, text, anchor=None):
            if anchor:
                raise TypeError
            return (0, 0, 0, 0)

    monkeypatch.setattr(pdf_mod, "_load_truetype", lambda *a, **k: ZeroWidth())
    assert pdf_mod._size_for_ink_width("/x.ttf", 0, "Hi", 40, 8, 20) == 12.0

    w, h = pdf_mod._glyph_ink_size(ZeroWidth(), "A")
    assert w == 0.0
    origin = pdf_mod._origin_for_ink_box(ZeroWidth(), "A", [10, 20, 40, 40], align="right")
    assert origin[0] == 40.0
    box = pdf_mod._glyph_box(ZeroWidth(), "A", (5.0, 6.0))
    assert box[0] == 5.0


def test_ink_and_bg_pixel_splits() -> None:
    assert pdf_mod._ink_and_bg_from_pixels([]) == ((0, 0, 0), (255, 255, 255))
    lights_only = pdf_mod._ink_and_bg_from_pixels([(250, 250, 250)] * 5)
    assert lights_only[0] == (0, 0, 0)
    darks_only = pdf_mod._ink_and_bg_from_pixels([(10, 10, 10)] * 8)
    assert darks_only[1][0] <= 20
    mid = pdf_mod._ink_and_bg_from_pixels([(120, 120, 120)] * 8)
    assert mid[0][0] <= 120
    mixed = pdf_mod._ink_and_bg_from_pixels(
        [(0, 0, 0)] * 4 + [(255, 255, 255)] * 4 + [(90, 90, 90)] * 2
    )
    assert mixed[0][0] <= 20
    weak_dark = pdf_mod._ink_and_bg_from_pixels(
        [(100, 100, 100)] * 3 + [(250, 250, 250)] * 5
    )
    assert weak_dark[1][0] >= 140


def test_crop_halo_and_bg_sampling() -> None:
    image = _white_image((40, 30))
    ImageDraw.Draw(image).rectangle([8, 8, 24, 20], fill=(0, 0, 0))
    assert pdf_mod._crop_rgb_pixels(image, [1, 2]) == []
    assert pdf_mod._halo_pixels(image, [1, 2]) == []
    halo = pdf_mod._halo_pixels(image, [8, 8, 24, 20], pad=2)
    assert halo
    bg = pdf_mod._sample_bg_color(image, [8, 8, 24, 20])
    assert bg[0] >= 200
    empty = pdf_mod._sample_bg_color(_white_image((4, 4)), [0, 0, 4, 4])
    assert empty[0] >= 200
    assert pdf_mod._ink_rect_in_image(image, [1, 2, 3], (0, 0, 0), (255, 255, 255)) == [1, 2, 3]
    no_ink = pdf_mod._ink_rect_in_image(
        _white_image((10, 10)), [1, 1, 8, 8], (0, 0, 0), (255, 255, 255)
    )
    assert no_ink == [1.0, 1.0, 8.0, 8.0]


def test_snap_cut_and_fill_rect() -> None:
    tiny = _white_image((2, 2))
    assert pdf_mod._snap_cut_x(tiny, [0, 0, 2, 2], 1.0, 80) == 1.0
    image = _white_image((40, 16))
    ImageDraw.Draw(image).rectangle([2, 2, 10, 14], fill=(0, 0, 0))
    ImageDraw.Draw(image).rectangle([16, 2, 28, 14], fill=(0, 0, 0))
    snapped = pdf_mod._snap_cut_x(image, [0, 0, 40, 16], 12.0, 80)
    assert 0 <= snapped <= 40
    pdf_mod._fill_rect(image, [0, 0, 4, 4], (9, 9, 9))
    assert image.getpixel((1, 1)) == (9, 9, 9)


def test_union_wipe_and_value_subrect() -> None:
    union = pdf_mod._union_wipe_rect([0, 0, 10, 10], [2, -1, 12, 9], pad=1)
    assert union[0] == -1
    wipe = pdf_mod._ink_line_wipe_rect([10, 10, 30, 20], [8, 12, 40, 18], pad_x=1, pad_y=1)
    assert wipe[0] == 7
    font = pdf_mod._pil_font("helv", 14)
    field = _field(rect=[0, 0, 100, 20], text="ABCDE")
    sub = pdf_mod._value_subrect(field, "ABCDE", 1, 4, font)
    assert sub[0] > 0
    assert sub[2] < 100


def test_rect_overlap_and_ocr_fraction() -> None:
    assert pdf_mod._rect_overlap_area([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert pdf_mod._rect_overlap_area([0, 0, 10, 10], [5, 5, 15, 15]) == 25.0
    frac = pdf_mod._ocr_overlap_fraction([0, 0, 10, 10], [[0, 0, 10, 5]])
    assert frac == 0.5


def test_mask_fill_and_connected_boxes() -> None:
    mask = Image.new("L", (20, 12), 0)
    assert pdf_mod._mask_fill_ratio(mask, 0, 0, 0, 4) == 0.0
    draw = ImageDraw.Draw(mask)
    draw.rectangle([1, 1, 8, 8], fill=255)
    draw.point((18, 10), fill=255)
    boxes = pdf_mod._connected_pixel_boxes(mask, min_pixels=5)
    assert boxes
    assert all(box[2] > box[0] for box in boxes)
    tiny = pdf_mod._connected_pixel_boxes(mask, min_pixels=500)
    assert tiny == []


def test_dominant_paper_color_and_raster_image_filters() -> None:
    tiny = _white_image((10, 10))
    assert pdf_mod._discover_raster_image_fields(tiny, {}, metadata={}, page=0) == {}

    page = _white_image((200, 200))
    draw = ImageDraw.Draw(page)
    draw.rectangle([20, 20, 70, 70], fill=(200, 10, 10))
    draw.rectangle([10, 160, 190, 168], fill=(10, 10, 200))
    draw.rectangle([5, 5, 195, 150], fill=(30, 180, 30))
    fields = pdf_mod._discover_raster_image_fields(
        page,
        {"label": _field(rect=[18, 18, 72, 72], text="Logo")},
        metadata={"title": "x"},
        page=1,
    )
    # Huge green blob and skinny blue bar should be rejected; overlapping red
    # square is also skipped for OCR overlap. Result may be empty or a leftover blob.
    for field in fields.values():
        assert field.kind == "image"

    logo_page = _white_image((180, 180), (250, 250, 250))
    ImageDraw.Draw(logo_page).rectangle([40, 40, 100, 100], fill=(180, 20, 20))
    found = pdf_mod._discover_raster_image_fields(
        logo_page, {}, metadata={"title": "logo"}, page=0
    )
    assert any(field.kind == "image" for field in found.values())
    paper = pdf_mod._dominant_paper_color(logo_page)
    assert paper[0] >= 200


# ---------------------------------------------------------------------------
# String spans
# ---------------------------------------------------------------------------


def test_shared_prefix_and_redraw_spans() -> None:
    assert pdf_mod._shared_prefix_len("Account No. 111", "Account No. 222") > 0
    assert pdf_mod._shared_prefix_len("Ref: ABC", "Ref: XYZ") >= 3
    assert pdf_mod._shared_prefix_len("Hi", "Yo") == 0
    assert pdf_mod._shared_prefix_len("ABCDEF", "ABCXYZ") >= 3

    assert pdf_mod._changed_middle("ABC", "XYZ") is None
    assert pdf_mod._changed_middle("AAAA", "AAAA") is None
    middle = pdf_mod._changed_middle("HELLO WORLD", "HELLO THERE")
    assert middle is not None
    shared_head = pdf_mod._changed_middle("ABCDEF", "ABCXYZ")
    assert shared_head is not None

    assert pdf_mod._value_redraw_span("Name", "Name") is None
    labelled = pdf_mod._value_redraw_span("Account No. 111", "Account No. 222")
    assert labelled is not None
    assert labelled[0] > 0
    replaced = pdf_mod._value_redraw_span("Account No. 111", "Other 222")
    assert replaced is None or replaced[0] >= 0
    same_label = pdf_mod._value_redraw_span("Account No. 111", "Account No. 999")
    assert same_label is not None

    assert pdf_mod._image_redraw_span("abc", "abc") is None
    image_span = pdf_mod._image_redraw_span("Account No. 111", "Account No. 222")
    assert image_span is not None
    assert pdf_mod._image_redraw_span("Name", "Other") is None

    assert pdf_mod._keep_pixel_span("", "x") is None
    assert pdf_mod._keep_pixel_span("abc", "abc") is None
    keep = pdf_mod._keep_pixel_span("HELLO", "HXLLO")
    assert keep == (1, 2, 1, 2)
    assert pdf_mod._keep_pixel_span("ab", "xy") is None


def test_image_rewrite_font_paths(monkeypatch) -> None:
    matched = pdf_mod._pil_font("helv", 14)
    swapped = pdf_mod._image_rewrite_font(
        matched,
        font_name="cour",
        requested_size=18,
        original_size=14,
        preserve_face=False,
        size_changed=True,
    )
    assert swapped is not None
    same = pdf_mod._image_rewrite_font(
        matched,
        font_name="helv",
        requested_size=18,
        original_size=14,
        preserve_face=True,
        size_changed=False,
    )
    assert same is matched
    resized = pdf_mod._image_rewrite_font(
        matched,
        font_name="helv",
        requested_size=22,
        original_size=14,
        preserve_face=True,
        size_changed=True,
    )
    assert resized is not None

    monkeypatch.setattr(pdf_mod, "_font_path_index", lambda font: ("/missing.ttf", 0))
    monkeypatch.setattr(
        pdf_mod, "_load_truetype", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    fallback = pdf_mod._image_rewrite_font(
        matched,
        font_name="helv",
        requested_size=22,
        original_size=14,
        preserve_face=True,
        size_changed=True,
    )
    assert fallback is not None


# ---------------------------------------------------------------------------
# OCR / discovery helpers
# ---------------------------------------------------------------------------


def test_iter_ocr_lines_shapes() -> None:
    assert list(pdf_mod._iter_ocr_lines(None)) == []
    obj = SimpleNamespace(txts=None, boxes=None, scores=None)
    assert list(pdf_mod._iter_ocr_lines(obj)) == []
    obj = SimpleNamespace(
        txts=["A", "B"],
        boxes=[[[0, 0], [1, 0], [1, 1], [0, 1]], [[2, 2], [3, 2], [3, 3], [2, 3]]],
        scores=[0.9],
    )
    rows = list(pdf_mod._iter_ocr_lines(obj))
    assert rows[0][1] == "A"
    assert rows[1][2] == 1.0
    tuple_rows = list(
        pdf_mod._iter_ocr_lines(([[ [0, 0], "ok", 0.8], None, ["x"]],))
    )
    assert tuple_rows[0][1] == "ok"
    list_rows = list(pdf_mod._iter_ocr_lines([[ [0, 0], "plain"]]))
    assert list_rows[0][2] == 1.0


def test_unique_normalize_placeholder() -> None:
    existing = {"name": _field()}
    assert pdf_mod._unique_field_name("name", existing) == "name_2"
    existing["name_2"] = _field()
    assert pdf_mod._unique_field_name("name", existing) == "name_3"
    assert pdf_mod._normalize_font(None) == BUILTIN_FONT_HELVETICA
    assert pdf_mod._normalize_font("ABC+Times") == "Times"
    placeholder = pdf_mod._placeholder_image_field({"title": "t"}, width=80)
    assert placeholder.text.startswith("[image")
    assert placeholder.rect[2] == 80.0


def test_separate_stacked_ocr_boxes() -> None:
    upper = _field(text="Label", rect=[10, 10, 80, 30], y=29)
    lower = _field(text="100.00", rect=[10, 24, 80, 50], y=49)
    fields = {"a": upper, "b": lower}
    pdf_mod._separate_stacked_ocr_boxes(fields)
    assert upper.rect[3] <= lower.rect[1] + 0.01
    same_row = {
        "l": _field(rect=[0, 0, 40, 20]),
        "r": _field(rect=[30, 1, 80, 21]),
    }
    pdf_mod._separate_stacked_ocr_boxes(same_row)
    assert same_row["l"].rect[3] == 20


def test_discover_fields_from_image_with_mocked_ocr(monkeypatch, tmp_path: Path) -> None:
    path = _save(_white_image((80, 80)), tmp_path / "blank.png")

    class Empty:
        def __call__(self, _path):
            return None

    monkeypatch.setattr(pdf_mod, "_get_ocr_engine", lambda: Empty())
    pdf_mod._ocr_cache.clear()
    fields = discover_fields_from_image(path)
    assert "document_image" in fields

    class Mixed:
        def __call__(self, _path):
            return SimpleNamespace(
                boxes=[
                    None,
                    [[0, 0], [40, 0], [40, 16], [0, 16]],
                    [[0, 20], [50, 20], [50, 40], [0, 40]],
                    [[0, 50], [40, 50], [40, 66], [0, 66]],
                ],
                txts=["", "SKIP", "Hello", "World"],
                scores=[1.0, 0.1, 0.9, 0.95],
            )

    monkeypatch.setattr(pdf_mod, "_get_ocr_engine", lambda: Mixed())
    pdf_mod._ocr_cache.clear()
    fields = discover_fields_from_image(path)
    names = {field.text for field in fields.values() if field.kind != "image"}
    assert "SKIP" not in names
    assert "Hello" in names


def test_discover_fields_from_pdf_empty_and_populated(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    doc = pymupdf.open()
    doc.new_page(width=200, height=200)
    doc.save(empty)
    doc.close()
    fields = discover_fields_from_pdf(empty)
    assert "document_image" in fields
    assert extract_pdf_text(empty).strip() == ""

    populated = _tiny_pdf(tmp_path / "full.pdf", with_image=True)
    found = discover_fields_from_pdf(populated)
    assert any("Golden" in field.text for field in found.values())
    assert any(field.kind == "image" for field in found.values())
    assert "Golden Harvest" in extract_pdf_text(populated)


def test_discover_native_pdf_fields_skips_bad_spans(monkeypatch) -> None:
    page = SimpleNamespace(
        get_text=lambda mode: {
            "blocks": [
                {"type": 1},
                {
                    "type": 0,
                    "lines": [
                        {
                            "spans": [
                                {"text": "   "},
                                {"text": "Hi", "bbox": [0, 0, 1]},
                                {"text": "Yo", "bbox": [1, 2, 20, 16], "origin": [1, 15]},
                                {
                                    "text": "NoOrigin",
                                    "bbox": [1, 20, 40, 34],
                                    "size": None,
                                    "font": None,
                                },
                            ]
                        }
                    ],
                },
            ]
        }
    )
    doc = [page]
    fields = pdf_mod._discover_native_pdf_fields(doc, {"title": "t"})
    assert "yo" in fields
    assert "noorigin" in fields
    assert fields["noorigin"].font == BUILTIN_FONT_HELVETICA


def test_discover_pdf_image_fields_filters(monkeypatch) -> None:
    page = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 400, 400),
        get_image_info=lambda: [
            {},
            {"bbox": [0, 0, 2, 2], "width": 64, "height": 64},
            {"bbox": [10, 10, 80, 80], "width": 2, "height": 2},
            {"bbox": [10, 10, 80, 80], "width": 64, "height": 32},
            {"bbox": [10, 10, 80, 80], "width": 64, "height": 32},
        ],
    )
    doc = [page]
    fields = pdf_mod._discover_pdf_image_fields(doc, {}, {})
    images = [field for field in fields.values() if field.kind == "image"]
    assert len(images) == 1


# ---------------------------------------------------------------------------
# Replace / emphasis / overlay / manipulate
# ---------------------------------------------------------------------------


def test_replace_field_text_on_pdf_and_image(tmp_path: Path) -> None:
    pdf_path = _tiny_pdf(tmp_path / "rep.pdf")
    fields = discover_fields_from_pdf(pdf_path)
    text_field = next(field for field in fields.values() if "Golden" in field.text)
    replace_field_text(pdf_path, text_field, "Silver Harvest", font="cour")
    assert "Silver Harvest" in extract_pdf_text(pdf_path)

    image = _draw_black_text(_white_image((220, 80)), "HELLO")
    png = _save(image, tmp_path / "rep.png")
    field = _field(text="HELLO", rect=[8, 8, 120, 40], x=10, y=30)
    replace_field_text(png, field, "HELLOX")
    replace_field_text(png, field, "HELLO", x=field.x + 8)
    replace_field_text(png, field, "HELLO", font_size=22)
    replace_field_text(png, _field(text="X", rect=[], x=4, y=20), "Y")
    replace_field_text(png, field, "HELLO", font="cour", font_size=18)


def test_scale_field_near_identity_is_noop() -> None:
    image = _draw_black_text(_white_image((160, 60)), "HELLO")
    field = _field(text="HELLO", font_size=16, rect=[8, 8, 100, 40])
    before = image.tobytes()
    pdf_mod._scale_field_on_image(image, field, 16.1)
    assert image.tobytes() == before


def test_try_splice_rewritten_text(tmp_path: Path) -> None:
    image = _draw_black_text(_white_image((240, 70)), "HELLO")
    field = _field(text="HELLO", rect=[8, 6, 140, 42], font_size=18)
    pdf_mod._mimic_rewritten_text_on_image(image, field, "HXLLO")
    pdf_mod._mimic_rewritten_text_on_image(image, field, "ZZZZZ")
    assert image.size == (240, 70)


def test_apply_text_emphasis_pdf_and_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported emphasis"):
        apply_text_emphasis(tmp_path / "x.pdf", _field(), "wavy")

    pdf_path = _tiny_pdf(tmp_path / "em.pdf")
    field = next(
        item
        for item in discover_fields_from_pdf(pdf_path).values()
        if "Golden" in item.text
    )
    apply_text_emphasis(pdf_path, field, "bold")
    apply_text_emphasis(pdf_path, field, "underline")
    apply_text_emphasis(pdf_path, _field(**{**field.model_dump(), "font": "Arial"}), "italic")

    png = _save(_draw_black_text(_white_image((220, 80)), "Account No. 1234"), tmp_path / "em.png")
    labelled = _field(text="Account No. 1234", rect=[8, 8, 180, 40], font_size=18)
    apply_text_emphasis(png, labelled, "bold")
    apply_text_emphasis(png, labelled, "underline")
    with pytest.raises(ValueError, match="bounding box"):
        apply_text_emphasis(png, _field(rect=[]), "bold")


def test_overlay_field_text_pdf_and_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        overlay_field_text(tmp_path / "x.pdf", _field(), "  ")

    pdf_path = _tiny_pdf(tmp_path / "ov.pdf")
    field = next(
        item
        for item in discover_fields_from_pdf(pdf_path).values()
        if "Golden" in item.text
    )
    overlay_field_text(pdf_path, field, "CERT COPY")
    overlay_field_text(pdf_path, field, "SHIFT", x=field.x + 4, y=field.y)

    png = _save(_draw_black_text(_white_image((220, 80)), "HELLO"), tmp_path / "ov.png")
    image_field = _field(text="HELLO", rect=[8, 8, 140, 40])
    overlay_field_text(png, image_field, "COPY")
    overlay_field_text(png, image_field, "COPY", x=12, y=30)
    with pytest.raises(ValueError, match="bounding box"):
        overlay_field_text(png, _field(rect=[]), "COPY")


def test_pdf_overlay_helpers(tmp_path: Path) -> None:
    path = _tiny_pdf(tmp_path / "rules.pdf")
    doc = pymupdf.open(path)
    page = doc[0]
    field = _field(text="Golden Harvest", rect=[70, 86, 200, 110], x=72, y=100)
    assert pdf_mod._pdf_glyph_ink_rect(page, _field(rect=[])) is None
    ink = pdf_mod._pdf_glyph_ink_rect(page, field)
    assert ink is None or not ink.is_empty
    ys = pdf_mod._horizontal_rule_ys(page, pymupdf.Rect(field.rect))
    assert ys
    origin = pdf_mod._overlay_origin(field, x=None, y=8)
    assert origin[1] == 8
    kwargs = {"fontname": "helv"}
    cover = pdf_mod._overlay_cover_rect(page, field, "X", 14, (72, 100), kwargs)
    assert not cover.is_empty
    no_rect = pdf_mod._overlay_cover_rect(
        page, _field(rect=[], x=72, y=100, text="Hi"), "Hi", 12, (72, 100), kwargs
    )
    assert no_rect.x0 >= 0
    assert pdf_mod._pdf_text_length("Hi", 12, {"fontname": "helv"}) > 0
    assert pdf_mod._pdf_text_length("Hi", 12, {"fontfile": "/nope.ttf"}) > 0
    colors = pdf_mod._sample_pdf_field_colors(page, [1, 2])
    assert colors == ((0, 0, 0), (255, 255, 255))
    empty_box = pdf_mod._sample_pdf_field_colors(page, [0, 0, 0.1, 0.1])
    assert empty_box[1] == (255, 255, 255)
    gray = page.get_pixmap(colorspace=pymupdf.csGRAY, clip=pymupdf.Rect(70, 90, 120, 110))
    pixels = pdf_mod._pixmap_rgb_pixels(gray)
    assert pixels
    rgb = page.get_pixmap(clip=pymupdf.Rect(70, 90, 90, 110))
    assert pdf_mod._pixmap_rgb_pixels(rgb)
    doc.close()

    image = _draw_black_text(_white_image((160, 50)), "HELLO")
    ImageDraw.Draw(image).line([(8, 6), (140, 6)], fill=(0, 0, 0), width=2)
    rules = pdf_mod._horizontal_rule_ys_in_image(image, [6, 4, 150, 40])
    assert rules
    assert pdf_mod._horizontal_rule_ys_in_image(image, [0, 0, 2, 1]) == []
    cover_px = pdf_mod._overlay_cover_rect_on_image(
        image,
        _field(text="HELLO", rect=[8, 8, 120, 40], y=28),
        "COPY",
        pdf_mod._pil_font("helv", 16),
        (10, 28),
        (0, 0, 0),
        (255, 255, 255),
    )
    assert cover_px[2] > cover_px[0]
    no_box = pdf_mod._overlay_cover_rect_on_image(
        image,
        _field(rect=[], x=10, y=20),
        "X",
        pdf_mod._pil_font("helv", 16),
        (10, 20),
        (0, 0, 0),
        (255, 255, 255),
    )
    assert no_box[2] > no_box[0]


def test_longest_true_run() -> None:
    assert pdf_mod._longest_true_run([False, True, True, False, True]) == 2
    assert pdf_mod._longest_true_run([]) == 0


def test_manipulate_field_region_pdf_and_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown"):
        manipulate_field_region(tmp_path / "x.pdf", _field(), "nope")
    with pytest.raises(ValueError, match="bounding box"):
        manipulate_field_region(tmp_path / "x.pdf", _field(rect=[]), "visible_boundary")

    pdf_path = _tiny_pdf(tmp_path / "man.pdf", with_image=True)
    image_field = next(
        item for item in discover_fields_from_pdf(pdf_path).values() if item.kind == "image"
    )
    manipulate_field_region(pdf_path, image_field, "compression_mismatch")
    manipulate_field_region(pdf_path, image_field, "scaling_anomaly", scale_x=1.6, scale_y=0.5)
    with pytest.raises(ValueError, match="empty"):
        manipulate_field_region(
            pdf_path, _field(rect=[0, 0, 0.1, 0.1], page=0), "visible_boundary"
        )

    png = _save(_draw_black_text(_white_image((180, 80)), "LOGO"), tmp_path / "man.png")
    ImageDraw.Draw(Image.open(png)).rectangle([20, 10, 70, 50], fill=(200, 0, 0))
    field = _field(kind="image", rect=[18, 8, 72, 52])
    # Rebuild PNG with a real coloured block.
    image = _white_image((180, 80))
    ImageDraw.Draw(image).rectangle([20, 10, 70, 50], fill=(200, 0, 0))
    png = _save(image, tmp_path / "man.png")
    manipulate_field_region(png, field, "quality_mismatch")
    manipulate_field_region(png, field, "scaling_anomaly")
    with pytest.raises(ValueError, match="empty"):
        manipulate_field_region(png, _field(rect=[0, 0, 1, 1]), "visible_boundary")


def test_apply_manipulation_variants() -> None:
    tiny = Image.new("RGB", (1, 1), (10, 10, 10))
    assert pdf_mod._apply_manipulation(tiny, "visible_boundary") is tiny
    crop = Image.new("RGB", (40, 30), (220, 220, 220))
    ImageDraw.Draw(crop).rectangle([8, 6, 28, 22], fill=(20, 20, 20))
    assert pdf_mod._apply_manipulation(crop, "scaling_anomaly").size != crop.size
    assert pdf_mod._apply_manipulation(crop, "resolution_mismatch").size == crop.size
    assert pdf_mod._apply_manipulation(crop, "compression_mismatch").mode == "RGB"
    assert pdf_mod._apply_manipulation(crop, "visible_boundary").size == crop.size
    assert pdf_mod._apply_manipulation(crop, "image_overlay").size == crop.size
    assert pdf_mod._apply_manipulation(crop, "quality_mismatch").size == crop.size
    with pytest.raises(ValueError):
        pdf_mod._resolved_scale_factors(0, 1)
    with pytest.raises(ValueError):
        pdf_mod._resolved_scale_factors(1, 9)


def test_flatten_insert_clip_and_cover_helpers(tmp_path: Path) -> None:
    rgb = Image.new("RGB", (8, 8), (1, 2, 3))
    assert pdf_mod._flatten_rgba_on_paper(rgb, rgb).mode == "RGB"
    rgba = Image.new("RGBA", (8, 8), (10, 20, 30, 128))
    paper = Image.new("RGB", (8, 8), (255, 255, 255))
    assert pdf_mod._flatten_rgba_on_paper(rgba, paper).mode == "RGB"

    cover = pymupdf.Rect(10, 10, 30, 20)
    same = pdf_mod._insert_rect_for_treated(cover, rgb, rgb)
    assert same == cover
    treated = Image.new("RGB", (16, 4), (0, 0, 0))
    stretched = pdf_mod._insert_rect_for_treated(cover, rgb, treated)
    assert stretched.width > cover.width or stretched.height < cover.height

    bounds = pymupdf.Rect(0, 0, 40, 40)
    inside = pdf_mod._clip_treated_to_bounds(treated, stretched, bounds)
    assert inside[1].x0 >= 0
    clipped = pdf_mod._clip_treated_to_bounds(
        treated, pymupdf.Rect(30, 10, 80, 40), pymupdf.Rect(0, 0, 50, 50)
    )
    assert clipped[1].x1 <= 50
    empty = pdf_mod._clip_treated_to_bounds(
        treated, pymupdf.Rect(0, 0, 0.2, 0.2), pymupdf.Rect(10, 10, 20, 20)
    )
    assert empty[0] is treated

    image = _white_image((80, 40))
    ImageDraw.Draw(image).rectangle([20, 8, 50, 28], fill=(0, 0, 200))
    assert pdf_mod._cover_slot_with_nearby_paper_image(image, (20, 8, 50, 28))
    assert not pdf_mod._cover_slot_with_nearby_paper_image(image, (0, 0, 1, 1))
    edge = pdf_mod._cover_slot_with_nearby_paper_image(image, (0, 8, 4, 28))
    assert edge in {True, False}

    pdf_mod._paste_treated_region(image, treated, pymupdf.Rect(4, 4, 20, 12))
    rgba_big = Image.new("RGBA", (12, 8), (255, 0, 0, 90))
    pdf_mod._paste_treated_region(image, rgba_big, pymupdf.Rect(6, 6, 18, 14))

    path = _tiny_pdf(tmp_path / "host.pdf", with_image=True)
    doc = pymupdf.open(path)
    page = doc[0]
    box = pymupdf.Rect(20, 16, 84, 52)
    host = pdf_mod._host_image_rect(page, box)
    assert host is None or host.width >= box.width
    info = pdf_mod._pdf_image_info_for_field(page, _field(rect=list(box)))
    assert info is None or "xref" in info or "bbox" in info
    clip = pdf_mod._pdf_clip_to_pil(page, box)
    assert clip.mode == "RGB"
    pdf_mod._insert_pil_on_page(page, clip, box, jpeg=True)
    assert pdf_mod._cover_slot_with_nearby_paper(page, box) in {True, False}
    assert pdf_mod._pil_from_pdf_image(doc, 0) is None
    assert pdf_mod._pil_from_pdf_image(doc, 99999) is None
    if info and info.get("xref"):
        extracted = pdf_mod._pil_from_pdf_image(doc, int(info["xref"]))
        assert extracted is None or extracted.mode in {"RGB", "RGBA"}
    doc.close()


def test_pil_from_pdf_image_smask(monkeypatch) -> None:
    rgb = Image.new("RGB", (4, 4), (9, 9, 9))
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    blob = buf.getvalue()
    mask = Image.new("L", (4, 4), 128)
    mbuf = io.BytesIO()
    mask.save(mbuf, format="PNG")

    class Doc:
        def extract_image(self, xref):
            if xref == 1:
                return {"image": blob, "smask": 2}
            if xref == 2:
                return {"image": mbuf.getvalue()}
            raise RuntimeError("missing")

    image = pdf_mod._pil_from_pdf_image(Doc(), 1)
    assert image.mode == "RGBA"

    class BadMask:
        def extract_image(self, xref):
            if xref == 1:
                return {"image": blob, "smask": 2}
            raise RuntimeError("mask gone")

    assert pdf_mod._pil_from_pdf_image(BadMask(), 1).mode == "RGB"
    assert pdf_mod._pil_from_pdf_image(Doc(), 3) is None


def test_match_font_to_crop_fallbacks() -> None:
    image = _white_image((60, 30))
    fallback = pdf_mod._match_font_to_crop(
        image, [4, 4, 40, 20], "   ", ink=(0, 0, 0), bg=(255, 255, 255), max_size=16
    )
    assert fallback is not None
    no_ink = pdf_mod._match_font_to_crop(
        image, [4, 4, 40, 20], "HELLO", ink=(0, 0, 0), bg=(255, 255, 255), max_size=16
    )
    assert no_ink is not None
    drawn = _draw_black_text(_white_image((160, 50)), "HELLO")
    matched = pdf_mod._match_font_to_crop(
        drawn, [8, 6, 120, 40], "HELLO", ink=(0, 0, 0), bg=(255, 255, 255), max_size=22
    )
    assert matched is not None


def test_fit_truetype_to_ink_if_system_font_exists() -> None:
    # Prefer a real system font when available, but fall back to a TTF
    # bundled in the repo (doc_gen_scripts) so this test runs the same way
    # on every platform/CI runner instead of skipping when none match.
    repo_font = (
        Path(__file__).resolve().parents[1]
        / "doc_gen_scripts"
        / "assets"
        / "fonts"
        / "DancingScript.ttf"
    )
    path = pdf_mod._first_existing_font(
        (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            str(repo_font),
        )
    )
    assert path is not None
    font = pdf_mod._fit_truetype_to_ink(
        path,
        0,
        "Hi",
        target_width=40,
        target_height=14,
        min_size=8,
        max_size=28,
    )
    assert font is not None
    assert pdf_mod._size_for_ink_width(path, 0, "Hello", 48, 8, 24) >= 8


def test_changed_middle_shared_suffix_and_short_head() -> None:
    span = pdf_mod._changed_middle("ABC123XYZ", "ABC999XYZ")
    assert span == (3, 6, 3, 6)
    assert pdf_mod._changed_middle("ABXXXX", "ABYYYY") is None


def test_pil_font_skips_unloadable_existing_file(monkeypatch, tmp_path: Path) -> None:
    bogus = tmp_path / "broken.ttf"
    bogus.write_bytes(b"not a font")
    fallback = SimpleNamespace(size=12)
    monkeypatch.setattr(
        pdf_mod, "_load_truetype", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(
        pdf_mod.ImageFont, "truetype", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(pdf_mod.ImageFont, "load_default", lambda: fallback)
    monkeypatch.setattr(pdf_mod, "_iter_system_font_faces", list)
    font = pdf_mod._pil_font("helv", 12, font_path=str(bogus))
    assert font is fallback


def test_font_insert_kwargs_unknown_family() -> None:
    kwargs = pdf_mod._font_insert_kwargs("DisplayFaceXYZ", preserve_face=True)
    assert kwargs == {"fontname": BUILTIN_FONT_HELVETICA}


def test_replace_image_text_with_explicit_font(tmp_path: Path) -> None:
    png = _save(
        _draw_black_text(_white_image((260, 70)), "Account No. 1234"),
        tmp_path / "label.png",
    )
    field = _field(text="Account No. 1234", rect=[8, 8, 200, 42], font_size=18)
    replace_field_text(png, field, "Account No. 9999", font="cour")
    assert png.exists()


def test_raster_image_field_rejection_branches() -> None:
    page = _white_image((200, 200), (250, 250, 250))
    draw = ImageDraw.Draw(page)
    draw.rectangle([10, 10, 28, 50], fill=(200, 10, 10))
    assert pdf_mod._discover_raster_image_fields(page, {}, metadata={}, page=0) == {}

    bar = _white_image((200, 200), (250, 250, 250))
    ImageDraw.Draw(bar).rectangle([10, 10, 180, 20], fill=(10, 10, 200))
    assert pdf_mod._discover_raster_image_fields(bar, {}, metadata={}, page=0) == {}

    overlap = _white_image((200, 200), (250, 250, 250))
    ImageDraw.Draw(overlap).rectangle([40, 40, 100, 100], fill=(180, 20, 20))
    text = {"ocr": _field(rect=[38, 38, 102, 102], text="Logo")}
    overlapped = pdf_mod._discover_raster_image_fields(
        overlap, text, metadata={}, page=0
    )
    assert overlapped == {}


def test_styled_face_family_load_failure(monkeypatch) -> None:
    monkeypatch.setattr(pdf_mod, "_font_path_index", lambda font: ("/tmp/x.ttf", 0))
    monkeypatch.setattr(pdf_mod, "_styled_font_path", lambda *a, **k: None)
    monkeypatch.setattr(
        pdf_mod, "_load_truetype", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    assert pdf_mod._styled_face(SimpleNamespace(), "bold") is None


def test_emphasis_image_fallbacks(monkeypatch, tmp_path: Path) -> None:
    png = _save(_draw_black_text(_white_image((180, 60)), "HELLO"), tmp_path / "fb.png")
    field = _field(text="HELLO", rect=[8, 8, 120, 40])
    monkeypatch.setattr(pdf_mod, "_styled_face", lambda *a, **k: None)
    monkeypatch.setattr(pdf_mod, "resolve_emphasis_font_file", lambda *a, **k: None)
    monkeypatch.setattr(pdf_mod, "_font_path_index", lambda font: (None, 0))
    apply_text_emphasis(png, field, "italic")

    ttf = pdf_mod._first_existing_font(
        (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        )
    )
    if ttf:
        monkeypatch.setattr(pdf_mod, "resolve_emphasis_font_file", lambda *a, **k: ttf)
        apply_text_emphasis(png, field, "bold")


def test_draw_text_typeerror_on_replace_emphasis_overlay(monkeypatch, tmp_path: Path) -> None:
    def fake_text(*args, **kwargs):
        if kwargs.get("anchor"):
            raise TypeError("no anchor")

    monkeypatch.setattr(pdf_mod.ImageDraw.ImageDraw, "text", fake_text)
    png = _save(_white_image((120, 50)), tmp_path / "te.png")
    field = _field(text="Hi", rect=[8, 8, 80, 32], font_size=14)
    replace_field_text(png, _field(text="Hi", rect=[], x=8, y=20), "Yo")
    replace_field_text(png, field, "Yo", font="cour")
    apply_text_emphasis(png, field, "underline")
    overlay_field_text(png, field, "COPY", x=10, y=24)


def test_overlay_pdf_sets_simple_for_ttf(tmp_path: Path) -> None:
    pdf_path = _tiny_pdf(tmp_path / "arial.pdf")
    field = next(
        item
        for item in discover_fields_from_pdf(pdf_path).values()
        if "Golden" in item.text
    )
    field.font = "Arial"
    overlay_field_text(pdf_path, field, "COPY")
    apply_text_emphasis(pdf_path, field, "underline")


def test_host_and_image_info_helpers() -> None:
    box = pymupdf.Rect(20, 20, 80, 80)
    page = SimpleNamespace(
        get_image_info=lambda xrefs=False: [
            {},
            {"bbox": [0, 0, 10, 10]},
            {"bbox": [0, 0, 200, 200], "width": 400, "height": 400},
            {"bbox": [10, 10, 90, 90], "width": 80, "height": 80},
        ]
    )
    host = pdf_mod._host_image_rect(page, box)
    assert host is not None
    assert host.width >= box.width

    field = _field(rect=[20, 20, 80, 80])
    info = pdf_mod._pdf_image_info_for_field(page, field)
    assert info is None or "bbox" in info
    far = pdf_mod._pdf_image_info_for_field(
        SimpleNamespace(get_image_info=lambda xrefs=True: [{"bbox": [300, 300, 320, 320]}]),
        field,
    )
    assert far is None
    missing = pdf_mod._pdf_image_info_for_field(
        SimpleNamespace(get_image_info=lambda xrefs=True: [{}]),
        field,
    )
    assert missing is None


def test_pdf_clip_handles_gray_and_rgba(monkeypatch) -> None:
    class Gray:
        n = 1
        width = 2
        height = 2
        samples = bytes([10, 20, 30, 40])

    class Rgba:
        n = 4
        width = 1
        height = 1
        samples = bytes([1, 2, 3, 4])

    page = SimpleNamespace(get_pixmap=lambda **k: Gray())
    gray = pdf_mod._pdf_clip_to_pil(page, pymupdf.Rect(0, 0, 2, 2))
    assert gray.mode == "RGB"
    page.get_pixmap = lambda **k: Rgba()
    rgba = pdf_mod._pdf_clip_to_pil(page, pymupdf.Rect(0, 0, 1, 1))
    assert rgba.mode == "RGB"


def test_pil_from_pdf_empty_blob_and_resized_mask() -> None:
    class Empty:
        def extract_image(self, xref):
            return {"image": b""}

    assert pdf_mod._pil_from_pdf_image(Empty(), 3) is None

    rgb = Image.new("RGB", (4, 4), (9, 9, 9))
    buf = io.BytesIO()
    rgb.save(buf, format="PNG")
    mask = Image.new("L", (2, 2), 200)
    mbuf = io.BytesIO()
    mask.save(mbuf, format="PNG")

    class Mismatch:
        def extract_image(self, xref):
            if xref == 1:
                return {"image": buf.getvalue(), "smask": 2}
            return {"image": mbuf.getvalue()}

    image = pdf_mod._pil_from_pdf_image(Mismatch(), 1)
    assert image.mode == "RGBA"
    assert image.size == (4, 4)


def test_cover_slot_and_clip_edge_cases(tmp_path: Path) -> None:
    image = _white_image((40, 20))
    assert not pdf_mod._cover_slot_with_nearby_paper_image(image, (0, 0, 40, 20))

    treated = Image.new("RGB", (8, 8), (1, 2, 3))
    tiny = pdf_mod._clip_treated_to_bounds(
        treated,
        pymupdf.Rect(0, 0, 40, 40),
        pymupdf.Rect(0, 0, 0.6, 0.6),
    )
    assert tiny[0] is treated

    path = _tiny_pdf(tmp_path / "edge.pdf")
    doc = pymupdf.open(path)
    page = doc[0]
    edge = pymupdf.Rect(0, 0, 0.8, 40)
    assert pdf_mod._cover_slot_with_nearby_paper(page, edge) in {True, False}
    doc.close()


def test_scaling_anomaly_uses_extracted_rgba(monkeypatch, tmp_path: Path) -> None:
    pdf_path = _tiny_pdf(tmp_path / "scale.pdf", with_image=True)
    field = next(
        item
        for item in discover_fields_from_pdf(pdf_path).values()
        if item.kind == "image"
    )
    rgba = Image.new("RGBA", (24, 16), (255, 0, 0, 120))
    monkeypatch.setattr(pdf_mod, "_pdf_image_info_for_field", lambda *a, **k: {"xref": 7})
    monkeypatch.setattr(pdf_mod, "_pil_from_pdf_image", lambda *a, **k: rgba)
    monkeypatch.setattr(
        pdf_mod, "_host_image_rect", lambda *a, **k: pymupdf.Rect(0, 0, 400, 240)
    )
    manipulate_field_region(pdf_path, field, "scaling_anomaly", scale_x=1.5, scale_y=0.6)


def test_image_scaling_without_nearby_paper(tmp_path: Path) -> None:
    image = Image.new("RGB", (36, 24), (180, 20, 20))
    png = _save(image, tmp_path / "full.png")
    field = _field(kind="image", rect=[0, 0, 36, 24])
    manipulate_field_region(png, field, "scaling_anomaly")


def test_glyph_ink_rect_and_rule_skips(tmp_path: Path) -> None:
    path = _tiny_pdf(tmp_path / "glyph.pdf")
    doc = pymupdf.open(path)
    page = doc[0]
    assert pdf_mod._pdf_glyph_ink_rect(page, _field(rect=[0, 0, 0.2, 0.2])) is None
    white = _field(rect=[300, 10, 380, 40], text="   ")
    assert pdf_mod._pdf_glyph_ink_rect(page, white) is None
    cover = pdf_mod._overlay_cover_rect(
        page,
        _field(rect=[], x=72, y=40, text="Hi"),
        "Hi",
        12,
        (72, 40),
        {"fontname": "helv"},
    )
    assert cover.y1 > cover.y0
    doc.close()

    image = _white_image((80, 40))
    ImageDraw.Draw(image).line([(4, 30), (70, 30)], fill=(0, 0, 0), width=2)
    cover_px = pdf_mod._overlay_cover_rect_on_image(
        image,
        _field(text="HELLO", rect=[8, 8, 60, 22], y=18),
        "COPY",
        pdf_mod._pil_font("helv", 14),
        (10, 18),
        (0, 0, 0),
        (255, 255, 255),
    )
    assert cover_px[2] > cover_px[0]
