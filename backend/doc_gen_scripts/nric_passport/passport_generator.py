"""
Generates a Singapore passport biodata page (just the identity/photo page --
not the blank visa-observation pages either side of it in a real booklet):
header (PASSPORT / crest / REPUBLIC OF SINGAPORE), a Type/Country Code/
Passport No row, Name, a two-column field grid (Sex / Date of birth / Date of
issue / Date of expiry / National ID No | Nationality / Place of birth /
Authority), a photo placeholder, and a two-line Machine Readable Zone at the
foot with real ICAO 9303 check digits.

Rewritten canvas-based (previously Platypus/Paragraph-based, and broken): the
MRZ's "<" filler characters were fed straight into a reportlab Paragraph,
whose mini-XML markup parser treats "<" as the start of a tag --
"paraparser: syntax error: parse ended with 1 unclosed tags" on every single
passport, every run, standalone or from the mastersheet. Raw
canvas.drawString() (via nric_generator.py's _text() helper, imported below)
draws literal text with no markup parsing, so the same MRZ line that broke
Paragraph() is just text here.

Every field is a keyword argument to generate_passport_page(), so it can be
called standalone (see __main__ below) with no mastersheet at all. The CLI
instead drives it from the mastersheet's NRIC-Passport tab, one page per row
whose Template column says "Passport" (an "NRIC" row is nric_generator.py's
job instead) -- unlike nric_generator.py, this page carries no address field,
so build_persona() doesn't need an Individuals join at all.

Sex, place of birth and national ID number have no mastersheet source, and
surname/given-names have to be split out of the mastersheet's single "Name"
field -- build_persona() derives/invents all of these the same way
nric_generator.py's build_persona() does: sex as a seeded per-individual coin
flip, place of birth defaulted to Singapore, national ID number synthesized
in NRIC format and seeded per individual, and the name split with the same
Chinese-surname-first / Malay-Indian-patronymic heuristic as
credit_bureau_report_generator.py's _split_name().

Usage:
cd backend/doc_gen_scripts/nric_passport
python passport_generator.py ../v9_document_mastersheet.xlsx --outdir out/
python passport_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001 C002
python passport_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --individuals C001-P1

Run with no arguments instead to build one example page ("Tan Wei Ming") at
output/passport_template.pdf.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nric_generator import (
    PAGE_W, PAGE_H, FONT, BOLD, DEFAULT_CREST_ASSET, DEFAULT_FRONT_GHOST_ASSET,
    _text, _text_centred, _text_width, _placeholder_box,
    _load_ghost_image, _draw_fitted, _save_pdf,
)
from case_facts import as_date, seeded_rng

# ---- geometry --------------------------------------------------------------
# Taller and wider than a real ID-3 page's 125x88mm (1.42 ratio) -- this page
# carries all nine fields plus the MRZ band, with the photo on the left and
# every field to its right (a real passport spreads some of that across the
# facing visa page instead), so it needs the extra headroom.
CARD_W = 480.0
CARD_H = 300.0
CARD_X = (PAGE_W - CARD_W) / 2.0
CARD_Y = (PAGE_H - CARD_H) / 2.0

# ---- palette -----------------------------------------------------------
PAGE_BG = colors.white
CARD_BG = colors.HexColor("#eef1fb")
CARD_EDGE = colors.HexColor("#b7c2e0")
MRZ_BG = colors.HexColor("#e7eaf3")
NAVY = colors.HexColor("#14213d")
BLUE = colors.HexColor("#2547a8")
LABEL_BLUE = colors.HexColor("#2547a8")


_PATRONYM_RE = re.compile(r"\bbin\b|\bbinte\b|\bbinti\b|\bs/o\b|\bd/o\b", re.I)


def _split_name(name: str) -> tuple[str, str]:
    """Best-effort Surname / Given Names split -- same heuristic as
    credit_bureau_report_generator.py's _split_name(): Chinese names are
    surname-first ("Tan Wei Ming" -> "Tan" / "Wei Ming"); patronymic names
    ("Muhammad Faiz bin Ahmad") don't decompose into a surname/given pair at
    all, so the whole name goes into given_names rather than a wrong split."""
    name = name.strip()
    if _PATRONYM_RE.search(name):
        return "", name
    tokens = name.split()
    if len(tokens) == 2:
        return tokens[0], tokens[1]
    if len(tokens) == 3:
        return tokens[0], f"{tokens[1]} {tokens[2]}"
    if len(tokens) == 4:
        return tokens[1], f"{tokens[0]} {tokens[2]} {tokens[3]}"
    return "", name


# --------------------------------------------------------------------------
# Machine Readable Zone (ICAO 9303, TD3 / passport format)
# --------------------------------------------------------------------------

def _mrz_clean(s: str) -> str:
    out = []
    for ch in s.upper():
        if ch.isalnum():
            out.append(ch)
        elif ch in " '-":
            out.append("<")
    return "".join(out)


def _mrz_name_field(surname: str, given_names: str) -> str:
    surname_part = _mrz_clean(surname)
    given_part = "<".join(_mrz_clean(p) for p in given_names.split())
    return (f"{surname_part}<<{given_part}" + "<" * 39)[:39]


def _mrz_char_value(ch: str) -> int:
    if ch == "<":
        return 0
    if ch.isdigit():
        return int(ch)
    return ord(ch) - ord("A") + 10


def _mrz_check_digit(s: str) -> str:
    weights = (7, 3, 1)
    total = sum(_mrz_char_value(c) * weights[i % 3] for i, c in enumerate(s))
    return str(total % 10)


def build_mrz(passport_no: str, nationality_code: str, surname: str, given_names: str,
              date_of_birth: date, sex: str, date_of_expiry: date, national_id_no: str) -> tuple[str, str]:
    line1 = ("PA" + nationality_code.upper()[:3].ljust(3, "<") + _mrz_name_field(surname, given_names))
    line1 = (line1 + "<" * 44)[:44]

    passport_field = _mrz_clean(passport_no).ljust(9, "<")[:9]
    passport_check = _mrz_check_digit(passport_field)
    dob_field = date_of_birth.strftime("%y%m%d")
    dob_check = _mrz_check_digit(dob_field)
    sex_char = sex.upper()[0] if sex and sex.upper()[0] in ("M", "F") else "<"
    expiry_field = date_of_expiry.strftime("%y%m%d")
    expiry_check = _mrz_check_digit(expiry_field)
    personal_field = _mrz_clean(national_id_no).ljust(14, "<")[:14]
    personal_check = _mrz_check_digit(personal_field) if personal_field.strip("<") else "<"

    final_str = (passport_field + passport_check + dob_field + dob_check
                 + expiry_field + expiry_check + personal_field + personal_check)
    final_check = _mrz_check_digit(final_str)

    line2 = (
        passport_field + passport_check
        + nationality_code.upper()[:3].ljust(3, "<")
        + dob_field + dob_check
        + sex_char
        + expiry_field + expiry_check
        + personal_field + personal_check
        + final_check
    )
    return line1, (line2 + "<" * 44)[:44]


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

def generate_passport_page(
    output_path: str,
    *,
    passport_no: str,
    surname: str,
    given_names: str,
    nationality: str,
    date_of_birth: date,
    place_of_birth: str,
    sex: str,
    date_of_issue: date,
    date_of_expiry: date,
    national_id_no: str = "",
    issue_authority: str = "MINISTRY OF HOME AFFAIRS",
    nationality_code: str = "SGP",
    crest_asset: str | Path | None = DEFAULT_CREST_ASSET,
    watermark_asset: str | Path | None = DEFAULT_FRONT_GHOST_ASSET,
    synthetic_tag: bool = True,
    redact_photo: bool = False,
) -> dict:
    crest_art = _load_ghost_image(crest_asset, greyscale=False, knockout_white=True) if crest_asset else None
    watermark_art = (
        _load_ghost_image(watermark_asset, alpha=0.4, greyscale=True, knockout_white=True)
        if watermark_asset else None
    )
    full_name = f"{surname} {given_names}".strip() or given_names or surname
    nationality_label = (
        "SINGAPORE CITIZEN" if nationality.strip().lower().startswith("singapor")
        else f"{nationality.upper()} CITIZEN"
    )
    line1, line2 = build_mrz(passport_no, nationality_code, surname, given_names,
                              date_of_birth, sex, date_of_expiry, national_id_no)

    def draw(c: canvas.Canvas) -> None:
        c.setFillColor(PAGE_BG)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        c.setFillColor(CARD_BG)
        c.setStrokeColor(CARD_EDGE)
        c.setLineWidth(0.9)
        c.roundRect(CARD_X, CARD_Y, CARD_W, CARD_H, 8, fill=1, stroke=1)

        hx = CARD_X + 18
        top = CARD_Y + CARD_H

        # ---- header
        hy = top - 28
        _text(c, hx, hy, "PASSPORT", font=BOLD, size=16, color=NAVY, tracking=0.6)
        pw = _text_width("PASSPORT", BOLD, 16, 0.6)
        if crest_art is not None:
            _draw_fitted(c, crest_art, hx + pw + 26, hy + 6, 24, 24)
        _text(c, hx + pw + 44, hy, "REPUBLIC OF SINGAPORE", font=BOLD, size=13, color=BLUE, tracking=0.4)

        # ---- photo placeholder: on the left, reserved first so the text
        # column's x-origin can be measured off its right edge.
        photo_w, photo_h = 140.0, 175.0
        photo_x = hx
        photo_y = top - 46 - photo_h
        if not redact_photo:
            _placeholder_box(c, photo_x, photo_y, photo_w, photo_h, "PHOTO\nPLACEHOLDER")

        # emblem badge in the photo's bottom-right corner -- same ghost-image
        # mechanism as nric_generator.py's front ghost mark, but here it's a
        # corner accent on the photo rather than a full-page watermark. Drawn
        # before the redaction fill below, so half of it (the half over the
        # photo itself) ends up covered, and only the half straddling outside
        # the photo box still peeks out.
        if watermark_art is not None:
            _draw_fitted(c, watermark_art, photo_x + photo_w, photo_y, 56, 56)

        if redact_photo:
            # Simulates a scanned/uploaded document where the photo itself has
            # been blacked out -- solid fill, no caption.
            c.setFillColor(colors.black)
            c.rect(photo_x, photo_y, photo_w, photo_h, fill=1, stroke=0)

        tx = photo_x + photo_w + 22
        text_right = CARD_X + CARD_W - 18

        # ---- Type / Country Code / Passport No
        meta_y = top - 58
        meta_cols = [
            (tx, "Type", "PA"),
            (tx + 68, "Country Code", nationality_code.upper()),
            (tx + 145, "Passport No", passport_no.upper()),
        ]
        for x, label, value in meta_cols:
            _text(c, x, meta_y, label, font=FONT, size=7.5, color=LABEL_BLUE)
            _text(c, x, meta_y - 15, value, font=BOLD, size=10.5, color=colors.black, tracking=0.4)

        # ---- Name
        name_y = top - 100
        _text(c, tx, name_y, "Name", font=FONT, size=7.5, color=LABEL_BLUE)
        _text(c, tx, name_y - 16, full_name.upper(), font=BOLD, size=13, color=colors.black, tracking=0.8)

        # ---- two-column field grid
        col_l_x = tx
        col_r_x = tx + 95
        row_y = top - 138
        row_pitch = 27

        left_fields = [
            ("Sex", sex.upper()),
            ("Date of birth", date_of_birth.strftime("%d %b %Y").upper()),
            ("Date of issue", date_of_issue.strftime("%d %b %Y").upper()),
            ("Date of expiry", date_of_expiry.strftime("%d %b %Y").upper()),
        ]
        right_fields = [
            ("Nationality", nationality_label),
            ("Place of birth", place_of_birth.upper()),
            ("Authority", issue_authority.upper()),
        ]
        if national_id_no:
            right_fields.append(("National ID No", national_id_no.upper()))

        yy = row_y
        for label, value in left_fields:
            _text(c, col_l_x, yy, label, font=FONT, size=7.5, color=LABEL_BLUE)
            _text(c, col_l_x, yy - 13, value, font=BOLD, size=9.5, color=colors.black, tracking=0.3)
            yy -= row_pitch

        yy = row_y
        for label, value in right_fields:
            _text(c, col_r_x, yy, label, font=FONT, size=7.5, color=LABEL_BLUE)
            _text(c, col_r_x, yy - 13, value, font=BOLD, size=9.5, color=colors.black, tracking=0.3)
            yy -= row_pitch

        # ---- Machine Readable Zone. line1/line2 are always exactly 44 chars
        # (ICAO TD3), so tracking is solved to stretch that fixed-width string
        # across the full band instead of leaving it short of the right edge.
        mrz_h = 50.0
        mrz_margin = 16.0
        mrz_size = 15.0
        mrz_w = CARD_W - 2 * mrz_margin
        mrz_raw_w = pdfmetrics.stringWidth(line1, "Courier-Bold", mrz_size)
        mrz_tracking = max(0.0, (mrz_w - mrz_raw_w) / (len(line1) - 1))
        # Clip the fill to the card's own rounded outline -- a plain square-cornered
        # rect flush with the card's bottom edge otherwise pokes past the card's
        # rounded corners there.
        c.saveState()
        card_path = c.beginPath()
        card_path.roundRect(CARD_X, CARD_Y, CARD_W, CARD_H, 8)
        c.clipPath(card_path, stroke=0, fill=0)
        c.setFillColor(MRZ_BG)
        c.rect(CARD_X, CARD_Y, CARD_W, mrz_h, fill=1, stroke=0)
        c.restoreState()
        _text(c, CARD_X + mrz_margin, CARD_Y + 31, line1, font="Courier-Bold", size=mrz_size, color=colors.black, tracking=mrz_tracking)
        _text(c, CARD_X + mrz_margin, CARD_Y + 12, line2, font="Courier-Bold", size=mrz_size, color=colors.black, tracking=mrz_tracking)

        _text_centred(c, PAGE_W / 2, CARD_Y - 22,
                      "",
                      size=7, color=colors.HexColor("#8a8a8a"))

    _save_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {"output_path": output_path, "passport_no": passport_no, "name": full_name}


# --------------------------------------------------------------------------
# Mastersheet-driven persona
# --------------------------------------------------------------------------

def _rows(ws) -> list[dict]:
    header = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        out.append(dict(zip(header, row)))
    return out


def build_persona(row: dict) -> dict:
    individual_id = str(row.get("Individual ID") or "")
    rng = seeded_rng(f"{individual_id}|passport")

    name = str(row.get("Individual Name (auto)") or "")
    surname, given_names = _split_name(name)
    dob = as_date(row.get("Date of Birth (auto)")) or date(1980, 1, 1)

    issue_date = date(rng.randint(2019, 2025), rng.randint(1, 12), rng.randint(1, 28))
    expiry_date = date(issue_date.year + 10, issue_date.month, issue_date.day)

    # Despite the column name, "NRIC/Passport No. (auto)" always holds the person's
    # NRIC (every case's sample data uses the S/T-prefixed NRIC format here, even on
    # Passport-template rows) -- that's the right value for "National ID No" on the
    # passport page, not "Passport No", which needs its own distinct number.
    national_id_no = str(row.get("NRIC/Passport No. (auto)") or "")

    return dict(
        passport_no=f"{rng.choice('EK')}{rng.randint(1000000, 9999999)}{rng.choice('ABCDEFGHIZJ')}",
        surname=surname,
        given_names=given_names,
        nationality=str(row.get("Nationality (Auto)") or "Singaporean"),
        date_of_birth=dob,
        place_of_birth="Singapore",
        sex=rng.choice(["M", "F"]),
        date_of_issue=issue_date,
        date_of_expiry=expiry_date,
        national_id_no=national_id_no,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--cases", nargs="*", help="case IDs to include; default is all")
    ap.add_argument("--individuals", nargs="*", help="individual IDs to include; default is all")
    ap.add_argument("--no-synthetic-tag", action="store_true", help="omit the SYNTHETIC-TEST-DATA metadata tag")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(args.xlsx, data_only=True)

    count = 0
    for row in _rows(wb["NRIC-Passport"]):
        case_id = str(row.get("Case ID (auto)") or "")
        individual_id = str(row.get("Individual ID") or "")
        if args.cases and case_id not in args.cases:
            continue
        if args.individuals and individual_id not in args.individuals:
            continue
        if "passport" not in str(row.get("Template") or "").lower():
            continue

        persona = build_persona(row)
        out_name = row.get("Filename") or f"{individual_id}_Passport.pdf"
        result = generate_passport_page(
            str(outdir / out_name),
            synthetic_tag=not args.no_synthetic_tag,
            **persona,
        )
        count += 1
        print(f"{individual_id}: {result['name']} -> {result['output_path']}")

    print(f"wrote {count} passport page(s)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        out_dir = Path.cwd() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        result = generate_passport_page(
            str(out_dir / "passport_template.pdf"),
            passport_no="K0419047N",
            surname="Tan",
            given_names="Wei Ming",
            nationality="Singaporean",
            date_of_birth=date(1980, 1, 1),
            place_of_birth="Singapore",
            sex="M",
            date_of_issue=date(2024, 1, 15),
            date_of_expiry=date(2034, 1, 14),
            national_id_no="S8012345A",
        )
        print(f"wrote {result['output_path']}")
