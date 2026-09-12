#!/usr/bin/env python3
"""Fill budget-proposal-template.docx for every case in the registry."""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile


WORKSPACE = Path(__file__).resolve().parent.parent
TEMPLATE_CANDIDATES = [
    WORKSPACE / "budget-proposal-template.docx",
    Path(__file__).resolve().parent / "budget-proposal-template.docx",
]
CASE_REGISTRY = WORKSPACE / "v8_document_mastersheet.xlsx - Case Registry.csv"
BUDGET_SHEET = WORKSPACE / (
    "v8_document_mastersheet.xlsx - Budget Proposal (Unrecognised Documents).csv"
)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
OTHER_BANKS = ["DBS Bank", "OCBC Bank", "UOB", "HSBC", "Standard Chartered", "Maybank"]

PROFILES = {
    "warehouse": {
        "projects": [
            "Warehouse Expansion",
            "Racking & Fleet Upgrade",
            "Distribution Hub Fit-out",
        ],
        "expenses": [
            "Project management",
            "Civil & site works",
            "Engineering support",
            "Permits & insurance",
            "Marketing & launch",
            "Racking & MHE",
            "Lease deposits",
            "Parts & consumables",
            ("Working capital &", "contingency"),
        ],
    },
    "office": {
        "projects": [
            "Office Fit-out & IT Upgrade",
            "Working Capital Programme",
            "Systems Modernisation",
        ],
        "expenses": [
            "Project management",
            "Fit-out works",
            "IT consultancy",
            "Licences & insurance",
            "Staff communication",
            "IT systems & furniture",
            "Lease deposits",
            "Office supplies",
            ("Working capital &", "contingency"),
        ],
    },
    "industrial": {
        "projects": [
            "Production Line Expansion",
            "Plant Modernisation",
            "Capacity Upgrade",
        ],
        "expenses": [
            "Project management",
            "Plant civil works",
            "Process engineering",
            "Permits & insurance",
            "Customer transition",
            "Machinery & equipment",
            "Utility deposits",
            "Commissioning supplies",
            ("Working capital &", "contingency"),
        ],
    },
    "fnb": {
        "projects": [
            "Kitchen Expansion",
            "New Outlet Fit-out",
            "Central Kitchen Setup",
        ],
        "expenses": [
            "Project management",
            "Premises works",
            "Kitchen consultancy",
            "Licences & insurance",
            "Launch marketing",
            "Kitchen equipment",
            "Lease deposits",
            "Opening supplies",
            ("Working capital &", "contingency"),
        ],
    },
    "retail": {
        "projects": [
            "Flagship Store Fit-out",
            "Inventory Build Programme",
            "Store Network Refresh",
        ],
        "expenses": [
            "Project management",
            "Store works",
            "Visual merchandising",
            "Licences & insurance",
            "Launch advertising",
            "Fit-out & fixtures",
            "Lease deposits",
            "Pre-opening supplies",
            ("Working capital &", "contingency"),
        ],
    },
}

# Amount cells: (table, row, cell)
SOLE_INCOME = [(0, 2, 1), (0, 3, 1)]
SOLE_INCOME_TOTAL = (0, 4, 1)
SOLE_EXPENSES = [
    (0, 5, 1), (0, 7, 1), (0, 8, 1), (0, 9, 1), (0, 10, 1),
    (0, 11, 1), (0, 12, 1), (0, 13, 1), (0, 15, 1),
]
SOLE_EXPENSE_TOTAL = (0, 16, 1)
MULTI_INCOME = [(1, 2, 1), (1, 3, 1), (1, 4, 1), (1, 5, 1)]
MULTI_INCOME_TOTAL = (1, 6, 1)
MULTI_EXPENSES = [
    (1, 7, 1), (1, 9, 1), (1, 10, 1), (1, 11, 1), (1, 12, 1),
    (1, 13, 1), (1, 14, 1), (1, 15, 1), (1, 17, 1),
]
MULTI_EXPENSE_TOTAL = (1, 18, 1)


def template_path() -> Path:
    for path in TEMPLATE_CANDIDATES:
        if path.exists():
            return path
    raise SystemExit("budget-proposal-template.docx was not found.")


def parse_money(value: str | None) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace("$", "").replace(",", "").replace('"', "")
    if not text:
        return 0
    return int(round(float(text)))


def round_step(value: float, step: int = 500) -> int:
    return int(round(value / step) * step)


def split_amount(total: int, weights: tuple[float, ...] | list[float], step: int = 500) -> list[int]:
    if total == 0:
        return [0] * len(weights)
    raw = [max(weight, 0.0) for weight in weights]
    scale = sum(raw) or 1.0
    values = [round_step(total * weight / scale, step) for weight in raw]
    values[-1] += total - sum(values)
    if values[-1] < 0:
        donor = max(range(len(values) - 1), key=lambda i: values[i])
        values[donor] += values[-1]
        values[-1] = 0
    return values


def fmt_money(value: int) -> str:
    return f"${value:,}"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def infer_profile(company: str, label: str) -> str:
    text = f"{company} {label}".lower()
    if any(key in text for key in [
        "construction", "engineering", "steel", "metal", "civil",
        "precision", "manufacturing", "textile", "furniture",
        "renewable", "water treatment", "aviation",
    ]):
        return "industrial"
    if any(key in text for key in [
        "hospitality", "f&b", "bakery", "beauty", "wellness",
        "spa", "fitness", "childcare",
    ]):
        return "fnb"
    if any(key in text for key in ["retail", "store"]):
        return "retail"
    if any(key in text for key in [
        "consulting", "technology", "media", "data", "security",
        "property", "pharma", "research",
    ]):
        return "office"
    return "warehouse"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"CSV not found: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def load_budget_rows() -> dict[str, dict[str, str]]:
    if not BUDGET_SHEET.exists():
        return {}
    return {row["Case ID"]: row for row in read_csv(BUDGET_SHEET) if row.get("Case ID")}


def fallback_budget(loan: int) -> dict[str, int]:
    owner = round_step(loan * 0.15, 1_000)
    total = loan + owner
    working = round_step(total * 0.45, 500)
    equipment = round_step(total * 0.30, 500)
    marketing = round_step(total * 0.15, 500)
    contingency = total - working - equipment - marketing
    return {
        "loan": loan,
        "owner": owner,
        "total": total,
        "working_capital": working,
        "equipment": equipment,
        "marketing": marketing,
        "contingency": contingency,
        "purpose": "Business Expansion & Working Capital",
    }


def budget_from_sheet(row: dict[str, str], loan: int) -> dict[str, int | str]:
    owner = parse_money(row.get("Owner Contribution (SGD)"))
    total = parse_money(row.get("Total Projected Income (SGD)"))
    working = parse_money(row.get("Working Capital (SGD)"))
    equipment = parse_money(row.get("Equipment & Renovation (SGD)"))
    marketing = parse_money(row.get("Marketing & Advertising (SGD)"))
    contingency = parse_money(row.get("Contingency Reserve (SGD)"))
    expenses = parse_money(row.get("Total Projected Expenses (SGD)"))
    sheet_loan = parse_money(row.get("Loan Amount Applied (SGD) (auto)")) or loan
    if not total:
        total = sheet_loan + owner
    if expenses and expenses != total:
        total = expenses
    if working + equipment + marketing + contingency != total:
        raise RuntimeError(
            f"{row.get('Case ID')}: expense lines "
            f"{working + equipment + marketing + contingency} != total {total}"
        )
    if sheet_loan + owner != total:
        raise RuntimeError(
            f"{row.get('Case ID')}: loan {sheet_loan} + owner {owner} != total {total}"
        )
    return {
        "loan": sheet_loan,
        "owner": owner,
        "total": total,
        "working_capital": working,
        "equipment": equipment,
        "marketing": marketing,
        "contingency": contingency,
        "purpose": row.get("Proposal Purpose") or "Business Expansion & Working Capital",
    }


def split_expenses(budget: dict, profile_name: str) -> list[int]:
    equipment_parts = split_amount(budget["equipment"], (0.12, 0.28, 0.18, 0.42))
    working_parts = split_amount(budget["working_capital"], (0.18, 0.12, 0.70))
    contingency_parts = split_amount(budget["contingency"], (0.30, 0.70))
    marketing = budget["marketing"]
    values = [
        equipment_parts[0],
        equipment_parts[1],
        equipment_parts[2],
        contingency_parts[0],
        marketing,
        equipment_parts[3],
        working_parts[0],
        working_parts[1],
        working_parts[2] + contingency_parts[1],
    ]
    if sum(values) != budget["total"]:
        values[-1] += budget["total"] - sum(values)
    _ = profile_name
    return values


def other_bank(bank: str, rng: random.Random) -> str:
    choices = [item for item in OTHER_BANKS if item.split()[0] != bank.split()[0]]
    return rng.choice(choices or OTHER_BANKS)


def build_record(case: dict[str, str], budget_rows: dict[str, dict[str, str]]) -> dict:
    case_id = case["Case ID"]
    company = case["Company Name"].strip()
    rng = random.Random(int(case_id[1:]))
    loan = parse_money(case.get("Loan Amount Applied (SGD)"))
    budget = (
        budget_from_sheet(budget_rows[case_id], loan)
        if case_id in budget_rows
        else fallback_budget(loan)
    )
    profile_name = infer_profile(company, case.get("Case Label") or "")
    profile = PROFILES[profile_name]
    expenses = split_expenses(budget, profile_name)
    labels = profile["expenses"]
    project = profile["projects"][int(case_id[1:]) % len(profile["projects"])]
    bank = (case.get("Bank Name") or "DBS Bank").strip()
    alt_bank = other_bank(bank, rng)

    owner = budget["owner"]
    facility = budget["loan"]
    multi_company = round_step(owner * 0.45, 1_000)
    multi_shareholder = owner - multi_company
    if multi_shareholder <= 0:
        multi_company = max(0, owner - 1_000)
        multi_shareholder = owner - multi_company
    multi_loan = round_step(facility * 0.80, 1_000)
    multi_trade = facility - multi_loan
    if multi_trade < 10_000:
        multi_loan = facility - 10_000
        multi_trade = 10_000

    return {
        "case_id": case_id,
        "company": company,
        "uen": case.get("Company UEN / Reg No.", ""),
        "address": case.get("Registered Address", ""),
        "applicant": case.get("Applicant Name (Director & UBO)", ""),
        "bank": bank,
        "other_bank": alt_bank,
        "profile": profile_name,
        "project": project,
        "purpose": budget["purpose"],
        "loan": budget["loan"],
        "owner": budget["owner"],
        "total": budget["total"],
        "working_capital": budget["working_capital"],
        "equipment": budget["equipment"],
        "marketing": budget["marketing"],
        "contingency": budget["contingency"],
        "expenses": expenses,
        "expense_labels": labels,
        "sole_income": [budget["owner"], budget["loan"]],
        "multi_income": [multi_company, multi_loan, multi_shareholder, multi_trade],
        "sole_income_labels": [
            "Owner contribution",
            f"Term loan - {bank}",
        ],
        "multi_income_labels": [
            "Company cash contribution",
            f"Term loan - {bank}",
            "Shareholder loan",
            f"Trade / WC line - {alt_bank}",
        ],
        "fraud_case": case.get("Fraud Case? (Y/N)", "N"),
    }


def register_namespaces(xml: str) -> None:
    start = xml.find("<w:document")
    if start < 0:
        start = xml.find("<")
    end = xml.find(">", start) + 1
    head = xml[start:end]
    for prefix, uri in re.findall(r'xmlns:([A-Za-z0-9]+)="([^"]+)"', head):
        ET.register_namespace(prefix, uri)
    default = re.search(r'\sxmlns="([^"]+)"', head)
    if default:
        ET.register_namespace("", default.group(1))
    ET.register_namespace("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main")


def set_cell_text(tables: list[ET.Element], loc: tuple[int, int, int], text: str) -> None:
    table_i, row_i, cell_i = loc
    cell = tables[table_i].findall(f"{W}tr")[row_i].findall(f"{W}tc")[cell_i]
    nodes = list(cell.iter(f"{W}t"))
    if not nodes:
        para = cell.find(f"{W}p")
        if para is None:
            para = ET.SubElement(cell, f"{W}p")
        run = ET.SubElement(para, f"{W}r")
        node = ET.SubElement(run, f"{W}t")
        node.text = text
        return
    nodes[0].text = text
    if text[:1] == " " or text.endswith(" "):
        nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for extra in nodes[1:]:
        extra.text = ""


def replace_text_once(root: ET.Element, old: str, new: str) -> None:
    for node in root.iter(f"{W}t"):
        if node.text and old in node.text:
            node.text = node.text.replace(old, new, 1)
            return
    raise KeyError(f"Template text not found: {old!r}")


def replace_text_all(root: ET.Element, old: str, new: str) -> int:
    count = 0
    for node in root.iter(f"{W}t"):
        if node.text and old in node.text:
            node.text = node.text.replace(old, new)
            count += 1
    return count


def fill_document(template_xml: str, record: dict) -> bytes:
    register_namespaces(template_xml)
    root = ET.fromstring(template_xml)
    tables = list(root.iter(f"{W}tbl"))
    if len(tables) < 2:
        raise RuntimeError("Template does not contain both budget tables.")

    replace_text_all(root, "Scallop Beach Art Gallery", record["company"])
    replace_text_all(root, "Snails & Scales Exhibit", record["project"])
    replace_text_once(root, "Proposal Budget - BCF as sole funder example", "Proposal Budget - Term loan as sole facility")
    replace_text_once(root, "Proposal Budget - Multiple funder example", "Proposal Budget - Combined funding sources")
    replace_text_once(root, "Exhibition Sales", record["sole_income_labels"][0])
    replace_text_once(root, "Community Foundation Grant", record["sole_income_labels"][1])
    replace_text_once(root, "Exhibition Sales", record["multi_income_labels"][0])
    replace_text_once(root, "Community Foundation Grant", record["multi_income_labels"][1])
    replace_text_once(root, "Patron donations", record["multi_income_labels"][2])
    replace_text_once(root, "Corporate sponsors", record["multi_income_labels"][3])

    labels = record["expense_labels"]
    for original, label in [
        ("Exhibition coordination", labels[0]),
        ("Installation", labels[1]),
        ("Curatorial support", labels[2]),
        ("Registration", labels[3]),
        ("Publicity & Advertising", labels[4]),
        ("Display", labels[5]),
        ("Rent", labels[6]),
        ("Supplies", labels[7]),
        ("Consignment payment to", labels[8][0]),
        ("artists", labels[8][1]),
    ]:
        replace_text_once(root, original, label)
        replace_text_once(root, original, label)

    replace_text_all(
        root,
        "We request that you use a spreadsheet in this format.  Of course, your line items will differ from the sample.",
        "",
    )
    replace_text_all(
        root,
        'You may find it helpful to use the "actual expense" column to track your expenses during the grant period.',
        "",
    )
    replace_text_all(
        root,
        "This will be required in submitting your Grant Final Report.",
        "",
    )
    replace_text_all(
        root,
        "If during the grant period, any actual expense differs from the budgeted expense by 10%",
        "If during the facility period, any actual expense differs from the budgeted expense by 10%",
    )
    replace_text_all(
        root,
        "you must notify BCF's Grant Manager as soon as possible since this may indicate a change in the purpose of the grant, and may require BCF Board approval.",
        f"you must notify {record['bank']}'s relationship manager as soon as possible since this may indicate a change in the purpose of the facility, and may require credit committee approval.",
    )

    for loc, amount in zip(SOLE_INCOME, record["sole_income"]):
        set_cell_text(tables, loc, fmt_money(amount))
    set_cell_text(tables, SOLE_INCOME_TOTAL, fmt_money(record["total"]))
    for loc, amount in zip(SOLE_EXPENSES, record["expenses"]):
        set_cell_text(tables, loc, fmt_money(amount))
    set_cell_text(tables, SOLE_EXPENSE_TOTAL, fmt_money(record["total"]))

    for loc, amount in zip(MULTI_INCOME, record["multi_income"]):
        set_cell_text(tables, loc, fmt_money(amount))
    set_cell_text(tables, MULTI_INCOME_TOTAL, fmt_money(record["total"]))
    for loc, amount in zip(MULTI_EXPENSES, record["expenses"]):
        set_cell_text(tables, loc, fmt_money(amount))
    set_cell_text(tables, MULTI_EXPENSE_TOTAL, fmt_money(record["total"]))

    body = ET.tostring(root, encoding="unicode")
    if body.startswith("<?xml"):
        body = body.split(">", 1)[1].lstrip()
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body
    ).encode("utf-8")


def write_docx(src: Path, dest: Path, document_xml: bytes) -> None:
    with ZipFile(src, "r") as zin, ZipFile(dest, "w") as zout:
        for info in zin.infolist():
            data = document_xml if info.filename == "word/document.xml" else zin.read(info.filename)
            zout.writestr(info, data)


def _applescript_posix(path: Path) -> str:
    return str(path.resolve()).replace("\\", "\\\\").replace('"', '\\"')


def convert_docx_to_pdf(pairs: list[tuple[Path, Path]]) -> None:
    if not pairs:
        return
    word_app = Path("/Applications/Microsoft Word.app")
    if not word_app.exists():
        raise SystemExit("Microsoft Word.app is required to save the proposals as PDF.")

    lines = [
        'tell application "Microsoft Word"',
        "    launch",
        "    delay 1",
    ]
    for docx, pdf in pairs:
        pdf.parent.mkdir(parents=True, exist_ok=True)
        if pdf.exists():
            pdf.unlink()
        lines.extend(
            [
                f'    set inputPath to POSIX file "{_applescript_posix(docx)}"',
                f'    set outputPath to POSIX file "{_applescript_posix(pdf)}"',
                "    open inputPath",
                "    delay 0.4",
                "    save as active document file name (outputPath as text) file format 17",
                "    close active document saving no",
            ]
        )
    lines.append("end tell")

    result = subprocess.run(
        ["osascript", "-"],
        input="\n".join(lines),
        capture_output=True,
        text=True,
    )
    missing = [pdf for _, pdf in pairs if not pdf.exists()]
    if result.returncode != 0 or missing:
        detail = (result.stderr or result.stdout or "").strip()
        names = ", ".join(path.name for path in missing[:5])
        raise SystemExit(
            "Word failed to export PDF"
            + (f" for {names}" if names else "")
            + (f". {detail}" if detail else ".")
        )


def write_index(output_dir: Path, records: list[dict]) -> None:
    rows = []
    for rec in records:
        rows.append(
            "<tr>"
            f"<td><a href=\"{rec['filename']}\">{rec['filename']}</a></td>"
            f"<td>{rec['case_id']}</td>"
            f"<td>{rec['company']}</td>"
            f"<td>{rec['project']}</td>"
            f"<td>{fmt_money(rec['loan'])}</td>"
            f"<td>{fmt_money(rec['owner'])}</td>"
            f"<td>{fmt_money(rec['total'])}</td>"
            f"<td>{rec['bank']}</td>"
            "</tr>"
        )
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Generated Loan Budget Proposals</title>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; margin: 40px; color: #222; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ padding: 10px 12px; border-bottom: 1px solid #ddd; text-align: left; }}
        th {{ font-size: 12px; letter-spacing: 0.4px; }}
        a {{ color: #1f4e79; }}
    </style>
</head>
<body>
    <h1>Corporate Loan Budget Proposals</h1>
    <p>{len(records)} use-of-proceeds budgets saved as PDF from budget-proposal-template.docx</p>
    <table>
        <thead>
            <tr>
                <th>File</th>
                <th>Case</th>
                <th>Company</th>
                <th>Project</th>
                <th>Term Loan</th>
                <th>Owner</th>
                <th>Project Cost</th>
                <th>Bank</th>
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
    cases = [
        row for row in read_csv(CASE_REGISTRY)
        if (row.get("Company Name") or "").strip()
    ]
    if not cases:
        raise SystemExit("No completed cases found in the Case Registry.")
    budget_rows = load_budget_rows()
    source = template_path()
    template_xml = ZipFile(source).read("word/document.xml").decode("utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    for pattern in ("C*_BudgetProposal.docx", "C*_BudgetProposal.pdf"):
        for stale in output_dir.glob(pattern):
            stale.unlink()
    for stale_name in ("index.html", "manifest.json"):
        stale = output_dir / stale_name
        if stale.exists():
            stale.unlink()

    records: list[dict] = []
    pdf_pairs: list[tuple[Path, Path]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for case in cases:
            record = build_record(case, budget_rows)
            if sum(record["sole_income"]) != record["total"]:
                raise RuntimeError(f"{record['case_id']}: sole income does not match total")
            if sum(record["multi_income"]) != record["total"]:
                raise RuntimeError(f"{record['case_id']}: combined funding does not match total")
            if sum(record["expenses"]) != record["total"]:
                raise RuntimeError(f"{record['case_id']}: expenses do not match total")

            filename = f"{record['case_id']}_BudgetProposal.pdf"
            record["filename"] = filename
            docx_path = tmp_dir / f"{record['case_id']}_BudgetProposal.docx"
            pdf_path = output_dir / filename
            write_docx(source, docx_path, fill_document(template_xml, record))
            pdf_pairs.append((docx_path, pdf_path))
            records.append(record)
        convert_docx_to_pdf(pdf_pairs)

    public = []
    skip = {"expense_labels", "sole_income_labels", "multi_income_labels"}
    for rec in records:
        item = {key: value for key, value in rec.items() if key not in skip}
        item["expense_lines"] = [
            {
                "label": " ".join(label) if isinstance(label, tuple) else label,
                "amount": amount,
            }
            for label, amount in zip(rec["expense_labels"], rec["expenses"])
        ]
        public.append(item)
    (output_dir / "manifest.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    write_index(output_dir, records)
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate corporate-loan budget proposals for every Case Registry row."
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
    print(f"Generated {len(records)} budget proposals in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
