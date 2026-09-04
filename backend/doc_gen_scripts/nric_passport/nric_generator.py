"""
Generates an NRIC template PDF laid out to the standard Singapore NRIC field
arrangement.

Front card: white header band holding "REPUBLIC OF SINGAPORE", IDENTITY CARD NO.
and the crest, a portrait, and a field column carrying Name / Race / Date of
birth + Sex / Country/Place of birth. No secondary portrait -- there's no
synthetic photo asset to put there.

Back card: serial top-right, a full-width tinted band holding the barcode, NRIC No.
directly beneath it, a fingerprint area, Date of issue, and the address block at the
foot. There is no blood group -- the card does not carry one.

Every field is a keyword argument to generate_nric_card(), so it can be called
standalone (see __main__ below) with no mastersheet at all. The CLI instead drives
it from the mastersheet's NRIC-Passport tab, one card per row whose Template column
says "NRIC" (a "Passport" row is passport_generator.py's job instead), joined to
Individuals (by Individual ID) for the residential address, which NRIC-Passport
itself doesn't carry. Output filenames come from NRIC-Passport's own Filename column.

Gender and race have no mastersheet source. Race is inferred from the name itself --
this mastersheet's personas already encode it there (a Malay "bin/binte/binti" or
Indian "s/o/d/o" patronymic, else Chinese naming), which is exactly the CMIO signal a
real Singapore NRIC's Race field reflects, so this isn't a guess so much as reading a
signal that's already present by construction. Gender has no signal anywhere in the
mastersheet, so it's an invented, seeded-per-individual coin flip -- same "invent only
when nothing sources it, but keep it deterministic" convention as case_facts.py's
corporate secretary, or credit_bureau_report_generator.py's score/risk grade.

Portrait, secondary portrait and fingerprint stay as marked placeholder boxes
rather than rendered imagery -- there's no synthetic photo/print asset to put
there. The header crest and the two faded ghost watermarks (front, beside Sex;
back) each use their own bundled artwork by default (DEFAULT_CREST_ASSET /
DEFAULT_FRONT_GHOST_ASSET / DEFAULT_BACK_GHOST_ASSET); pass crest_asset=None /
front_ghost_asset=None / back_ghost_asset=None to fall back to the drawn grey
placeholder shapes instead. No
guilloche, hologram or microprint is drawn either. The page carries a SPECIMEN footer and, by default, a
SYNTHETIC-TEST-DATA metadata tag, suppressible with --no-synthetic-tag in line with
the other generators.

Usage:
cd backend/doc_gen_scripts/nric_passport
python nric_generator.py ../v9_document_mastersheet.xlsx --outdir out/
python nric_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001 C002
python nric_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --individuals C001-P1

Run with no arguments instead to build one example card ("Long Jing En") at
output/nric_template.pdf.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from case_facts import as_date, seeded_rng

PAGE_W, PAGE_H = A4
FONT = "Helvetica"
BOLD = "Helvetica-Bold"

# Emblem artwork: the front header crest, the front ghost watermark (beside the
# Sex field), and the back ghost watermark each use their own image by default --
# see generate_nric_card()'s crest_asset/front_ghost_asset/back_ghost_asset.
DEFAULT_CREST_ASSET = Path(__file__).resolve().parent / "tg_image_609089445.jpeg"
DEFAULT_FRONT_GHOST_ASSET = Path(__file__).resolve().parent / "images.png"
DEFAULT_BACK_GHOST_ASSET = Path(__file__).resolve().parent / "tg_image_609089445.jpeg"

# ---- geometry ------------------------------------------------------------
# ID-1 card is 85.6 x 54 mm (aspect 1.586).
CARD_W = 500.0
CARD_H = CARD_W / 1.586          # ~315.3
CARD_X = (PAGE_W - CARD_W) / 2.0
FRONT_TOP = 782.0
BACK_TOP = 422.0

HEADER_H = 78.0                  # front card header block

# ---- palette -------------------------------------------------------------
PAGE_BG = colors.HexColor("#ffffff")
CARD_BG = colors.HexColor("#f9e5e8")
CARD_EDGE = colors.HexColor("#c7a4ac")
HEADER_RULE = colors.HexColor("#8c2434")
LABEL_GREY = colors.HexColor("#4a4a4a")
PLACEHOLDER_EDGE = colors.HexColor("#9a8791")
GHOST = colors.HexColor("#e6d4d8")


def _save_pdf(output_path: str, draw_fn, *, synthetic_tag: bool = True) -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = canvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    meta = {
        "/Producer": "DocuSuite Enterprise 4.2",
        "/Creator": "DocuSuite Enterprise 4.2",
        "/CreationDate": creation,
    }
    if synthetic_tag:
        meta["/Subject"] = "SYNTHETIC-TEST-DATA"
        meta["/Keywords"] = "SYNTHETIC-TEST-DATA"
    writer.add_metadata(meta)
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _text(c: canvas.Canvas, x: float, y: float, s: str, *, font: str = FONT, size: float = 9, color=colors.black, tracking: float = 0.0) -> None:
    """Card values are set with noticeable letter-spacing. charSpace lives on the
    text object rather than the canvas, and a fresh text object per call keeps the
    tracking from leaking into anything drawn afterwards."""
    t = c.beginText()
    t.setTextOrigin(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(tracking)   # always emitted: Tc persists in the content stream
    t.textOut(s)
    c.drawText(t)


def _text_centred(c: canvas.Canvas, cx: float, y: float, s: str, *, font: str = FONT, size: float = 9, color=colors.black, tracking: float = 0.0) -> None:
    _text(c, cx - _text_width(s, font, size, tracking) / 2, y, s, font=font, size=size, color=color, tracking=tracking)


def _text_width(s: str, font: str, size: float, tracking: float = 0.0) -> float:
    return pdfmetrics.stringWidth(s, font, size) + tracking * max(len(s) - 1, 0)


def _field(c: canvas.Canvas, x: float, y: float, label: str, value: str, *, label_size: float = 7.5, value_size: float = 11.5, gap: float = 5.0, tracking: float = 0.5) -> float:
    """Small label with the value beneath it. Returns the value's baseline; callers
    step down by an explicit row pitch rather than trusting a return value that used
    to under-report the block height and cause label/value collisions."""
    _text(c, x, y, label, font=FONT, size=label_size, color=LABEL_GREY)
    value_y = y - value_size - gap
    _text(c, x, value_y, value, font=BOLD, size=value_size, tracking=tracking)
    return value_y


def _load_ghost_image(path: str | Path, *, alpha: float = 1.0, greyscale: bool = True, knockout_white: bool = True, white_cutoff: int = 235):
    """Load an emblem asset and prepare it for ghosting.

    reportlab's drawImage takes no alpha argument, and setFillAlpha does not apply to
    images -- so transparency has to be baked into the pixels before handing the image
    over. That is what this does: desaturate, optionally knock out a near-white
    background (scans and JPEG/AVIF exports have no alpha channel, so the paper shows
    up as an opaque white rectangle otherwise), then scale the alpha channel down.

    Returns an ImageReader ready for drawImage(..., mask="auto"), or None if the file
    is missing or the format has no decoder installed (AVIF and newer WebP need
    pillow-avif-plugin / a recent Pillow; PNG is the safe choice for assets/).
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(str(path)).convert("RGBA")
    except (FileNotFoundError, OSError):
        return None

    if greyscale:
        grey = im.convert("L").convert("RGBA")
        grey.putalpha(im.getchannel("A"))
        im = grey

    if knockout_white:
        from PIL import ImageChops

        # zero the alpha wherever the pixel is near-white, keeping whatever alpha
        # the source already had everywhere else. A named, explicitly-typed
        # function here (rather than a bare lambda) is what gets Image.point()'s
        # overloaded signature to resolve against the per-pixel Callable[[int],
        # int] form instead of its unrelated ImagePointTransform form.
        def _keep_non_white(v: int) -> int:
            return 0 if v >= white_cutoff else 255

        keep = im.convert("L").point(_keep_non_white)
        im.putalpha(ImageChops.multiply(im.getchannel("A"), keep))

    if alpha < 1.0:
        def _scale_alpha(v: int) -> int:
            return int(v * alpha)

        im.putalpha(im.getchannel("A").point(_scale_alpha))

    return ImageReader(im)


def _draw_fitted(c: canvas.Canvas, reader, cx: float, cy: float, box_w: float, box_h: float) -> None:
    """Centre an image in a box, preserving aspect ratio."""
    iw, ih = reader.getSize()
    scale = min(box_w / iw, box_h / ih)
    w, h = iw * scale, ih * scale
    c.drawImage(reader, cx - w / 2, cy - h / 2, width=w, height=h, mask="auto")


def _placeholder_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, caption: str, *, caption_size: float = 5.5, radius: float = 0.0) -> None:
    c.setFillColor(colors.white)
    c.setStrokeColor(PLACEHOLDER_EDGE)
    c.setLineWidth(0.7)
    c.setDash(3, 2)
    if radius:
        c.roundRect(x, y, w, h, radius, fill=1, stroke=1)
    else:
        c.rect(x, y, w, h, fill=1, stroke=1)
    c.setDash()
    for i, line in enumerate(caption.split("\n")):
        _text_centred(c, x + w / 2, y + h / 2 + 4 - i * (caption_size + 2), line,
                      size=caption_size, color=colors.HexColor("#7d7d7d"))


def _crest_placeholder(c: canvas.Canvas, cx: float, cy: float, r: float = 22.0, *, asset=None) -> None:
    if asset is not None:
        _draw_fitted(c, asset, cx, cy, r * 2.2, r * 2.2)
        return
    c.setFillColor(colors.HexColor("#fae3e3"))
    c.setStrokeColor(colors.HexColor("#c58f8f"))
    c.setLineWidth(0.8)
    c.circle(cx, cy, r, fill=1, stroke=1)
    _text_centred(c, cx, cy + 2, "SG", font=BOLD, size=8, color=colors.HexColor("#bf2b2b"))
    _text_centred(c, cx, cy - 8, "CREST", size=5.2, color=colors.HexColor("#8a6a2f"))


def _ghost_mark(c: canvas.Canvas, cx: float, cy: float, w: float = 74.0, h: float = 84.0, *, alpha: float = 0.22, label: str = "EMBLEM\nPLACEHOLDER", asset=None) -> None:
    """Neutral shield mark standing in for the ghost emblem region.

    Transparency in reportlab is an ExtGState (PDF >= 1.4), not an RGBA colour, and
    there is a trap: setFillColor(c) defaults its alpha argument to the Color
    object's own .alpha, which is 1.0, so a setFillColor call AFTER setFillAlpha
    silently resets transparency back to opaque. Alpha therefore has to be passed
    into the colour call itself. It is also graphics state, so it needs
    saveState/restoreState or every later fill on the page inherits it.

    Greying is done by desaturating the colour; alpha only controls how much of the
    card shows through underneath.
    """
    if asset is not None:
        _draw_fitted(c, asset, cx, cy, w, h)
        return

    x, y = cx - w / 2, cy - h / 2
    c.saveState()
    shield = c.beginPath()
    shield.moveTo(x, y + h)
    shield.lineTo(x + w, y + h)
    shield.lineTo(x + w, y + h * 0.40)
    shield.curveTo(x + w, y + h * 0.14, x + w * 0.72, y, x + w * 0.5, y)
    shield.curveTo(x + w * 0.28, y, x, y + h * 0.14, x, y + h * 0.40)
    shield.close()

    c.setFillColor(colors.HexColor("#6b6b6b"), alpha=alpha)
    c.setStrokeColor(colors.HexColor("#4f4f4f"), alpha=alpha)
    c.setLineWidth(1.2)
    c.drawPath(shield, fill=1, stroke=1)

    inner = c.beginPath()
    m = 9.0
    inner.moveTo(x + m, y + h - m)
    inner.lineTo(x + w - m, y + h - m)
    inner.lineTo(x + w - m, y + h * 0.42)
    inner.curveTo(x + w - m, y + h * 0.18, x + w * 0.70, y + m, x + w * 0.5, y + m)
    inner.curveTo(x + w * 0.30, y + m, x + m, y + h * 0.18, x + m, y + h * 0.42)
    inner.close()
    c.setFillColor(colors.HexColor("#8e8e8e"), alpha=alpha * 0.8)
    c.setStrokeColor(colors.HexColor("#8e8e8e"), alpha=alpha * 0.8)
    c.drawPath(inner, fill=1, stroke=0)
    c.restoreState()

    c.saveState()
    faded = colors.HexColor("#3f3f3f")
    faded.alpha = min(alpha + 0.4, 1.0)
    for i, line in enumerate(label.split("\n")):
        _text_centred(c, cx, cy + 4 - i * 7.5, line, size=5.2, color=faded)
    c.restoreState()


def _barcode_placeholder(c: canvas.Canvas, x: float, y: float, w: float, h: float, seed: str) -> None:
    """Decorative bar block seeded off the serial. Encodes nothing; it exists so the
    back card has a barcode-shaped region for layout and extraction tests."""
    rng = seeded_rng(f"{seed}|barcode-bars")
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.HexColor("#c4c4c4"))
    c.setLineWidth(0.5)
    c.rect(x, y, w, h, fill=1, stroke=1)

    c.setFillColor(colors.black)
    cur = x + 7.0
    limit = x + w - 7.0
    while cur < limit:
        bar_w = rng.choice([0.9, 0.9, 1.3, 1.8, 2.4])
        if cur + bar_w > limit:
            break
        c.rect(cur, y + 5, bar_w, h - 10, fill=1, stroke=0)
        cur += bar_w + rng.choice([1.0, 1.4, 1.9, 2.7])


def _card_shell(c: canvas.Canvas, y0: float) -> None:
    c.setFillColor(CARD_BG)
    c.setStrokeColor(CARD_EDGE)
    c.setLineWidth(0.9)
    c.roundRect(CARD_X, y0, CARD_W, CARD_H, 8, fill=1, stroke=1)


def generate_nric_card(
    output_path: str,
    *,
    name: str,
    nric_no: str,
    date_of_birth: date,
    nationality: str,
    gender: str,
    race: str,
    address_lines: list[str],
    issue_date: date,
    expiry_date: date,
    serial_no: str,
    birth_place: str = "Singapore",
    card_type: str = "NRIC",
    crest_asset: str | Path | None = DEFAULT_CREST_ASSET,
    front_ghost_asset: str | Path | None = DEFAULT_FRONT_GHOST_ASSET,
    back_ghost_asset: str | Path | None = DEFAULT_BACK_GHOST_ASSET,
    ghost_alpha: float = 0.22,
    synthetic_tag: bool = True,
) -> dict:
    """`expiry_date`, `nationality` and `card_type` are accepted for call-site
    compatibility but are not drawn: the card carries none of them (it does not
    expire, and citizenship is implied by the card itself).

    `crest_asset` fills the front header's crest slot at full strength (defaults to
    DEFAULT_CREST_ASSET). `front_ghost_asset`/`back_ghost_asset` fill their
    respective ghost-watermark slots, desaturated and faded to `ghost_alpha`
    (default to DEFAULT_FRONT_GHOST_ASSET/DEFAULT_BACK_GHOST_ASSET). Pass None (or
    a missing path) for any of the three to fall back to its drawn grey placeholder
    shape instead."""

    crest_art = _load_ghost_image(crest_asset, greyscale=False, knockout_white=True) if crest_asset else None
    front_ghost_art = _load_ghost_image(front_ghost_asset, alpha=ghost_alpha, greyscale=True, knockout_white=True) if front_ghost_asset else None
    back_ghost_art = _load_ghost_image(back_ghost_asset, alpha=ghost_alpha, greyscale=True, knockout_white=True) if back_ghost_asset else None

    def _draw_front(c: canvas.Canvas) -> None:
        y0 = FRONT_TOP - CARD_H
        _card_shell(c, y0)

        # ---- header block. Only the "IDENTITY CARD NO." row sits on a white strip;
        # "REPUBLIC OF SINGAPORE" stays on the card's pink body colour above it.
        band_y = y0 + CARD_H - HEADER_H
        id_strip_y = y0 + CARD_H - 74
        id_strip_h = 28
        c.setFillColor(colors.white)
        c.rect(CARD_X + 8, id_strip_y, CARD_W - 16, id_strip_h, fill=1, stroke=0)

        _text(c, CARD_X + 14, y0 + CARD_H - 32, "REPUBLIC OF SINGAPORE",
              font=BOLD, size=20, color=colors.black, tracking=0.4)
        _text(c, CARD_X + 14, y0 + CARD_H - 62, "IDENTITY CARD NO.",
              font=BOLD, size=12, color=colors.black, tracking=0.3)
        _text(c, CARD_X + 168, y0 + CARD_H - 64, nric_no,
              font=BOLD, size=17, color=colors.black, tracking=0.6)

        _crest_placeholder(c, CARD_X + CARD_W - 46, y0 + CARD_H - 40, r=26, asset=crest_art)

        c.setStrokeColor(HEADER_RULE)
        c.setLineWidth(1.6)
        c.line(CARD_X + 8, band_y, CARD_X + CARD_W - 8, band_y)

        # ---- portrait column
        photo_w, photo_h = 112.0, 142.0
        photo_x = CARD_X + 10
        photo_y = band_y - 8 - photo_h
        _placeholder_box(c, photo_x, photo_y, photo_w, photo_h, "PHOTO\nPLACEHOLDER")

        # ---- field column
        fx = CARD_X + 140
        _field(c, fx, y0 + 218, "Name", name.upper(), value_size=14.5, tracking=0.6)
        _field(c, fx, y0 + 168, "Race", race.upper())
        _field(c, fx, y0 + 120, "Date of birth", date_of_birth.strftime("%d-%m-%Y"))
        _field(c, fx + 200, y0 + 120, "Sex", gender.upper())
        _field(c, fx, y0 + 72, "Country/Place of birth", birth_place.upper())

        _ghost_mark(c, CARD_X + CARD_W - 74, y0 + 112, alpha=ghost_alpha, asset=front_ghost_art)

    def _draw_back(c: canvas.Canvas) -> None:
        y0 = BACK_TOP - CARD_H
        _card_shell(c, y0)

        # ---- serial, top right
        _text(c, CARD_X + CARD_W - 16 - _text_width(serial_no, BOLD, 10.5, 1.4),
              y0 + CARD_H - 26, serial_no,
              font=BOLD, size=10.5, color=colors.HexColor("#333333"), tracking=1.4)

        # ---- barcode row (sits directly on the card's pink body colour -- no
        # separate tinted band; _barcode_placeholder draws its own white box behind
        # the bars themselves).
        band_h = 62.0
        band_y = y0 + CARD_H - 36 - band_h

        bc_x = CARD_X + 78
        bc_w = CARD_W - 156
        _barcode_placeholder(c, bc_x, band_y + 7, bc_w, band_h - 14, serial_no)

        # ---- NRIC No. directly beneath the band
        nric_y = band_y - 20
        label_x = CARD_X + 168
        _text(c, label_x, nric_y, "NRIC No.", font=FONT, size=8, color=LABEL_GREY)
        _text(c, label_x + _text_width("NRIC No.", FONT, 8) + 9, nric_y - 2, nric_no,
              font=BOLD, size=14, tracking=0.6)

        # ---- fingerprint
        fp_w, fp_h = 96.0, 100.0
        fp_x = CARD_X + 46
        fp_y = y0 + 88
        _placeholder_box(c, fp_x, fp_y, fp_w, fp_h, "FINGERPRINT\nPLACEHOLDER")

        # ---- date of issue, right of the fingerprint
        _field(c, CARD_X + 190, y0 + 112, "Date of issue", issue_date.strftime("%d-%m-%Y"))

        _ghost_mark(c, CARD_X + CARD_W - 88, y0 + 132, w=82.0, h=94.0, alpha=ghost_alpha, asset=back_ghost_art)

        # ---- address, laid out upward from a fixed floor so it can never run past
        # the card edge no matter how many lines the address splits into
        lines = [ln.upper() for ln in address_lines][:4]
        floor = y0 + 18
        pitch = 14.0
        _text(c, fp_x, floor + pitch * len(lines) + 6, "Address", font=FONT, size=7.5, color=LABEL_GREY)
        ay = floor + pitch * (len(lines) - 1)
        for line in lines:
            _text(c, fp_x, ay, line, font=BOLD, size=10.5, tracking=0.4)
            ay -= pitch

    def draw(c: canvas.Canvas) -> None:
        c.setFillColor(PAGE_BG)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        _draw_front(c)
        _draw_back(c)
        _text_centred(c, PAGE_W / 2, 46,
                      "",
                      size=7, color=colors.HexColor("#8a8a8a"))

    _save_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {"output_path": output_path, "name": name, "nric_no": nric_no}


# --------------------------------------------------------------------------
# Mastersheet-driven persona
# --------------------------------------------------------------------------

_MALAY_PATRONYM_RE = re.compile(r"\bbin\b|\bbinte\b|\bbinti\b", re.I)
_INDIAN_PATRONYM_RE = re.compile(r"\bs/o\b|\bd/o\b", re.I)


def _infer_race(name: str) -> str:
    if _MALAY_PATRONYM_RE.search(name):
        return "Malay"
    if _INDIAN_PATRONYM_RE.search(name):
        return "Indian"
    if name.strip():
        return "Chinese"
    return "Others"


def _rows(ws) -> list[dict]:
    header = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        out.append(dict(zip(header, row)))
    return out


def build_persona(row: dict, address: str) -> dict:
    individual_id = str(row.get("Individual ID") or "")
    rng = seeded_rng(f"{individual_id}|nric-card")

    name = str(row.get("Individual Name (auto)") or "")
    dob = as_date(row.get("Date of Birth (auto)")) or date(1980, 1, 1)

    issue_date = date(rng.randint(2019, 2025), rng.randint(1, 12), rng.randint(1, 28))
    expiry_date = date(issue_date.year + 10, issue_date.month, issue_date.day)

    return dict(
        name=name,
        nric_no=str(row.get("NRIC/Passport No. (auto)") or ""),
        date_of_birth=dob,
        nationality=str(row.get("Nationality (Auto)") or "Singaporean"),
        gender=rng.choice(["M", "F"]),
        race=_infer_race(name),
        address_lines=[part.strip() for part in address.split(",") if part.strip()] or [""],
        issue_date=issue_date,
        expiry_date=expiry_date,
        serial_no=f"{rng.randint(0, 9_999_999):07d}",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--cases", nargs="*", help="case IDs to include; default is all")
    ap.add_argument("--individuals", nargs="*", help="individual IDs to include; default is all")
    ap.add_argument("--crest-asset", default=str(DEFAULT_CREST_ASSET),
                     help="path to artwork for the front header crest slot (default: the bundled crest image)")
    ap.add_argument("--front-ghost-asset", default=str(DEFAULT_FRONT_GHOST_ASSET),
                     help="path to artwork for the front ghost-watermark slot, beside the Sex field (default: the bundled lion-head image)")
    ap.add_argument("--back-ghost-asset", default=str(DEFAULT_BACK_GHOST_ASSET),
                     help="path to artwork for the back ghost-watermark slot (default: the bundled crest image)")
    ap.add_argument("--ghost-alpha", type=float, default=0.22, help="opacity of the ghosted emblem (default 0.22)")
    ap.add_argument("--no-synthetic-tag", action="store_true", help="omit the SYNTHETIC-TEST-DATA metadata tag")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(args.xlsx, data_only=True)
    address_by_individual = {
        str(r["Individual ID"]): str(r.get("Residential Address") or "")
        for r in _rows(wb["Individuals"])
    }

    count = 0
    for row in _rows(wb["NRIC-Passport"]):
        case_id = str(row.get("Case ID (auto)") or "")
        individual_id = str(row.get("Individual ID") or "")
        if args.cases and case_id not in args.cases:
            continue
        if args.individuals and individual_id not in args.individuals:
            continue
        if "nric" not in str(row.get("Template") or "").lower():
            continue

        address = address_by_individual.get(individual_id, "")
        persona = build_persona(row, address)
        out_name = row.get("Filename") or f"{individual_id}_NRIC.pdf"
        result = generate_nric_card(
            str(outdir / out_name),
            crest_asset=args.crest_asset,
            front_ghost_asset=args.front_ghost_asset,
            back_ghost_asset=args.back_ghost_asset,
            ghost_alpha=args.ghost_alpha,
            synthetic_tag=not args.no_synthetic_tag,
            **persona
        )
        count += 1
        print(f"{individual_id}: {result['name']} -> {result['output_path']}")

    print(f"wrote {count} NRIC card(s)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        out_dir = Path.cwd() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        print(generate_nric_card(
            str(out_dir / "nric_template.pdf"),
            name="LONG JING EN",
            nric_no="T0424580J",
            date_of_birth=date(2004, 1, 6),
            nationality="Singaporean",
            gender="F",
            race="Chinese",
            address_lines=["2 Orchard Turn", "#01-01", "Singapore 238801"],
            issue_date=date(2025, 1, 1),
            expiry_date=date(2035, 1, 1),
            serial_no="0123456",
        ))
