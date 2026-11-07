"""
Bank statements as a bank prints them, for the cross-page checks (fraud_detection/pages): page numbers, the account
header on every page, and the balance brought and carried forward on the same row as its label.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from app.fraud_detection.font_consistency import ExtractedField

A4 = (595, 842)
_RIGHT = 540  # the balance column's right edge


@dataclass
class Statement:
    pdf: bytes
    transactions: list[dict] = field(default_factory=list)

    def fields(self, account: str = "012-345678-9", holder: str = "ACME TRADING PTE. LTD.") -> list[ExtractedField]:
        return [
            ExtractedField(field_name="account_number", field_type="string", field_value=account, confidence=0.95),
            ExtractedField(field_name="company_name", field_type="string", field_value=holder, confidence=0.95),
            ExtractedField(field_name="transactions", field_type="array", field_value=self.transactions, confidence=0.95),
        ]


def _right(page: pymupdf.Page, y: float, text: str, size: float = 9, font: str = "helv") -> None:
    x = _RIGHT - pymupdf.get_text_length(text, fontname=font, fontsize=size)
    page.insert_text((x, y), text, fontsize=size, fontname=font)


def make_statement(
    pages: int = 3, rows_per_page: int = 4, *, account: str = "012-345678-9", holder: str = "ACME TRADING PTE. LTD.",
    opening: float = 10_000.00, start_amount: float = 100.0, numbering: bool = True, carry: bool = True,
    page_sizes: dict[int, tuple[int, int]] | None = None, account_on_page: dict[int, str] | None = None,
    terms_page: bool = False, font_on_page: dict[int, str] | None = None,
) -> Statement:
    """A statement as a bank prints one: page numbers, the account header on every page, and the balance
    brought and carried forward on the same row as its label, in the balance column."""
    doc = pymupdf.open()
    balance = opening
    rows: list[dict] = []
    for number in range(1, pages + 1):
        width, height = (page_sizes or {}).get(number, A4)
        page = doc.new_page(width=width, height=height)
        font = (font_on_page or {}).get(number, "helv")
        if numbering:
            page.insert_text((470, 30), f"Page {number} of {pages + (1 if terms_page else 0)}", fontsize=8, fontname=font)
        page.insert_text((50, 80), f"Account No: {(account_on_page or {}).get(number, account)}", fontsize=9, fontname=font)
        page.insert_text((50, 95), f"Account Holder: {holder}", fontsize=9, fontname=font)
        y = 140
        if carry:
            page.insert_text((50, y), "Balance Brought Forward", fontsize=9, fontname=font)
            _right(page, y, f"{balance:,.2f}", font=font)
            y += 18
        for row in range(rows_per_page):
            amount = round(start_amount + number * 10 + row, 2)
            balance = round(balance + amount, 2)
            date = f"{number:02d}-Jun-26"
            description = f"GIRO PAYMENT {number}{row}"
            page.insert_text((50, y), f"{date}  {description}", fontsize=9, fontname=font)
            page.insert_text((330, y), f"{amount:,.2f}", fontsize=9, fontname=font)
            _right(page, y, f"{balance:,.2f}", font=font)
            rows.append({"date": date, "description": description, "amount": amount, "balance": balance})
            y += 18
        if carry:
            page.insert_text((50, y), "Balance Carried Forward", fontsize=9, fontname=font)
            _right(page, y, f"{balance:,.2f}", font=font)
    if terms_page:
        page = doc.new_page(width=420, height=595)  # a smaller insert at the back, as some banks print
        page.insert_text((50, 60), "Terms and conditions apply. Please check this statement carefully.", fontsize=8)
    pdf = doc.tobytes()
    doc.close()
    return Statement(pdf, rows)
