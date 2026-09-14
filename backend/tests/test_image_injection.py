from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.injection import pdf as injection_pdf
from app.injection.field_text import is_label_only
from app.injection.pdf import (
    MANIPULATION_INDICATORS,
    discover_fields_from_image,
    manipulate_field_region,
    overlay_field_text,
    read_document_metadata,
    replace_field_text,
    resolve_emphasis_font_file,
    _image_redraw_span,
    _match_font_to_crop,
    _sample_bg_color,
    _save_raster_image,
    _value_redraw_span,
)
from app.injection.schemas import DefectType, FieldValue
from app.injection.store import get_document, get_document_file_path, reset_sample_documents
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_documents(isolated_injection_templates) -> None:
    reset_sample_documents()


def _inject(document_id: str, field_name: str | None, defect_type: DefectType, **extra):
    payload: dict = {
        "document_id": document_id,
        "defect_type": defect_type.value,
    }
    if field_name is not None:
        payload["field_name"] = field_name
    defaults: dict = {
        DefectType.FONT: {"font": "cour"},
        DefectType.FONT_SIZE: {"font_size": 16.0},
        DefectType.NUMERICAL: {"new_text": "100001"},
        DefectType.SPELLING: {"new_text": "OCBCX"},
        DefectType.METADATA: {"metadata": {"author": "Reviewer"}},
        DefectType.MISALIGNMENT: {"x": 90.0},
        DefectType.EMPHASIS: {"style": "italic"},
        DefectType.TEXT_OVERLAY: {"new_text": "JANE CITIZEN"},
        DefectType.IMAGE_MANIPULATION: {"indicator": "compression_mismatch"},
    }
    payload.update(defaults[defect_type])
    payload.update(extra)
    return client.post("/api/dev/inject", json=payload)


def test_image_templates_keep_original_format() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    assert document.filename.endswith(".png")
    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.exists()
    assert path.suffix.lower() == ".png"
    assert not path.read_bytes().startswith(b"%PDF")


def test_image_document_id_accepts_extension() -> None:
    with_ext = get_document("ocbc_sample.png")
    without_ext = get_document("ocbc_sample")
    assert with_ext is not None
    assert without_ext is not None
    assert with_ext.id == without_ext.id == "ocbc_sample"
    assert with_ext.filename == "ocbc_sample.png"


def test_ocr_discovers_text_on_png() -> None:
    fields = discover_fields_from_image(injection_pdf.TEMPLATES_DIR / "ocbc_sample.png")
    assert len(fields) > 5
    joined = " ".join(field.text for field in fields.values()).upper()
    assert "OCBC" in joined
    images = [field for field in fields.values() if field.kind == "image"]
    assert images, "sample PNG should expose a pictorial logo region"
    logo = images[0]
    assert logo.rect[2] - logo.rect[0] >= 80
    assert logo.rect[3] - logo.rect[1] >= 40


def test_png_discovers_synthetic_logo_region(tmp_path: Path) -> None:
    path = tmp_path / "logo_page.png"
    image = Image.new("RGB", (400, 220), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((20, 24), "HELLO", fill=(20, 20, 20))
    draw.rectangle([260, 40, 370, 150], fill=(210, 20, 20))
    image.save(path)
    fields = discover_fields_from_image(path)
    images = [field for field in fields.values() if field.kind == "image"]
    assert len(images) == 1
    box = images[0].rect
    assert box[0] < 270
    assert box[2] > 360
    assert box[1] < 50
    assert box[3] > 140


def test_png_image_manipulation_rejects_text_fields() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    text_name = next(
        name
        for name, field in document.fields.items()
        if field.kind != "image" and field.text.strip()
    )
    response = _inject(
        "ocbc_sample",
        text_name,
        DefectType.IMAGE_MANIPULATION,
        indicator="compression_mismatch",
    )
    assert response.status_code == 400
    detail = response.json()["detail"].lower()
    assert "image" in detail


def test_can_inject_into_png_and_stay_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None

    numeric = next(
        (
            name
            for name, field in document.fields.items()
            if field.kind != "image" and any(ch.isdigit() for ch in field.text)
        ),
        None,
    )
    field_name = numeric or next(iter(document.fields))
    defect = DefectType.NUMERICAL if numeric else DefectType.SPELLING

    response = _inject("ocbc_sample.png", field_name, defect)

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == "ocbc_sample"
    assert payload["filename"] == "ocbc_sample.png"
    assert payload["field_name"] == field_name
    assert payload["defect_count"] == 1

    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.suffix.lower() == ".png"
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_can_inject_metadata_into_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(iter(document.fields))
    before_meta = read_document_metadata(Path(get_document_file_path("ocbc_sample") or ""))

    response = _inject("ocbc_sample.png", field_name, DefectType.METADATA)

    assert response.status_code == 200
    after_meta = read_document_metadata(Path(get_document_file_path("ocbc_sample") or ""))
    assert after_meta != before_meta


def test_fields_endpoint_works_for_image() -> None:
    response = client.get("/api/dev/documents/ocbc_sample.png/fields")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) >= 1
    assert all(item["source"] == "ocr" for item in payload)
    assert all(item["field_name"] and item["text"] for item in payload)


def test_png_fields_include_recognised_names_and_text() -> None:
    response = client.get("/api/dev/documents/ocbc_sample.png/fields")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 5
    assert all(item["source"] == "ocr" for item in payload)

    by_name = {item["field_name"]: item["text"] for item in payload}
    assert "ocbc" in by_name
    assert "OCBC" in by_name["ocbc"].upper()
    joined = " ".join(by_name.values()).upper()
    assert "STATEMENT" in joined or "OCBC" in joined


def test_png_spelling_keeps_combined_label_prefix() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    mixed = next(
        (
            name
            for name, field in document.fields.items()
            if field.text.startswith("Account No.")
        ),
        None,
    )
    assert mixed is not None

    response = _inject("ocbc_sample.png", mixed, DefectType.SPELLING, new_text="12345679")

    assert response.status_code == 200
    payload = response.json()
    assert payload["before_text"].startswith("Account No.")
    assert payload["after_text"].startswith("Account No.")
    assert payload["after_text"] != payload["before_text"]


def test_png_spelling_accepts_explicit_replacement() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    mixed = next(
        (
            name
            for name, field in document.fields.items()
            if field.text.startswith("Account No.")
        ),
        None,
    )
    assert mixed is not None

    response = _inject(
        "ocbc_sample.png",
        mixed,
        DefectType.SPELLING,
        new_text="XXYX-0472",
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["after_text"].startswith("Account No.")
    assert payload["after_text"].endswith("XXYX-0472")


def test_png_rejects_label_only_spelling() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    label = next(
        (name for name, field in document.fields.items() if is_label_only(field.text)),
        None,
    )
    assert label is not None

    response = _inject("ocbc_sample.png", label, DefectType.SPELLING)

    assert response.status_code == 400
    assert "label" in response.json()["detail"].lower()


def test_image_bg_sample_uses_halo_on_tinted_fill() -> None:
    image = Image.new("RGB", (220, 90), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([8, 8, 212, 82], fill=(200, 210, 220))
    draw.text((28, 32), "1000.00", fill=(15, 15, 15))
    bg = _sample_bg_color(image, [28.0, 30.0, 120.0, 58.0])
    assert bg[0] < 240
    assert abs(bg[0] - 200) < 25
    assert abs(bg[2] - 220) < 25


def test_png_emphasis_italic_resolves_a_ttf() -> None:
    path = resolve_emphasis_font_file("italic", "helv", prefer_ttf=True)
    assert path is not None
    assert Path(path).exists()
    lowered = Path(path).name.lower()
    assert "italic" in lowered or "oblique" in lowered


def test_png_numerical_keeps_tinted_background(isolated_injection_templates: Path) -> None:
    font_path = next(
        (
            candidate
            for candidate in (
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            )
            if Path(candidate).exists()
        ),
        None,
    )
    path = isolated_injection_templates / "tinted_amount.png"
    image = Image.new("RGB", (420, 140), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([12, 12, 408, 128], fill=(198, 208, 218))
    if font_path:
        font = ImageFont.truetype(font_path, 32)
        draw.text((40, 50), "1000.00", fill=(20, 20, 20), font=font)
    else:
        draw.text((40, 50), "1000.00", fill=(20, 20, 20))
    image.save(path)
    reset_sample_documents()

    document = get_document("tinted_amount")
    assert document is not None
    numeric = next(
        (name for name, field in document.fields.items() if any(ch.isdigit() for ch in field.text) and field.kind != "image"),
        None,
    )
    assert numeric is not None

    response = _inject("tinted_amount", numeric, DefectType.NUMERICAL)
    assert response.status_code == 200

    after = Image.open(Path(get_document_file_path("tinted_amount") or "")).convert("RGB")
    field = document.fields[numeric]
    bg = _sample_bg_color(after, field.rect)
    assert bg[0] < 245
    assert abs(bg[0] - 198) < 40
    assert abs(bg[2] - 218) < 40


def _first_existing_ttf(*candidates: str) -> str | None:
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def _draw_amount(path: Path, text: str, font_path: str, size: int = 28) -> list[float]:
    font = ImageFont.truetype(font_path, size)
    image = Image.new("RGB", (420, 90), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin = (24, 62)
    draw.text(origin, text, fill=(18, 18, 18), font=font, anchor="ls")
    try:
        bbox = font.getbbox(text, anchor="ls")
    except TypeError:
        bbox = font.getbbox(text)
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    _save_raster_image(image, path)
    return rect


def test_png_font_match_does_not_pick_courier_for_sans_amount(tmp_path: Path) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    text = "2,843.75"
    path = tmp_path / "sans_amount.png"
    rect = _draw_amount(path, text, sans, size=28)
    image = Image.open(path)
    matched = _match_font_to_crop(
        image,
        rect,
        text,
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=28,
    )
    name = Path(getattr(matched, "path", "") or "").name.lower()
    assert "courier" not in name
    assert "mono" not in name
    assert "times" not in name
    assert "serif" not in name


def test_png_font_match_keeps_courier_when_source_is_mono(tmp_path: Path) -> None:
    mono = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    )
    if mono is None:
        pytest.skip("no monospace TTF available")

    text = "2,843.75"
    path = tmp_path / "mono_amount.png"
    rect = _draw_amount(path, text, mono, size=28)
    image = Image.open(path)
    matched = _match_font_to_crop(
        image,
        rect,
        text,
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=28,
    )
    name = Path(getattr(matched, "path", "") or "").name.lower()
    assert "courier" in name or "mono" in name


def test_png_numerical_rewrite_stays_sans_not_courier(tmp_path: Path) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    text = "2,843.75"
    path = tmp_path / "rewrite_amount.png"
    rect = _draw_amount(path, text, sans, size=28)
    field = FieldValue(
        text=text,
        font="helv",
        font_size=28.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, "3,843.75")

    after = Image.open(path)
    rematched = _match_font_to_crop(
        after,
        rect,
        "3,843.75",
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=28,
    )
    name = Path(getattr(rematched, "path", "") or "").name.lower()
    assert "courier" not in name
    assert "mono" not in name
    assert "times" not in name
    assert "serif" not in name


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_keeps_bold_sans_not_serif(tmp_path: Path, suffix: str) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    path = tmp_path / f"overlay_amount{suffix}"
    rect = _draw_amount(path, "$187.41", sans, size=36)
    before = Image.open(path).convert("L")
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    source_h = _ink_height(before.crop((bx0, by0, bx1, by1)))
    field = FieldValue(
        text="$187.41",
        font="helv",
        font_size=36.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "$287.41")
    after = Image.open(path)
    rematched = _match_font_to_crop(
        after,
        [
            rect[0] - 8,
            rect[1] - 8,
            rect[2] + 40,
            rect[3] + 8,
        ],
        "$287.41",
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=36,
    )
    name = Path(getattr(rematched, "path", "") or "").name.lower()
    style = (getattr(getattr(rematched, "font", None), "style", "") or "").lower()
    family = (getattr(getattr(rematched, "font", None), "family", "") or "").lower()
    blob = f"{name} {style} {family}"
    assert "times" not in blob
    assert "serif" not in blob.replace("sans-serif", "sans").replace("sans serif", "sans")
    assert "courier" not in blob
    assert "mono" not in blob
    overlay_h = _ink_height(
        after.convert("L").crop((max(0, bx0 - 4), max(0, by0 - 4), bx1 + 48, by1 + 4))
    )
    assert source_h > 0 and overlay_h > 0
    assert abs(overlay_h - source_h) / source_h < 0.18


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_matches_smaller_label_size(tmp_path: Path, suffix: str) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    path = tmp_path / f"overlay_date{suffix}"
    rect = _draw_amount(path, "15 Apr 2026", sans, size=14)
    before = Image.open(path).convert("L")
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    source_h = _ink_height(before.crop((bx0, by0, bx1, by1)))
    field = FieldValue(
        text="15 Apr 2026",
        font="helv",
        font_size=14.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "15 Apr 3026")
    after = Image.open(path).convert("L")
    overlay_h = _ink_height(after.crop((max(0, bx0 - 4), max(0, by0 - 4), bx1 + 8, by1 + 4)))
    assert source_h > 0 and overlay_h > 0
    assert abs(overlay_h - source_h) / source_h < 0.22


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_does_not_paste_donor_glyphs_from_elsewhere(
    tmp_path: Path, suffix: str
) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    font = ImageFont.truetype(sans, 36)
    path = tmp_path / f"copy_digit{suffix}"
    image = Image.new("RGB", (420, 180), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((24, 62), "$187.41", fill=(18, 18, 18), font=font, anchor="ls")
    draw.text((24, 130), "200.00", fill=(210, 20, 20), font=font, anchor="ls")
    try:
        bbox = font.getbbox("$187.41", anchor="ls")
    except TypeError:
        bbox = font.getbbox("$187.41")
    rect = [
        float(24 + bbox[0] - 2),
        float(62 + bbox[1] - 2),
        float(24 + bbox[2] + 2),
        float(62 + bbox[3] + 2),
    ]
    _save_raster_image(image, path)
    field = FieldValue(
        text="$187.41",
        font="helv",
        font_size=36.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "$287.41")
    after = Image.open(path).convert("RGB")
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    crop = after.crop((max(0, x0), max(0, y0), min(after.width, x1), min(after.height, y1)))
    reds = [
        pixel
        for pixel in crop.getdata()
        if pixel[0] > 160 and pixel[1] < 80 and pixel[2] < 80
    ]
    assert not reds, "overlay should mimic this field, not paste a glyph from elsewhere"


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_keeps_unchanged_prefix_when_rewriting_a_word(
    tmp_path: Path,
    suffix: str,
) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    font = ImageFont.truetype(sans, 22)
    path = tmp_path / f"charges{suffix}"
    image = Image.new("RGB", (520, 180), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    heading = "SUMMARY OF CHARGES"
    draw.text((20, 50), heading, fill=(18, 18, 18), font=font, anchor="ls")
    draw.text((20, 110), "CONSUMPTION TREND EXTRA", fill=(18, 18, 18), font=font, anchor="ls")
    try:
        bbox = font.getbbox(heading, anchor="ls")
        prefix_bbox = font.getbbox("SUMMARY OF ", anchor="ls")
    except TypeError:
        bbox = font.getbbox(heading)
        prefix_bbox = font.getbbox("SUMMARY OF ")
    rect = [
        float(20 + bbox[0] - 2),
        float(50 + bbox[1] - 2),
        float(20 + bbox[2] + 2),
        float(50 + bbox[3] + 2),
    ]
    prefix_box = (
        max(0, int(20 + prefix_bbox[0])),
        max(0, int(50 + prefix_bbox[1])),
        int(20 + prefix_bbox[2]),
        int(50 + prefix_bbox[3]),
    )
    _save_raster_image(image, path)
    field = FieldValue(
        text=heading,
        font="helv",
        font_size=22.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "SUMMARY OF EXPENSES")
    after = Image.open(path).convert("RGB")
    rematched = _match_font_to_crop(
        after,
        [
            rect[0] - 4,
            rect[1] - 4,
            rect[2] + 40,
            rect[3] + 4,
        ],
        "SUMMARY OF EXPENSES",
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=22,
    )
    blob = Path(getattr(rematched, "path", "") or "").name.lower()
    assert "courier" not in blob
    assert "times" not in blob
    suffix = after.crop(
        (
            prefix_box[2],
            max(0, int(rect[1])),
            min(after.width, int(rect[2]) + 40),
            min(after.height, int(rect[3])),
        )
    )
    assert any(pixel[0] < 80 for pixel in suffix.getdata())


def test_png_font_match_keeps_times_when_source_is_serif(tmp_path: Path) -> None:
    serif = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    )
    if serif is None:
        pytest.skip("no serif TTF available")

    text = "2,843.75"
    path = tmp_path / "serif_amount.png"
    rect = _draw_amount(path, text, serif, size=28)
    image = Image.open(path)
    matched = _match_font_to_crop(
        image,
        rect,
        text,
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=28,
    )
    name = Path(getattr(matched, "path", "") or "").name.lower()
    family = (getattr(getattr(matched, "font", None), "family", "") or "").lower()
    blob = f"{name} {family}"
    assert "times" in blob or "serif" in blob
    assert "courier" not in blob
    assert "mono" not in blob


def test_image_spelling_span_redraws_whole_value_not_two_letters() -> None:
    old = "SINGAPORE RESIDENCES"
    new = "SINGAPROE RESIDENCES"
    tiny = _value_redraw_span(old, new)
    assert tiny is not None
    assert tiny[1] - tiny[0] <= 3
    assert _image_redraw_span(old, new) is None

    labelled = _image_redraw_span("Account No. XXXX-0472", "Account No. XXYX-0472")
    assert labelled is not None
    assert labelled[0] == len("Account No. ")
    assert labelled[2] == len("Account No. ")


def test_png_spelling_middle_letter_keeps_one_sans_face(tmp_path: Path) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    text = "SINGAPORE RESIDENCES"
    font = ImageFont.truetype(sans, 22)
    image = Image.new("RGB", (520, 90), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin = (16, 58)
    draw.text(origin, text, fill=(18, 18, 18), font=font, anchor="ls")
    try:
        bbox = font.getbbox(text, anchor="ls")
    except TypeError:
        bbox = font.getbbox(text)
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    path = tmp_path / "spelling_line.png"
    image.save(path)
    field = FieldValue(
        text=text,
        font="helv",
        font_size=22.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, "SINGAPROE RESIDENCES")

    after = Image.open(path)
    rematched = _match_font_to_crop(
        after,
        rect,
        "SINGAPROE RESIDENCES",
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=22,
    )
    name = Path(getattr(rematched, "path", "") or "").name.lower()
    assert "courier" not in name
    assert "mono" not in name
    assert "times" not in name
    assert "serif" not in name


def test_png_spelling_same_length_keeps_glyph_size(tmp_path: Path) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    before_text = "TAN WEI MING"
    after_text = "TAN WEI NING"
    path = tmp_path / "name_spelling.png"
    font = ImageFont.truetype(sans, 22)
    image = Image.new("RGB", (420, 90), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin = (24, 62)
    draw.text(origin, before_text, fill=(18, 18, 18), font=font, anchor="ls")
    try:
        bbox = font.getbbox(before_text, anchor="ls")
    except TypeError:
        bbox = font.getbbox(before_text)
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    image.save(path)
    before = Image.open(path).convert("L")
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    before_h = _ink_height(before.crop((bx0, by0, bx1, by1)))
    before_w = _ink_width(before.crop((bx0, by0, bx1, by1)))

    field = FieldValue(
        text=before_text,
        font="helv",
        font_size=22.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, after_text)

    after = Image.open(path).convert("L")
    pad = 16
    crop = after.crop(
        (
            max(0, bx0 - pad),
            max(0, by0 - pad),
            min(after.width, bx1 + pad + 40),
            min(after.height, by1 + pad),
        )
    )
    after_h = _ink_height(crop)
    after_w = _ink_width(crop)
    assert after_h >= before_h * 0.85, (before_h, after_h)
    assert after_w >= before_w * 0.85, (before_w, after_w)


def test_png_font_size_increase_makes_glyphs_taller(tmp_path: Path) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")

    text = "ACCOUNT"
    path = tmp_path / "size_account.png"
    rect = _draw_amount(path, text, sans, size=18)
    before = Image.open(path).convert("L")
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    before_h = _ink_height(before.crop((bx0, by0, bx1, by1)))

    field = FieldValue(
        text=text,
        font="helv",
        font_size=18.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, text, font_size=26.0)

    after = Image.open(path).convert("L")
    pad = 12
    after_h = _ink_height(
        after.crop(
            (
                max(0, bx0 - pad),
                max(0, by0 - pad),
                min(after.width, bx1 + pad),
                min(after.height, by1 + pad),
            )
        )
    )
    assert after_h > before_h * 1.15, (before_h, after_h)


def test_png_font_size_increase_keeps_source_face(tmp_path: Path) -> None:
    bold = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Tahoma Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    if bold is None:
        pytest.skip("no bold sans TTF available")

    text = "ACCOUNT"
    path = tmp_path / "size_face.png"
    rect = _draw_amount(path, text, bold, size=18)
    field = FieldValue(
        text=text,
        font="helv",
        font_size=18.0,
        x=rect[0],
        y=rect[3] - 1.0,
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, text, font_size=26.0)

    after = Image.open(path)
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    pad = 16
    matched = _match_font_to_crop(
        after,
        [
            max(0, bx0 - pad),
            max(0, by0 - pad),
            min(after.width, bx1 + pad),
            min(after.height, by1 + pad),
        ],
        text,
        ink=(18, 18, 18),
        bg=(255, 255, 255),
        max_size=26,
    )
    name = Path(getattr(matched, "path", "") or "").name.lower()
    style = (getattr(getattr(matched, "font", None), "style", "") or "").lower()
    assert "bold" in name or style == "bold", (name, style)


def _ink_height(gray: Image.Image) -> int:
    pixels = list(gray.getdata())
    width, _height = gray.size
    ys = [i // width for i, value in enumerate(pixels) if value < 80]
    if not ys:
        return 0
    return max(ys) - min(ys) + 1


def _ink_width(gray: Image.Image) -> int:
    pixels = list(gray.getdata())
    width, _height = gray.size
    xs = [i % width for i, value in enumerate(pixels) if value < 80]
    if not xs:
        return 0
    return max(xs) - min(xs) + 1


def test_can_overlay_text_on_png_and_stay_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(iter(document.fields))
    before = get_document("ocbc_sample")
    assert before is not None
    original = before.fields[field_name].text
    response = _inject(
        "ocbc_sample",
        field_name,
        DefectType.TEXT_OVERLAY,
        new_text="JANE CITIZEN",
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["before_text"] == payload["after_text"] == original
    assert "JANE CITIZEN" in payload["description"]
    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.suffix.lower() == ".png"
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("stem,suffix", [("ocbc_jpg", ".jpg"), ("ocbc_jpeg", ".jpeg")])
def test_can_overlay_text_on_jpeg_and_stay_jpeg(
    isolated_injection_templates: Path, stem: str, suffix: str
) -> None:
    source = isolated_injection_templates / "ocbc_sample.png"
    dest = isolated_injection_templates / f"{stem}{suffix}"
    _save_raster_image(Image.open(source).convert("RGB"), dest)
    reset_sample_documents()

    document = get_document(stem)
    assert document is not None
    field_name = next(iter(document.fields))
    original = document.fields[field_name].text
    response = _inject(
        stem,
        field_name,
        DefectType.TEXT_OVERLAY,
        new_text="JANE CITIZEN",
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["before_text"] == payload["after_text"] == original
    assert payload["filename"] == f"{stem}{suffix}"
    assert "JANE CITIZEN" in payload["description"]
    path = Path(get_document_file_path(stem) or "")
    assert path.suffix.lower() == suffix
    assert path.read_bytes()[:3] == b"\xff\xd8\xff"


def test_jpeg_font_fontsize_numerical_spelling_misalignment_emphasis(
    isolated_injection_templates: Path,
) -> None:
    source = isolated_injection_templates / "ocbc_sample.png"
    dest = isolated_injection_templates / "ocbc_jpg.jpg"
    _save_raster_image(Image.open(source).convert("RGB"), dest)
    reset_sample_documents()

    document = get_document("ocbc_jpg")
    assert document is not None
    injectable = [
        (name, field)
        for name, field in document.fields.items()
        if field.text.strip() and field.kind != "image" and not is_label_only(field.text)
    ]
    numeric = next(
        (
            name
            for name, field in injectable
            if any(ch.isdigit() for ch in field.text)
        ),
        None,
    )
    assert numeric is not None
    text_name = next(
        (
            name
            for name, field in injectable
            if name != numeric and not any(ch.isdigit() for ch in field.text)
        ),
        numeric,
    )
    text_field = document.fields[text_name]
    num_field = document.fields[numeric]
    spelling = text_field.text + "X" if not text_field.text.endswith("X") else text_field.text + "Z"
    numerical = "100001" if num_field.text != "100001" else "100002"
    cases = [
        (text_name, DefectType.FONT, {"font": "cour"}),
        (text_name, DefectType.FONT_SIZE, {"font_size": round(text_field.font_size + 8.0, 1)}),
        (numeric, DefectType.NUMERICAL, {"new_text": numerical}),
        (text_name, DefectType.SPELLING, {"new_text": spelling}),
        (text_name, DefectType.MISALIGNMENT, {"x": text_field.x + 24.0}),
        (text_name, DefectType.EMPHASIS, {"style": "italic"}),
    ]
    for field_name, defect, extra in cases:
        response = _inject("ocbc_jpg", field_name, defect, **extra)
        assert response.status_code == 200, (defect.value, response.json())
        payload = response.json()
        assert payload["filename"] == "ocbc_jpg.jpg"
        assert payload["generated_filename"].endswith(".jpg")
        generated = isolated_injection_templates.parent / "documents_generated" / payload["generated_filename"]
        assert generated.exists()
        assert generated.read_bytes()[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_matches_field_background_and_origin(tmp_path: Path, suffix: str) -> None:
    font_path = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    if font_path is None:
        pytest.skip("no sans TTF available")
    fill = (209, 230, 245)
    path = tmp_path / f"overlay{suffix}"
    image = Image.new("RGB", (400, 200), fill)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 14)
    origin = (72.0, 100.0)
    draw.text(origin, "S8000000A", fill=(20, 20, 20), font=font, anchor="ls")
    try:
        bbox = font.getbbox("S8000000A", anchor="ls")
    except TypeError:
        bbox = font.getbbox("S8000000A")
    rect = [
        origin[0] + bbox[0],
        origin[1] + bbox[1],
        origin[0] + bbox[2],
        origin[1] + bbox[3],
    ]
    _save_raster_image(image, path)
    field = FieldValue(
        text="S8000000A",
        font="helv",
        font_size=14.0,
        x=origin[0],
        y=origin[1],
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "CERT COPY")

    after = Image.open(path).convert("RGB")
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    crop = after.crop((max(0, x0 - 4), max(0, y0 - 4), x1 + 24, y1 + 4))
    lights = [
        pixel
        for pixel in crop.getdata()
        if 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2] >= 140
    ]
    assert lights
    median = sorted(lights, key=lambda p: p[0] + p[1] + p[2])[len(lights) // 2]
    assert all(abs(median[i] - fill[i]) < 40 for i in range(3)), (median, fill)
    darks = [
        pixel
        for pixel in crop.getdata()
        if 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2] <= 70
    ]
    assert darks
    assert after.tobytes() != Image.new("RGB", after.size, fill).tobytes()


@pytest.mark.parametrize("suffix", [".png", ".jpg", ".jpeg"])
def test_overlay_leaves_nearby_table_rules(tmp_path: Path, suffix: str) -> None:
    font_path = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    )
    if font_path is None:
        pytest.skip("no sans TTF available")
    path = tmp_path / f"totals{suffix}"
    image = Image.new("RGB", (400, 200), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, 11)
    origin = (72.0, 100.0)
    draw.text(origin, "$1,452,650", fill=(18, 18, 18), font=font, anchor="ls")
    draw.line([(70, 86), (150, 86)], fill=(10, 10, 10), width=2)
    draw.line([(70, 106), (150, 106)], fill=(10, 10, 10), width=2)
    draw.line([(70, 108), (150, 108)], fill=(10, 10, 10), width=2)
    try:
        bbox = font.getbbox("$1,452,650", anchor="ls")
    except TypeError:
        bbox = font.getbbox("$1,452,650")
    rect = [
        origin[0] + bbox[0],
        origin[1] + bbox[1],
        origin[0] + bbox[2],
        origin[1] + bbox[3],
    ]
    _save_raster_image(image, path)
    field = FieldValue(
        text="$1,452,650",
        font="helv",
        font_size=11.0,
        x=origin[0],
        y=origin[1],
        page=0,
        rect=rect,
    )
    overlay_field_text(path, field, "$1,999,999")

    after = Image.open(path).convert("RGB")

    def _line_still_dark(y: float) -> bool:
        band = after.crop((72, int(y) - 1, 140, int(y) + 2))
        dark = 0
        total = 0
        for pixel in band.getdata():
            total += 1
            if 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2] <= 80:
                dark += 1
        return total > 0 and dark / total >= 0.12

    assert _line_still_dark(86)
    assert _line_still_dark(106)
    assert _line_still_dark(108)


def _logo_field(rect: list[float]) -> FieldValue:
    return FieldValue(
        text="[image]",
        font="helv",
        font_size=12.0,
        x=rect[0],
        y=rect[3],
        page=0,
        rect=rect,
        kind="image",
    )


def test_can_manipulate_png_region_and_stay_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(
        name
        for name, field in document.fields.items()
        if field.kind == "image" and len(field.rect) == 4
    )
    before_bytes = Path(get_document_file_path("ocbc_sample") or "").read_bytes()
    response = _inject(
        "ocbc_sample",
        field_name,
        DefectType.IMAGE_MANIPULATION,
        indicator="compression_mismatch",
    )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["defect_type"] == "image_manipulation"
    assert "compression mismatch" in payload["description"].lower()
    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.read_bytes() != before_bytes
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize("indicator", list(MANIPULATION_INDICATORS))
def test_png_image_manipulation_indicators_rewrite_the_field_region(
    tmp_path: Path, indicator: str
) -> None:
    path = tmp_path / f"logo_{indicator}.png"
    image = Image.new("RGB", (220, 90), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([24, 18, 88, 58], fill=(200, 30, 30))
    draw.rectangle([90, 18, 120, 58], fill=(30, 30, 200))
    image.save(path)
    before = path.read_bytes()
    field = _logo_field([24.0, 18.0, 120.0, 58.0])
    extra = {}
    if indicator == "scaling_anomaly":
        extra = {"scale_x": 1.85, "scale_y": 0.45}
    manipulate_field_region(path, field, indicator, **extra)
    assert path.read_bytes() != before
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_png_scaling_anomaly_keeps_the_full_mark_but_changes_aspect(tmp_path: Path) -> None:
    path = tmp_path / "crest.png"
    image = Image.new("RGB", (200, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 10, 38, 49], fill=(220, 20, 20))
    draw.rectangle([102, 10, 119, 49], fill=(20, 20, 220))
    image.save(path)
    field = _logo_field([20.0, 10.0, 120.0, 50.0])
    manipulate_field_region(path, field, "scaling_anomaly")
    after = Image.open(path).convert("RGB")
    pixels = list(after.getdata())
    assert any(pixel[0] > 180 and pixel[2] < 80 for pixel in pixels)
    assert any(pixel[2] > 180 and pixel[0] < 80 for pixel in pixels)
    reds = [i % after.width for i, pixel in enumerate(pixels) if pixel[0] > 180 and pixel[2] < 80]
    blues = [i % after.width for i, pixel in enumerate(pixels) if pixel[2] > 180 and pixel[0] < 80]
    assert reds and blues
    assert max(blues) - min(reds) > 100


def test_png_scaling_anomaly_honours_scale_overrides(tmp_path: Path) -> None:
    path = tmp_path / "crest_custom.png"
    image = Image.new("RGB", (200, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 10, 119, 49], fill=(40, 160, 40))
    image.save(path)
    field = _logo_field([20.0, 10.0, 120.0, 50.0])
    manipulate_field_region(path, field, "scaling_anomaly", scale_x=2.0, scale_y=0.4)
    after = Image.open(path).convert("RGB")
    greens = [
        (i % after.width, i // after.width)
        for i, pixel in enumerate(after.getdata())
        if pixel[1] > 120 and pixel[0] < 100
    ]
    assert greens
    xs = [x for x, _ in greens]
    ys = [y for _, y in greens]
    assert max(xs) - min(xs) > 160
    assert max(ys) - min(ys) < 28


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_image_misalignment_moves_original_pixels_not_a_new_font(
    tmp_path: Path, suffix: str
) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")
    font = ImageFont.truetype(sans, 28)
    path = tmp_path / f"shift_name{suffix}"
    image = Image.new("RGB", (420, 120), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin = (24.0, 70.0)
    draw.text(origin, "Ang Choon Huat", fill=(210, 20, 20), font=font, anchor="ls")
    try:
        bbox = font.getbbox("Ang Choon Huat", anchor="ls")
    except TypeError:
        bbox = font.getbbox("Ang Choon Huat")
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    _save_raster_image(image, path)
    before = Image.open(path).convert("RGB")
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    source_crop = before.crop((x0, y0, x1, y1))
    field = FieldValue(
        text="Ang Choon Huat",
        font="helv",
        font_size=28.0,
        x=origin[0],
        y=origin[1],
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, field.text, x=origin[0] + 30)
    after = Image.open(path).convert("RGB")
    original_slot = after.crop((x0, y0, min(x0 + 24, x1), y1))
    moved_slot = after.crop((x0 + 30, y0, x1 + 30, y1))
    assert not any(
        pixel[0] > 160 and pixel[1] < 80 and pixel[2] < 80
        for pixel in original_slot.getdata()
    )
    mae = sum(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
        for a, b in zip(source_crop.getdata(), moved_slot.getdata())
    ) / max(1, source_crop.width * source_crop.height)
    assert mae < 12, f"shifted pixels should match the original crop, mae={mae}"


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_image_font_size_scales_original_pixels_not_a_new_font(
    tmp_path: Path, suffix: str
) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")
    text = "Ang Choon Huat"
    font = ImageFont.truetype(sans, 18)
    path = tmp_path / f"size_name{suffix}"
    image = Image.new("RGB", (420, 140), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    origin = (24.0, 90.0)
    draw.text(origin, text, fill=(210, 20, 20), font=font, anchor="ls")
    try:
        bbox = font.getbbox(text, anchor="ls")
    except TypeError:
        bbox = font.getbbox(text)
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    _save_raster_image(image, path)
    field = FieldValue(
        text=text,
        font="helv",
        font_size=18.0,
        x=origin[0],
        y=origin[1],
        page=0,
        rect=rect,
    )
    before = Image.open(path).convert("L")
    bx0, by0, bx1, by1 = [int(round(v)) for v in rect]
    before_h = _ink_height(before.crop((bx0, by0, bx1, by1)))
    replace_field_text(path, field, text, font_size=28.0)
    after = Image.open(path).convert("RGB")
    after_gray = after.convert("L")
    pad = 20
    after_h = _ink_height(
        after_gray.crop(
            (
                max(0, bx0 - pad),
                max(0, by0 - pad),
                min(after.width, bx1 + pad),
                min(after.height, by1 + pad),
            )
        )
    )
    assert after_h > before_h * 1.15, (before_h, after_h)
    assert any(
        pixel[0] > 160 and pixel[1] < 80 and pixel[2] < 80
        for pixel in after.getdata()
    )


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_image_spelling_keeps_unchanged_prefix_pixels(
    tmp_path: Path, suffix: str
) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    if sans is None:
        pytest.skip("no sans TTF available")
    font = ImageFont.truetype(sans, 22)
    path = tmp_path / f"spell_name{suffix}"
    image = Image.new("RGB", (460, 140), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((24.0, 40.0), "Geneco", fill=(210, 20, 20), font=font, anchor="ls")
    origin = (24.0, 95.0)
    text = "Ang Choon Huat"
    draw.text(origin, text, fill=(210, 20, 20), font=font, anchor="ls")
    try:
        bbox = font.getbbox(text, anchor="ls")
    except TypeError:
        bbox = font.getbbox(text)
    rect = [
        float(origin[0] + bbox[0] - 2),
        float(origin[1] + bbox[1] - 2),
        float(origin[0] + bbox[2] + 2),
        float(origin[1] + bbox[3] + 2),
    ]
    _save_raster_image(image, path)
    before = Image.open(path).convert("RGB")
    try:
        prefix_bbox = font.getbbox("Ang Choon Hu", anchor="ls")
    except TypeError:
        prefix_bbox = font.getbbox("Ang Choon Hu")
    px0 = int(origin[0] + prefix_bbox[0])
    py0 = int(origin[1] + prefix_bbox[1])
    px1 = int(origin[0] + prefix_bbox[2] - 3)
    py1 = int(origin[1] + prefix_bbox[3])
    source_prefix = before.crop((px0, py0, px1, py1))
    x0, y0, x1, y1 = [int(round(v)) for v in rect]
    field = FieldValue(
        text=text,
        font="helv",
        font_size=22.0,
        x=origin[0],
        y=origin[1],
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, "Ang Choon Huet")
    after = Image.open(path).convert("RGB")
    rematched = _match_font_to_crop(
        after,
        rect,
        "Ang Choon Huet",
        ink=(210, 20, 20),
        bg=(255, 255, 255),
        max_size=22,
    )
    name = Path(getattr(rematched, "path", "") or "").name.lower()
    assert "courier" not in name
    assert "mono" not in name
    after_gray = after.convert("L")
    assert _ink_height(after_gray.crop((x0, y0, x1 + 8, y1))) > 10
    after_prefix = after.crop((px0, py0, px1, py1))
    mae = sum(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
        for a, b in zip(source_prefix.getdata(), after_prefix.getdata())
    ) / max(1, source_prefix.width * source_prefix.height)
    limit = 18.0 if suffix in {".jpg", ".jpeg"} else 6.0
    assert mae < limit, f"unchanged prefix should keep original pixels, mae={mae}"


@pytest.mark.parametrize("suffix", [".png", ".jpg"])
def test_numerical_rewrite_does_not_clip_line_above(tmp_path: Path, suffix: str) -> None:
    sans = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    bold = _first_existing_ttf(
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        sans,
    )
    if sans is None or bold is None:
        pytest.skip("no sans TTF available")
    label_font = ImageFont.truetype(sans, 16)
    amount_font = ImageFont.truetype(bold, 36)
    path = tmp_path / f"amount_over_label{suffix}"
    image = Image.new("RGB", (420, 200), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    label = "Statement No.: 6871408"
    amount = "$187.41"
    label_origin = (180.0, 70.0)
    amount_origin = (220.0, 130.0)
    draw.text(label_origin, label, fill=(90, 90, 90), font=label_font, anchor="ls")
    draw.text(amount_origin, amount, fill=(20, 20, 20), font=amount_font, anchor="ls")
    try:
        label_bbox = label_font.getbbox(label, anchor="ls")
        amount_bbox = amount_font.getbbox(amount, anchor="ls")
    except TypeError:
        label_bbox = label_font.getbbox(label)
        amount_bbox = amount_font.getbbox(amount)
    label_box = (
        int(label_origin[0] + label_bbox[0]),
        int(label_origin[1] + label_bbox[1]),
        int(label_origin[0] + label_bbox[2]),
        int(label_origin[1] + label_bbox[3]),
    )
    # OCR-style box that overlaps the line above, like the Geneco bill.
    rect = [
        float(amount_origin[0] + amount_bbox[0] - 4),
        float(label_box[3] - 4),
        float(amount_origin[0] + amount_bbox[2] + 4),
        float(amount_origin[1] + amount_bbox[3] + 4),
    ]
    _save_raster_image(image, path)
    before = Image.open(path).convert("RGB")
    source_label = before.crop(label_box)
    field = FieldValue(
        text=amount,
        font="helv",
        font_size=36.0,
        x=amount_origin[0],
        y=amount_origin[1],
        page=0,
        rect=rect,
    )
    replace_field_text(path, field, "$287.41")
    after = Image.open(path).convert("RGB")
    after_label = after.crop(label_box)
    mae = sum(
        abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
        for a, b in zip(source_label.getdata(), after_label.getdata())
    ) / max(1, source_label.width * source_label.height)
    limit = 16.0 if suffix in {".jpg", ".jpeg"} else 4.0
    assert mae < limit, f"line above the amount should stay intact, mae={mae}"
    amount_crop = after.crop(
        (
            int(rect[0]),
            int(rect[1] + 8),
            int(rect[2] + 12),
            int(rect[3]),
        )
    )
    assert _ink_width(amount_crop.convert("L")) > 20
