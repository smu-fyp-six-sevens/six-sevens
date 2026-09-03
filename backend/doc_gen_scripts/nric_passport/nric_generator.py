"""
Generates an NRIC template PDF with a fixed, visual layout matching the
reference image: grey page background, centered title, two pink cards,
photo placeholder, barcode area, and address block.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas


PAGE_W, PAGE_H = A4
FONT = "Helvetica"


def _save_pdf(output_path: str, draw_fn) -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = canvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({"/Producer": "DocuSuite Enterprise 4.2", "/Creator": "DocuSuite Enterprise 4.2", "/CreationDate": creation})
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, width: float, *, font: str = FONT, size: float = 9, leading: float = 11, color=colors.black, bold: bool = False) -> float:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if pdfmetrics.stringWidth(trial, f"{font}-Bold" if bold else font, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    c.setFillColor(color)
    c.setFont(f"{font}-Bold" if bold else font, size)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def _draw_label_value(c: canvas.Canvas, x: float, y: float, label: str, value: str, *, label_w: float, value_w: float, label_size: float = 8, value_size: float = 10) -> float:
    c.setFillColor(colors.HexColor("#6f6f6f"))
    c.setFont(FONT, label_size)
    c.drawString(x, y, label.upper())
    c.setFillColor(colors.black)
    c.setFont(f"{FONT}-Bold", value_size)
    c.drawString(x, y - 13, value)
    c.setStrokeColor(colors.HexColor("#e2c4cf"))
    c.setLineWidth(0.6)
    c.line(x, y - 16, x + label_w + value_w, y - 16)
    return y - 22


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
    optional_fields: list[tuple[str, str]] | None = None,
) -> dict:
    optional_fields = optional_fields or [("Country of Birth", birth_place), ("Card Type", card_type), ("Status", "Active")]

    def draw(c: canvas.Canvas) -> None:
        c.setFillColor(colors.HexColor("#efefef"))
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 17)
        c.drawCentredString(PAGE_W / 2, PAGE_H - 42, "NRIC Template")

        # upper card
        card_x, card_w = 42, PAGE_W - 84
        card_y_top, card_h = 430, 245
        c.setFillColor(colors.HexColor("#f4b8ca"))
        c.roundRect(card_x, card_y_top, card_w, card_h, 10, fill=1, stroke=1)
        c.setStrokeColor(colors.HexColor("#d9b0bd"))
        c.roundRect(card_x, card_y_top, card_w, card_h, 10, fill=0, stroke=1)
        c.setFillColor(colors.HexColor("#cde3f4"))
        c.rect(card_x, card_y_top + card_h - 34, card_w, 34, fill=1, stroke=0)
        c.setStrokeColor(colors.HexColor("#8bb6d6"))
        c.line(card_x, card_y_top + card_h - 34, card_x + card_w, card_y_top + card_h - 34)

        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(card_x + 12, card_y_top + card_h - 18, "REPUBLIC OF SINGAPORE")
        c.setFont("Helvetica", 9)
        c.drawString(card_x + 12, card_y_top + card_h - 28, "Identification Card Number: ")
        c.setFont("Helvetica-Bold", 12)
        c.drawString(card_x + 140, card_y_top + card_h - 28, nric_no)

        # crest placeholder
        crest_x = card_x + card_w - 70
        crest_y = card_y_top + card_h - 72
        c.setFillColor(colors.HexColor("#f8d8d8"))
        c.circle(crest_x + 20, crest_y + 20, 18, fill=1, stroke=1)
        c.setFillColor(colors.HexColor("#d02c2c"))
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(crest_x + 20, crest_y + 18, "SG")
        c.setFillColor(colors.HexColor("#7a4c0d"))
        c.setFont("Helvetica", 6)
        c.drawCentredString(crest_x + 20, crest_y + 10, "Coat")

        # photo placeholder
        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#666666"))
        c.setDash(4, 3)
        c.rect(card_x + 12, card_y_top + 65, 88, 110, fill=1, stroke=1)
        c.setDash()
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 7)
        c.drawCentredString(card_x + 56, card_y_top + 118, "PHOTO")
        c.drawCentredString(card_x + 56, card_y_top + 108, "PLACEHOLDER")

        # right side fields
        x = card_x + 120
        y = card_y_top + 152
        y = _draw_label_value(c, x, y, "Name", name, label_w=220, value_w=180)
        y = _draw_label_value(c, x, y, "Race", race, label_w=220, value_w=180)
        y = _draw_label_value(c, x, y, "Date of Birth", date_of_birth.strftime("%d %b %Y"), label_w=220, value_w=180)
        y = _draw_label_value(c, x + 170, card_y_top + 118, "Sex", gender, label_w=70, value_w=80)
        y = _draw_label_value(c, x, card_y_top + 85, "Country/Place of Birth", birth_place, label_w=220, value_w=180)

        # lower card
        low_y = 120
        c.setFillColor(colors.HexColor("#f4b8ca"))
        c.roundRect(card_x, low_y, card_w, card_h, 10, fill=1, stroke=1)
        c.setStrokeColor(colors.HexColor("#d9b0bd"))
        c.roundRect(card_x, low_y, card_w, card_h, 10, fill=0, stroke=1)

        c.setFillColor(colors.white)
        c.setStrokeColor(colors.HexColor("#888888"))
        c.setDash(4, 3)
        c.rect(card_x + 18, low_y + 128, card_w - 36, 40, fill=1, stroke=1)
        c.setDash()
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont("Helvetica", 7)
        c.drawCentredString(card_x + card_w / 2, low_y + 145, "BARCODE PLACEHOLDER")

        c.setFillColor(colors.HexColor("#6f6f6f"))
        c.setFont(FONT, 8)
        c.drawString(card_x + 12, low_y + 105, "DATE OF ISSUE")
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 10)
        c.drawString(card_x + 12, low_y + 92, issue_date.strftime("%d %b %Y").upper())

        c.setStrokeColor(colors.HexColor("#e2c4cf"))
        c.line(card_x + 12, low_y + 82, card_x + card_w - 12, low_y + 82)

        c.setFillColor(colors.HexColor("#6f6f6f"))
        c.setFont(FONT, 7)
        c.drawString(card_x + 12, low_y + 64, "REGISTERED ADDRESS")
        c.setFillColor(colors.black)
        addr_y = low_y + 50
        for line in address_lines:
            c.setFont("Helvetica-Bold" if line == address_lines[0] else "Helvetica", 9)
            c.drawString(card_x + 12, addr_y, line)
            addr_y -= 12

        # optional fields on lower card bottom
        opt_x = card_x + 12
        opt_y = low_y + 18
        for label, value in optional_fields:
            c.setFillColor(colors.HexColor("#6f6f6f"))
            c.setFont(FONT, 7)
            c.drawString(opt_x, opt_y, label.upper())
            c.setFillColor(colors.black)
            c.setFont("Helvetica-Bold", 8.5)
            c.drawString(opt_x, opt_y - 10, value)
            opt_y -= 22

    _save_pdf(output_path, draw)
    return {"output_path": output_path, "name": name, "nric_no": nric_no}


if __name__ == "__main__":
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
        address_lines=["2 Orchard Turn", "#01-01", "238801"],
        issue_date=date(2025, 1, 1),
        expiry_date=date(2035, 1, 1),
        serial_no="SAMPLE123",
    ))
