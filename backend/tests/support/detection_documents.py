"""
Documents generated for the detection regression suites (pymupdf and Pillow), each carrying the anomaly its test
case injects: the Sprint 3 test files (C005, C028, C034...) aren't in the repository.
"""
from __future__ import annotations

import io
from collections.abc import Callable

import pymupdf
from PIL import Image, ImageDraw

from tests.support.detection_harness import FakeStore

# --- generated documents ----------------------------------------------------------
# Each builder writes a small native-text PDF; the tampered variant carries
# exactly the anomaly its test case injects into the real test document.


def _pdf(build: Callable[[pymupdf.Document], None]) -> bytes:
    doc = pymupdf.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


_LABELS = ["Basic salary", "Overtime pay", "Transport allowance", "Employee CPF", "Income tax", "Net pay"]
_AMOUNTS = ["$5,200.00", "$480.00", "$300.00", "$1,040.00", "$212.00", "$4,728.00"]


def payslip_pdf(*, font: dict[int, str] | None = None, size: dict[int, float] | None = None,
                underline: int | None = None) -> bytes:
    """A payslip's label/amount table; the overrides restyle one label (SCRUM-61's three tampers)."""

    def build(doc):
        page = doc.new_page(width=400, height=300)
        for i, (label, amount) in enumerate(zip(_LABELS, _AMOUNTS, strict=True)):
            y = 80 + i * 18
            label_font = (font or {}).get(i, "helv")
            label_size = (size or {}).get(i, 9)
            page.insert_text((50, y), label, fontsize=label_size, fontname=label_font)
            page.insert_text((250, y), amount, fontsize=9, fontname="helv")
            if i == underline:
                width = pymupdf.get_text_length(label, label_font, label_size)
                page.draw_line((50, y + 2), (50 + width, y + 2), width=0.6)

    return _pdf(build)


_CLAUSE = (
    "The registered office of the Company shall be at such place in Singapore "
    "as the directors may from time to time determine, and the Company shall "
    "keep its registers at that office in accordance with the Act."
)


def clauses_pdf(*, indent_third: bool = False, overlay: str | None = None) -> bytes:
    """Four body paragraphs; tampered by indenting one (SCRUM-62) or typing over one (SCRUM-63)."""

    def build(doc):
        page = doc.new_page(width=612, height=792)
        y = 90
        for i in range(4):
            left = 96 if indent_third and i == 2 else 72
            page.insert_textbox(pymupdf.Rect(left, y, 540, y + 70), _CLAUSE, fontsize=11, fontname="helv")
            y += 80
        if overlay:
            page.insert_text((150, 104), overlay, fontsize=11, fontname="helv")

    return _pdf(build)


_CREST = pymupdf.Rect(100, 100, 220, 240)


def _crest(width: int = 300, height: int = 350) -> Image.Image:
    image = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, width - 40, height - 90), fill=(220, 40, 50), outline=(30, 30, 30), width=4)
    draw.polygon([(width // 2, 70), (width // 2 + 40, 160), (width // 2 - 40, 160)], fill=(250, 250, 250))
    draw.rectangle((60, height - 80, width - 60, height - 60), fill=(200, 160, 30))
    return image


def _encode(image: Image.Image, fmt: str = "PNG", quality: int = 90) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=quality)
    return buffer.getvalue()


def id_card_pdf(*, replace_crest: bool = False) -> bytes:
    """An ID page with one crest; tampered by painting the crest over with a low-resolution JPEG copy."""

    def build(doc):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 72), "REPUBLIC OF SINGAPORE", fontsize=16)
        page.insert_image(_CREST, stream=_encode(_crest()))
        if replace_crest:
            cover = pymupdf.Rect(_CREST.x0 - 0.8, _CREST.y0 - 0.8, _CREST.x1 + 0.8, _CREST.y1 + 0.8)
            page.draw_rect(cover, color=None, fill=(1, 1, 1), overlay=True)
            low_res = _encode(_crest().resize((52, 60)), "JPEG", quality=16)
            page.insert_image(cover, stream=low_res, keep_proportion=False)

    return _pdf(build)


def image_only_pdf() -> bytes:
    """A page that is one picture: no text for the font and layout checks to read."""

    def build(doc):
        page = doc.new_page(width=200, height=200)
        page.insert_image(pymupdf.Rect(20, 20, 180, 120), stream=_encode(_crest()))

    return _pdf(build)


def certificate_pdf(company_name: str) -> bytes:
    def build(doc):
        page = doc.new_page(width=595, height=842)
        lines = [
            "ACCOUNTING AND CORPORATE REGULATORY AUTHORITY",
            "CERTIFICATE CONFIRMING INCORPORATION OF COMPANY",
            f"This is to confirm that {company_name}",
            "Registration No. 201634564N",
            "is incorporated under the Companies Act 1967 on 26 March 2016",
            "and that the company is a private company limited by shares.",
        ]
        for i, line in enumerate(lines):
            page.insert_text((72, 100 + i * 24), line, fontsize=11, fontname="helv")

    return _pdf(build)


# A credit report's embedded metadata (what ingestion's exiftool stored).
TAMPERED_METADATA = {
    "Author": "FraudTests",
    "Creator": "Adobe Acrobat Pro (64-bit) 24.1",
    "Producer": "Adobe Acrobat Pro (64-bit) 24.1",
}
CLEAN_METADATA = {
    "Producer": "Experian Credit Bureau Report Generator",
    "Creator": "Experian",
    "CreateDate": "2026:04:14 09:00:00",
    "ModifyDate": "2026:04:14 09:00:00",
}



def credit_report_with_font_and_metadata_tampering(store: FakeStore) -> str:
    """C028 in the live run: a restyled field and Acrobat/"FraudTests" metadata."""
    return store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(font={2: "cour"}),
                     metadata=TAMPERED_METADATA)
