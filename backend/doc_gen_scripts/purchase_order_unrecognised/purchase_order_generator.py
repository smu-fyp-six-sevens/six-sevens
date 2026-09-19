#!/usr/bin/env python3
"""Fill Fashion-Purchase-Order-Template.pdf from the document mastersheet.

Reads the Case Registry and Purchase Order tabs out of
``vN_document_mastersheet.xlsx``. The version digit is not hardcoded: with no
path argument the latest ``vN_document_mastersheet.xlsx`` in this package is
used, so v16 → v17 is just dropping in the new file.

Usage:
    python purchase_order_generator.py
    python purchase_order_generator.py ../v16_document_mastersheet.xlsx
    python purchase_order_generator.py ../v16_document_mastersheet.xlsx --cases C001 C002
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path


warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

_DEPS = Path(__file__).resolve().parent / ".pydeps"
if _DEPS.exists():
    sys.path.insert(0, str(_DEPS))

try:
    import openpyxl
    import pymupdf
except ImportError as exc:
    raise SystemExit(
        "openpyxl and pymupdf are required. Install them with:\n"
        "  pip install openpyxl pymupdf --target \".pydeps\"\n"
    ) from exc


WORKSPACE = Path(__file__).resolve().parent.parent
TEMPLATE_CANDIDATES = [
    Path(__file__).resolve().parent / "Fashion-Purchase-Order-Template.pdf",
]
MASTERSHEET_NAME = re.compile(r"^v(\d+)_document_mastersheet\.xlsx$", re.I)
PO_SHEET_NAMES = (
    "Purchase Order (Unrecognised Documents)",
    "Purchase Order (Unrecognised)",
    "Purchase Order",
)
ITEM_RE = re.compile(r"(.+?)\s+x(\d+)\s+@\s+\$([0-9,]+\.\d{2})$")
MONTHS = [
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]
SHIPPING = [
    ("Road", "YCH Transport"),
    ("Courier", "DHL Express"),
    ("Sea Freight", "PIL"),
    ("Road", "Toll Logistics"),
]

# Data-row bands between the table grid lines.
ROW_BANDS = [
    (321.46, 340.90),
    (341.38, 361.06),
    (361.54, 381.00),
    (381.48, 400.92),
    (401.40, 421.08),
    (421.56, 441.00),
    (441.48, 460.94),
    (461.42, 481.10),
]
COLS = {
    "name": (85.0, 268.0, 0),
    "sku": (270.0, 323.0, 1),
    "price": (324.0, 373.0, 2),
    "qty": (375.0, 409.0, 1),
    "total": (412.0, 528.0, 2),
}
# Template ships with 4 sample line items. Only those rows have text to erase.
SAMPLE_ROWS = 4
TABLE_GRID_X = (82.58, 269.86, 323.88, 374.30, 410.30, 529.15)
TABLE_GRID_Y = (320.98, 340.90, 361.06, 381.00, 400.92, 421.08, 441.00, 460.94, 481.10)
TABLE_GRID_THICK = 0.48
TABLE_GRID_GREY = (0.667, 0.667, 0.667)
TABLE_TOTAL_X = 410.54
YELLOW = (0.984, 0.910, 0.722)
BODY = 9.6
SMALL = 7.9
HEADER = 13.0


def template_path() -> Path:
    for path in TEMPLATE_CANDIDATES:
        if path.exists():
            return path
    raise SystemExit("Fashion-Purchase-Order-Template.pdf was not found.")


def find_mastersheet(explicit: str | Path | None = None) -> Path:
    """Resolve a mastersheet path without baking in v16 vs v17."""
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.exists():
            raise SystemExit(f"Mastersheet not found: {path}")
        return path
    ranked: list[tuple[int, Path]] = []
    for path in WORKSPACE.glob("v*_document_mastersheet.xlsx"):
        match = MASTERSHEET_NAME.match(path.name)
        if match:
            ranked.append((int(match.group(1)), path))
    if not ranked:
        raise SystemExit(
            f"No vN_document_mastersheet.xlsx found in {WORKSPACE}. "
            "Pass the xlsx path explicitly."
        )
    ranked.sort()
    return ranked[-1][1]


def _cell_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _sheet_rows(ws) -> list[dict[str, str]]:
    header = [_cell_str(cell.value) for cell in ws[1]]
    rows: list[dict[str, str]] = []
    for raw in ws.iter_rows(min_row=2, values_only=True):
        if not any(raw):
            continue
        rows.append({key: _cell_str(value) for key, value in zip(header, raw) if key})
    return rows


def _open_sheet(wb, *names: str):
    """Match a tab even when Excel truncates the name at 31 characters."""
    for name in names:
        if name in wb.sheetnames:
            return wb[name]
        truncated = name[:31]
        matches = [
            sheet
            for sheet in wb.sheetnames
            if sheet == truncated or sheet.startswith(name) or name.startswith(sheet)
        ]
        if len(matches) == 1:
            return wb[matches[0]]
        if truncated in matches:
            return wb[truncated]
        if matches:
            return wb[max(matches, key=len)]
    raise SystemExit(
        f"Mastersheet is missing one of {names!r}. Found: {', '.join(wb.sheetnames)}"
    )


def load_mastersheet(xlsx: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    po_rows = [
        row
        for row in _sheet_rows(_open_sheet(wb, *PO_SHEET_NAMES))
        if row.get("Case ID") and row.get("Company Name (auto)")
    ]
    cases = {
        row["Case ID"]: row
        for row in _sheet_rows(_open_sheet(wb, "Case Registry"))
        if row.get("Case ID")
    }
    return po_rows, cases


def parse_money(value: str | None) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace("$", "").replace(",", "").replace('"', "")
    if not text:
        return 0.0
    return round(float(text), 2)


def parse_percent(value: str | None) -> float:
    if value is None:
        return 0.0
    n = float(str(value).strip().replace("%", "") or 0)
    if 0 < n <= 1:
        return n * 100.0
    return n


def parse_date(value: str) -> date:
    text = (value or "").strip()
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        pass
    try:
        serial = float(text)
    except ValueError as exc:
        raise ValueError(f"Cannot parse date: {value!r}") from exc
    return date(1899, 12, 30) + timedelta(days=int(serial))


def ordinal(day: int) -> str:
    if 10 <= day % 100 <= 20:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(day % 10, "TH")
    return f"{day}{suffix}"


def fmt_long_date(value: date) -> str:
    return f"{ordinal(value.day)} {MONTHS[value.month - 1]}, {value.year}"


def fmt_short_date(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year}"


def fmt_amount(value: float, cents: bool = False) -> str:
    if cents or abs(value - round(value)) > 0.001:
        return f"{value:,.2f}"
    return f"{int(round(value)):,}"


def fmt_percent(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return f"{int(round(value))}%"
    return f"{value:.2f}%"


def fmt_template_money(value: float) -> str:
    """Match the template's European-style amounts, e.g. 1,080.00$."""
    return f"{value:,.2f}$"


def split_sg_address(address: str) -> tuple[str, str]:
    text = (address or "").strip()
    marker = ", Singapore "
    if marker in text:
        street, postal = text.rsplit(marker, 1)
        return street.strip(), f"Singapore {postal.strip()}"
    return text, "Singapore"


def parse_items(summary: str, po_number: str) -> list[dict]:
    items: list[dict] = []
    for index, raw in enumerate((summary or "").split(";"), start=1):
        part = raw.strip().strip('"')
        if not part:
            continue
        match = ITEM_RE.match(part)
        if not match:
            raise RuntimeError(f"Cannot parse order item: {part!r}")
        name = match.group(1).strip()
        qty = int(match.group(2))
        price = float(match.group(3).replace(",", ""))
        letters = "".join(ch for ch in name.upper() if ch.isalpha())[:4] or "ITEM"
        digits = re.sub(r"\D", "", po_number)[-4:] or "0000"
        items.append({
            "name": name,
            "sku": f"{letters}{digits}{index}",
            "price": price,
            "qty": qty,
            "total": round(qty * price, 2),
        })
    if not items:
        raise RuntimeError("Order has no line items.")
    if len(items) > len(ROW_BANDS):
        raise RuntimeError(f"Order has {len(items)} lines; template holds {len(ROW_BANDS)}.")
    return items


def vendor_email(vendor: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", vendor.lower())[:18] or "supplier"
    return f"sales@{slug}.com.sg"


def vendor_phone(case_id: str) -> str:
    n = int(case_id[1:])
    return f"+65 {6200 + (n * 17) % 700:04d} {1000 + (n * 91) % 9000:04d}"


def shipping_for(case_id: str) -> tuple[str, str]:
    return SHIPPING[int(case_id[1:]) % len(SHIPPING)]


def tracking_for(po_number: str, case_id: str) -> str:
    digits = re.sub(r"\D", "", po_number + case_id)
    return f"12{digits.ljust(14, '0')[:14]}"


def build_record(row: dict[str, str], cases: dict[str, dict[str, str]]) -> dict:
    case_id = row["Case ID"]
    case = cases.get(case_id, {})
    po_number = row["PO Number"].strip()
    po_date = parse_date(row["PO Date"])
    items = parse_items(row.get("Order Items Summary") or "", po_number)
    subtotal = parse_money(row.get("Subtotal (SGD)"))
    discount_pct = parse_percent(row.get("Discount (%)"))
    tax_pct = parse_percent(row.get("Tax (%)"))
    shipping = parse_money(row.get("Shipping (SGD)"))
    total = parse_money(row.get("Total (SGD)"))
    line_sum = round(sum(item["total"] for item in items), 2)
    if abs(line_sum - subtotal) > 0.01:
        raise RuntimeError(f"{case_id}: line items {line_sum} != subtotal {subtotal}")

    company = row["Company Name (auto)"].strip()
    address = case.get("Registered Address") or ""
    street, city = split_sg_address(address)
    phone = (case.get("Company Contact No.") or row.get("Client Contact No. (auto)") or "").strip()
    bank = (case.get("Bank Name") or "DBS Bank").strip()
    method, carrier = shipping_for(case_id)
    arrival = po_date + timedelta(days=14)
    contact = (row.get("Client Contact Person (auto)") or "Purchasing").strip()
    vendor = row["Vendor Name"].strip()
    return {
        "case_id": case_id,
        "company": company,
        "uen": row.get("UEN (auto)", ""),
        "street": street,
        "city": city,
        "phone": phone,
        "vendor_phone": vendor_phone(case_id),
        "vendor_email": vendor_email(vendor),
        "contact": contact,
        "bank": bank,
        "supplier": vendor,
        "supplier_address": row["Vendor Address"].strip(),
        "po_number": po_number,
        "po_date": fmt_long_date(po_date),
        "po_date_iso": po_date.isoformat(),
        "arrival": fmt_short_date(arrival),
        "method": method,
        "carrier": carrier,
        "track": tracking_for(po_number, case_id),
        "payment": row["Payment Type"].strip(),
        "lines": items,
        "subtotal": subtotal,
        "discount_pct": discount_pct,
        "tax_pct": tax_pct,
        "shipping": shipping,
        "grand_total": total,
    }


def _hits(page: pymupdf.Page, text: str, count: int | None = None) -> list[pymupdf.Rect]:
    found = page.search_for(text)
    if not found:
        raise KeyError(f"Template text not found: {text!r}")
    if count is not None and len(found) < count:
        raise KeyError(f"Template text {text!r} found {len(found)} time(s), expected {count}.")
    return found


def _fit_size(text: str, width: float, fontsize: float, fontname: str, min_size: float = 6.0) -> float:
    font = pymupdf.Font(fontname)
    size = fontsize
    while size > min_size and font.text_length(text, fontsize=size) > width - 1:
        size -= 0.3
    return size


def _draw_left(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, fontsize: float, fontname: str) -> None:
    if not text:
        return
    size = _fit_size(text, rect.width, fontsize, fontname)
    baseline = rect.y0 + size * 0.82
    page.insert_text((rect.x0, baseline), text, fontsize=size, fontname=fontname, color=(0, 0, 0))


def _draw_aligned(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, fontsize: float, align: int, fontname: str = "helv") -> None:
    if not text:
        return
    size = _fit_size(text, rect.width - 2, fontsize, fontname)
    width = pymupdf.Font(fontname).text_length(text, fontsize=size)
    baseline = rect.y0 + (rect.height + size * 0.55) / 2
    if align == 2:
        x = rect.x1 - width - 2
    elif align == 1:
        x = rect.x0 + max(1, (rect.width - width) / 2)
    else:
        x = rect.x0 + 2
    page.insert_text((x, baseline), text, fontsize=size, fontname=fontname, color=(0, 0, 0))


def _cover_sample_table_text(page: pymupdf.Page, covers: list, yellow: tuple[float, float, float]) -> None:
    """Redact only the sample cell glyphs so the grid lines stay intact."""
    y_top = TABLE_GRID_Y[0]
    y_bot = TABLE_GRID_Y[SAMPLE_ROWS]
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span.get("text", "").strip():
                    continue
                rect = pymupdf.Rect(span["bbox"])
                if rect.y0 < y_top or rect.y1 > y_bot or rect.x0 < TABLE_GRID_X[0] or rect.x0 > TABLE_GRID_X[-1]:
                    continue
                padded = pymupdf.Rect(rect.x0 - 1.0, rect.y0 - 0.5, rect.x1 + 1.0, rect.y1 + 0.5)
                fill = yellow if rect.x0 >= TABLE_TOTAL_X else (1, 1, 1)
                covers.append((padded, fill))


def _redraw_table_grid(page: pymupdf.Page, rows: int = SAMPLE_ROWS) -> None:
    """Restore the grey cell borders that redaction can wipe out."""
    y_top = TABLE_GRID_Y[0] + TABLE_GRID_THICK
    y_bot = TABLE_GRID_Y[rows]
    for x in TABLE_GRID_X:
        page.draw_rect(
            pymupdf.Rect(x, y_top, x + TABLE_GRID_THICK, y_bot),
            color=None,
            fill=TABLE_GRID_GREY,
            width=0,
        )
    left = TABLE_GRID_X[0]
    right = TABLE_GRID_X[-1] + TABLE_GRID_THICK
    for y in TABLE_GRID_Y[: rows + 1]:
        page.draw_rect(
            pymupdf.Rect(left, y, right, y + TABLE_GRID_THICK),
            color=None,
            fill=TABLE_GRID_GREY,
            width=0,
        )


def fill_pdf(template: Path, record: dict, dest: Path) -> None:
    doc = pymupdf.open(template)
    try:
        page = doc[0]
        covers: list[tuple[pymupdf.Rect, tuple[float, float, float]]] = []
        draws: list[tuple] = []
        yellow = YELLOW

        def cover_hit(text: str, index: int = 0, x1: float | None = None, x0: float | None = None) -> pymupdf.Rect:
            hit = _hits(page, text)[index]
            rect = pymupdf.Rect(
                x0 if x0 is not None else hit.x0,
                hit.y0 - 0.35,
                x1 if x1 is not None else hit.x1 + 2.0,
                hit.y1 + 0.35,
            )
            covers.append((rect, (1, 1, 1)))
            return rect

        def replace(text: str, new: str, *, index: int = 0, size: float = BODY, fontname: str = "helv", x1: float | None = None, x0: float | None = None) -> None:
            rect = cover_hit(text, index, x1=x1, x0=x0)
            draws.append(("left", rect, new, size, fontname))

        replace("FASHION QUEEN", record["company"], size=HEADER, fontname="hebo", x1=330.0)
        replace("108 Waldorf Street", record["street"], x1=330.0)
        replace("Coventry, 25448 IL", record["city"], x1=330.0)
        replace("(000) 1234 56789", record["phone"], x1=330.0)
        replace("123456/22", record["po_number"], x1=575.0)
        replace("22ND SEPTEMBER, 2022", record["po_date"], x1=575.0)

        name_rect = pymupdf.Rect(97.0, 181.0, 565.0, 192.5)
        covers.append((name_rect, (1, 1, 1)))
        draws.append(("left", name_rect, f"{record['supplier']}    Attn. {record['contact']}", BODY, "helv"))
        replace("211 Arrow Bay, Westminster, 21656 Los Angeles", record["supplier_address"], x1=565.0)
        replace("info@fashionitems.com", record["vendor_email"], x1=290.0)
        replace("(555) 1234 56789", record["vendor_phone"], x1=500.0)

        _cover_sample_table_text(page, covers, yellow)
        for line, (y0, y1) in zip(record["lines"], ROW_BANDS):
            values = {
                "name": line["name"],
                "sku": line["sku"],
                "price": fmt_template_money(line["price"]),
                "qty": str(line["qty"]),
                "total": fmt_template_money(line["total"]),
            }
            for key, (x0, x1, align) in COLS.items():
                draws.append(("cell", pymupdf.Rect(x0, y0, x1, y1), values[key], SMALL, align))

        replace("Courier", record["method"], x1=270.0)
        replace("FedEx", record["carrier"], x1=270.0)
        replace("1222052520000680", record["track"], x1=270.0)
        replace("29/09/2022", record["arrival"], x1=270.0)
        replace("3,565.00$", fmt_template_money(record["subtotal"]), x1=420.0)
        replace("10%", fmt_percent(record["discount_pct"]), index=0, x1=530.0)
        replace("10%", fmt_percent(record["tax_pct"]), index=1, x1=370.0)
        replace("75.00$", fmt_template_money(record["shipping"]), x1=500.0)
        replace("Credit Card", record["payment"], x1=560.0)
        replace("3,604.35$", fmt_template_money(record["grand_total"]), size=10.6, fontname="hebo", x1=500.0)

        for rect, fill in covers:
            page.add_redact_annot(rect, fill=fill, cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        for kind, rect, text, size, extra in draws:
            if kind == "left":
                _draw_left(page, rect, text, fontsize=size, fontname=extra)
            else:
                _draw_aligned(page, rect, text, fontsize=size, align=extra)

        _redraw_table_grid(page)

        dest.parent.mkdir(parents=True, exist_ok=True)
        doc.save(dest, garbage=3, deflate=True)
    finally:
        doc.close()


def write_index(output_dir: Path, records: list[dict]) -> None:
    rows = []
    for rec in records:
        rows.append(
            "<tr>"
            f"<td><a href=\"{rec['filename']}\">{rec['filename']}</a></td>"
            f"<td>{rec['case_id']}</td>"
            f"<td>{rec['company']}</td>"
            f"<td>{rec['supplier']}</td>"
            f"<td>{rec['po_number']}</td>"
            f"<td>{rec['po_date']}</td>"
            f"<td>${fmt_amount(rec['grand_total'], cents=True)}</td>"
            f"<td>{rec['payment']}</td>"
            "</tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Generated Purchase Orders</title>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; margin: 40px; color: #222; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ padding: 10px 12px; border-bottom: 1px solid #ddd; text-align: left; }}
        th {{ font-size: 12px; letter-spacing: 0.4px; }}
        a {{ color: #1f4e79; }}
    </style>
</head>
<body>
    <h1>Corporate Loan Purchase Orders</h1>
    <p>{len(records)} POs generated from Fashion-Purchase-Order-Template.pdf</p>
    <table>
        <thead>
            <tr>
                <th>File</th>
                <th>Case</th>
                <th>Buyer</th>
                <th>Vendor</th>
                <th>PO #</th>
                <th>Date</th>
                <th>Total</th>
                <th>Payment</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def generate(
    output_dir: Path,
    xlsx: str | Path | None = None,
    cases: list[str] | None = None,
) -> list[dict]:
    mastersheet = find_mastersheet(xlsx)
    rows, case_registry = load_mastersheet(mastersheet)
    if cases:
        wanted = {item.strip() for item in cases}
        rows = [row for row in rows if row.get("Case ID") in wanted]
    if not rows:
        raise SystemExit(f"No purchase-order rows found in {mastersheet.name}.")
    template = template_path()
    output_dir.mkdir(parents=True, exist_ok=True)

    for stale in output_dir.glob("C*_PurchaseOrder.pdf"):
        stale.unlink()
    for stale_name in ("index.html", "manifest.json"):
        stale = output_dir / stale_name
        if stale.exists():
            stale.unlink()

    records: list[dict] = []
    for row in rows:
        record = build_record(row, case_registry)
        filename = f"{record['case_id']}_PurchaseOrder.pdf"
        record["filename"] = filename
        fill_pdf(template, record, output_dir / filename)
        records.append(record)

    public = []
    for rec in records:
        public.append({key: value for key, value in rec.items()})
    (output_dir / "manifest.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    write_index(output_dir, records)
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate purchase orders from the document mastersheet."
    )
    parser.add_argument(
        "xlsx",
        nargs="?",
        help="Mastersheet path. Default: latest vN_document_mastersheet.xlsx next to this package.",
    )
    parser.add_argument(
        "-o", "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--cases",
        nargs="*",
        help="Case IDs to include; default is every Purchase Order row.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = generate(args.out, xlsx=args.xlsx, cases=args.cases)
    print(f"Generated {len(records)} purchase orders in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
