#!/usr/bin/env python3
"""Fill the no-logo purchase-order PDF for every purchase-order case."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


_DEPS = Path(__file__).resolve().parent / ".pydeps"
if _DEPS.exists():
    sys.path.insert(0, str(_DEPS))

try:
    from pypdf import PdfReader, PdfWriter
except ImportError as exc:
    raise SystemExit(
        "pypdf is required. Install it with:\n"
        "  pip install pypdf --target \".pydeps\"\n"
    ) from exc


WORKSPACE = Path(__file__).resolve().parent.parent
TEMPLATE_CANDIDATES = [
    WORKSPACE / "Fashion-Purchase-Order-No-Logo.pdf",
    WORKSPACE / "Fashion-Purchase-Order-Phone-Only.pdf",
    Path(__file__).resolve().parent / "Fashion-Purchase-Order-Phone-Only.pdf",
]
PO_SHEET = WORKSPACE / (
    "v8_document_mastersheet.xlsx - Purchase Order (Unrecognised Documents) (1).csv"
)
CASE_REGISTRY = WORKSPACE / "v8_document_mastersheet.xlsx - Case Registry.csv"

LINE_ROWS = [
    (11, 12, 13, 14, 15, 16, 17),
    (18, 19, 20, 21, 22, 23, 24),
    (25, 26, 27, 28, 29, 30, 31),
    (32, 33, 34, 35, 36, 37, 38),
    (39, 40, 41, 42, 43, 44, 45),
    (46, 47, 48, 49, 50, 51, 52),
    (53, 54, 55, 56, 57, 58, 59),
    (60, 61, 62, 63, 64, 65, 66),
    (67, 68, 69, 70, 71, 72, 73),
]
MONTHS = [
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
]
CLIENT_PHONE_RECT = [76.4, 524.1, 331.7, 542.3]
# Client Name / Address / Phone values.
VALUE_X = 96.0
# Company header: same inset from the left edge as PO # / date from the right.
HEADER_X = 42.0
HEADER_FIELDS = {"Text3", "Text4", "Text5", "Text6"}
CLIENT_FIELDS = {"Text7", "Text8"}
ITEM_RE = re.compile(r"(.+?)\s+x(\d+)\s+@\s+\$([0-9,]+\.\d{2})$")
SHIPPING = [
    ("Road", "YCH Transport"),
    ("Courier", "DHL Express"),
    ("Sea Freight", "PIL"),
    ("Road", "Toll Logistics"),
]


def template_path() -> Path:
    for path in TEMPLATE_CANDIDATES:
        if path.exists():
            return path
    raise SystemExit("Fashion-Purchase-Order-No-Logo.pdf was not found.")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


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
    return float(str(value).strip().replace("%", "") or 0)


def parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


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
            "size": "*",
            "colour": "*",
            "total": round(qty * price, 2),
        })
    if not items:
        raise RuntimeError("Order has no line items.")
    if len(items) > len(LINE_ROWS):
        raise RuntimeError(f"Order has {len(items)} lines; template holds {len(LINE_ROWS)}.")
    return items


def load_cases() -> dict[str, dict[str, str]]:
    if not CASE_REGISTRY.exists():
        return {}
    return {
        row["Case ID"]: row
        for row in read_csv(CASE_REGISTRY)
        if row.get("Case ID")
    }


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
    notes = (
        f"This purchase is to be financed under {bank} corporate facility "
        f"{bank.split()[0].upper()}/{po_date.year}/{int(case_id[1:]):05d}. "
        "The amount of the Purchase Order is the agreed fixed price and shall not be "
        "exceeded without prior written consent of the buyer and the financing bank."
    )
    return {
        "case_id": case_id,
        "company": company,
        "uen": row.get("UEN (auto)", ""),
        "street": street,
        "city": city,
        "phone": phone,
        "vendor_phone": vendor_phone(case_id),
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
        "notes": notes,
    }


def field_values(record: dict) -> dict[str, str]:
    fields = {
        "Text1": record["po_number"],
        "Text2": record["po_date"],
        "Text3": record["company"],
        "Text4": record["street"],
        "Text5": record["city"],
        "Text6": record["phone"],
        "Text7": f"{record['supplier']}    Attn. Sales Desk",
        "Text8": record["supplier_address"],
        "Text74": record["method"],
        "Text75": record["carrier"],
        "Text76": record["track"],
        "Text77": record["arrival"],
        "Text78": fmt_amount(record["subtotal"]),
        "Text79": fmt_percent(record["discount_pct"]),
        "Text80": fmt_percent(record["tax_pct"]),
        "Text81": fmt_amount(record["shipping"], cents=True),
        "Text82": record["payment"],
        "Text83": fmt_amount(record["grand_total"], cents=True),
        "Text84": record["notes"],
    }
    for row_ids in LINE_ROWS:
        for field_id in row_ids:
            fields[f"Text{field_id}"] = ""
    for line, row_ids in zip(record["lines"], LINE_ROWS):
        name_id, sku_id, price_id, qty_id, size_id, colour_id, total_id = row_ids
        fields[f"Text{name_id}"] = line["name"]
        fields[f"Text{sku_id}"] = line["sku"]
        fields[f"Text{price_id}"] = fmt_amount(line["price"]).replace(",", "")
        fields[f"Text{qty_id}"] = str(line["qty"])
        fields[f"Text{size_id}"] = line["size"]
        fields[f"Text{colour_id}"] = line["colour"]
        fields[f"Text{total_id}"] = fmt_amount(line["total"]).replace(",", "")
    return fields


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text_width(text: str, size: float) -> float:
    return size * 0.52 * len(text)


def wrap_lines(text: str, width: float, size: float) -> list[str]:
    if not text:
        return []
    max_chars = max(4, int((width - 4) / max(size * 0.52, 1)))
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= max_chars:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [text]


def fit_lines(text: str, width: float, height: float, size: float) -> tuple[list[str], float]:
    size = min(size, max(6.0, height - 2))
    while size >= 6.0:
        leading = size * 1.18
        allowed = max(1, int(height / leading))
        lines = wrap_lines(text, width, size)
        if len(lines) <= allowed and all(text_width(line, size) <= width - 2 for line in lines):
            return lines, size
        if allowed == 1 and text_width(text, size) <= width - 2:
            return [text], size
        size -= 0.4
    lines = wrap_lines(text, width, 6.0)
    allowed = max(1, int(height / (6.0 * 1.18)))
    if len(lines) > allowed:
        lines = lines[:allowed]
        if lines:
            lines[-1] = lines[-1].rstrip(".,;:") + "..."
    return lines, 6.0


def widget_specs(reader: PdfReader, values: dict[str, str]) -> list[dict]:
    specs = []
    annots = reader.pages[0].get("/Annots") or []
    for annot in annots:
        obj = annot.get_object()
        name = str(obj.get("/T") or "")
        if name not in values:
            continue
        rect = [float(item) for item in obj.get("/Rect")]
        da = str(obj.get("/DA") or "")
        match = re.search(r"(\d+(?:\.\d+)?)\s+Tf", da)
        size = float(match.group(1)) if match else 8.0
        align = int(obj.get("/Q") or 0)
        # Long buyer names overflow the left header if kept centered like the sample.
        if name in HEADER_FIELDS:
            align = 0
            width = rect[2] - rect[0]
            rect = [HEADER_X, rect[1], HEADER_X + width, rect[3]]
        elif name in CLIENT_FIELDS:
            align = 0
            rect = [VALUE_X, rect[1], rect[2], rect[3]]
        specs.append({
            "name": name,
            "text": values[name],
            "rect": rect,
            "size": size,
            "align": align,
            "cover": False,
            "text_x": HEADER_X if name in HEADER_FIELDS else (
                VALUE_X if name in CLIENT_FIELDS else None
            ),
        })
    return specs


def overlay_commands(specs: list[dict]) -> str:
    chunks = ["q"]
    for spec in specs:
        text = spec["text"]
        x1, y1, x2, y2 = spec["rect"]
        width, height = x2 - x1, y2 - y1
        if spec.get("cover"):
            chunks.append(f"1 1 1 rg {x1:.3f} {y1:.3f} {width:.3f} {height:.3f} re f")
            chunks.append("0 0 0 rg")
        if not text:
            continue
        lines, size = fit_lines(text, width, height, spec["size"])
        leading = size * 1.18
        block = leading * len(lines)
        y = y2 - ((height - block) / 2) - size
        y = min(y, y2 - size - 0.5)
        y = max(y, y1 + 0.5)
        for index, line in enumerate(lines):
            line_y = y - index * leading
            if spec["align"] == 2:
                x = max(x1 + 1, x2 - 3 - text_width(line, size))
            elif spec["align"] == 1:
                x = x1 + max(1, (width - text_width(line, size)) / 2)
            else:
                x = spec.get("text_x") or (x1 + 2)
            chunks.append(
                f"BT /helv {size:.1f} Tf 0 0 0 rg {x:.3f} {line_y:.3f} Td "
                f"({pdf_escape(line)}) Tj ET"
            )
    chunks.append("Q")
    return "\n".join(chunks) + "\n"


def append_page_content(writer: PdfWriter, page, commands: str) -> None:
    from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(commands.encode("latin-1"))
    ref = writer._add_object(stream)
    contents = page.get("/Contents")
    if contents is None:
        page[NameObject("/Contents")] = ref
        return
    raw = contents.get_object() if hasattr(contents, "get_object") else contents
    if isinstance(raw, ArrayObject):
        raw.append(ref)
    else:
        page[NameObject("/Contents")] = ArrayObject([contents, ref])


def fill_pdf(template: Path, record: dict, dest: Path) -> None:
    reader = PdfReader(str(template))
    writer = PdfWriter(clone_from=reader)
    available = set(reader.get_fields() or {})
    values = {key: value for key, value in field_values(record).items() if key in available}
    specs = widget_specs(reader, values)
    phone_rect = [70.0, CLIENT_PHONE_RECT[1], CLIENT_PHONE_RECT[2], CLIENT_PHONE_RECT[3]]
    specs.append({
        "name": "Text10",
        "text": record["vendor_phone"],
        "rect": phone_rect,
        "size": 11.0,
        "align": 0,
        "cover": True,
        "text_x": VALUE_X,
    })
    specs.append({
        "name": "Text10-old",
        "text": "",
        "rect": [408.0, 524.1, 566.3, 542.3],
        "size": 11.0,
        "align": 0,
        "cover": True,
    })
    writer.update_page_form_field_values(
        writer.pages[0],
        values,
        auto_regenerate=True,
        flatten=False,
    )
    page = writer.pages[0]
    append_page_content(writer, page, overlay_commands(specs))
    if "/Annots" in page:
        del page["/Annots"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        writer.write(handle)


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
    <p>{len(records)} POs generated from Fashion-Purchase-Order-No-Logo.pdf</p>
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


def generate(output_dir: Path) -> list[dict]:
    rows = [row for row in read_csv(PO_SHEET) if (row.get("Company Name (auto)") or "").strip()]
    if not rows:
        raise SystemExit("No purchase-order rows found in the mastersheet.")
    cases = load_cases()
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
        record = build_record(row, cases)
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
        description="Generate purchase orders for every mastersheet row."
    )
    parser.add_argument(
        "-o", "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Output directory (default: ./output)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = generate(args.out)
    print(f"Generated {len(records)} purchase orders in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
