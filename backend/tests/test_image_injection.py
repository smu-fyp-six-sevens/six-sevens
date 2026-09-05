from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from app.injection import pdf as injection_pdf
from app.injection.field_text import is_label_only
from app.injection.pdf import (
    discover_fields_from_image,
    read_document_metadata,
    replace_field_text,
    resolve_emphasis_font_file,
    _image_redraw_span,
    _match_font_to_crop,
    _sample_bg_color,
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


def test_can_inject_into_png_and_stay_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None

    numeric = next(
        (
            name
            for name, field in document.fields.items()
            if any(ch.isdigit() for ch in field.text)
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
        (name for name, field in document.fields.items() if any(ch.isdigit() for ch in field.text)),
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
    image.save(path)
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


def test_png_text_overlay_is_not_supported() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(iter(document.fields))
    response = _inject(
        "ocbc_sample",
        field_name,
        DefectType.TEXT_OVERLAY,
        new_text="JANE CITIZEN",
    )
    assert response.status_code == 400
    assert "pdf" in response.json()["detail"].lower()


def test_png_image_manipulation_is_not_supported() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(
        name
        for name, field in document.fields.items()
        if any(ch.isdigit() for ch in field.text) and len(field.rect) == 4
    )
    response = _inject(
        "ocbc_sample",
        field_name,
        DefectType.IMAGE_MANIPULATION,
        indicator="compression_mismatch",
    )
    assert response.status_code == 400
    assert "pdf" in response.json()["detail"].lower()
