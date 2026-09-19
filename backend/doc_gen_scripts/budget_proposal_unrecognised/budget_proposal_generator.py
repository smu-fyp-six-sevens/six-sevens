#!/usr/bin/env python3
"""Fill budget-proposal-template.pdf from the document mastersheet.

Reads the Case Registry and the tab whose name contains ``Budget Proposal``
out of ``vN_document_mastersheet.xlsx``. Only rows on that tab are generated
(Excel truncates long names, e.g. ``Budget Proposal (Unrecognised D``).

Usage:
    python budget_proposal_generator.py
    python budget_proposal_generator.py ../v16_document_mastersheet.xlsx
    python budget_proposal_generator.py ../v16_document_mastersheet.xlsx --cases C001 C002
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import warnings
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pymupdf

warnings.filterwarnings("ignore", message="Data Validation extension is not supported")


WORKSPACE = Path(__file__).resolve().parent.parent
TEMPLATE_CANDIDATES = [
    Path(__file__).resolve().parent / "budget-proposal-template.pdf",
    WORKSPACE / "budget-proposal-template.pdf",
]
MASTERSHEET_NAME = re.compile(r"^v(\d+)_document_mastersheet\.xlsx$", re.I)
BUDGET_COLS = (
    "Company Name (auto)",
    "UEN (auto)",
    "Proposal Purpose",
    "Funder Type",
    "Loan Amount Applied (SGD) (auto)",
    "Owner Contribution (SGD)",
    "Total Projected Income (SGD)",
    "Working Capital (SGD)",
    "Equipment & Renovation (SGD)",
    "Marketing & Advertising (SGD)",
    "Contingency Reserve (SGD)",
    "Total Projected Expenses (SGD)",
)
OTHER_BANKS = ["DBS Bank", "OCBC Bank", "UOB", "HSBC", "Standard Chartered", "Maybank"]

LEFT = {
    "label_x0": 54.0,
    "label_x1": 226.0,
    "amount_x0": 230.0,
    "amount_x1": 276.0,
}
RIGHT = {
    "label_x0": 401.2,
    "label_x1": 590.0,
    "amount_x0": 590.0,
    "amount_x1": 650.0,
}
BODY_SIZE = 10.0
TITLE_SIZE = 13.0
NOTE_WIDTH = 510.0
_FONT_FILES: dict[str, str] = {}

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


def template_path() -> Path:
    for path in TEMPLATE_CANDIDATES:
        if path.exists():
            return path
    raise SystemExit("budget-proposal-template.pdf was not found.")


def _find_font_file(*names: str) -> str | None:
    roots = [
        Path("/System/Library/Fonts/Supplemental"),
        Path("/Library/Fonts"),
        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
        Path("/usr/share/fonts/truetype/msttcorefonts"),
        Path("/usr/share/fonts/truetype/liberation"),
    ]
    for root in roots:
        for name in names:
            path = root / name
            if path.exists():
                return str(path)
    return None


def _font_files() -> dict[str, str]:
    if not _FONT_FILES:
        regular = _find_font_file("Arial.ttf", "arial.ttf", "LiberationSans-Regular.ttf")
        bold = _find_font_file(
            "Arial Bold.ttf", "arialbd.ttf", "Arial-Bold.ttf", "LiberationSans-Bold.ttf"
        )
        if regular:
            _FONT_FILES["helv"] = regular
        if bold:
            _FONT_FILES["hebo"] = bold
    return _FONT_FILES


def _pdf_font(fontname: str) -> pymupdf.Font:
    path = _font_files().get(fontname)
    return pymupdf.Font(fontfile=path) if path else pymupdf.Font(fontname)


def _insert_kwargs(fontname: str) -> dict:
    path = _font_files().get(fontname)
    if path:
        return {"fontfile": path, "fontname": f"budget-{fontname}", "set_simple": True}
    return {"fontname": fontname}


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


def _open_sheet_containing(wb, needle: str):
    """Find the tab whose name contains ``needle`` (Excel may truncate the rest)."""
    matches = [name for name in wb.sheetnames if needle.lower() in name.lower()]
    if not matches:
        raise SystemExit(
            f"Mastersheet has no tab containing {needle!r}. Found: {', '.join(wb.sheetnames)}"
        )
    preferred = [name for name in matches if "unrecognised" in name.lower()]
    return wb[(preferred or matches)[0]]


def load_mastersheet(xlsx: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]]]:
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    cases = {
        row["Case ID"]: row
        for row in _sheet_rows(_open_sheet(wb, "Case Registry"))
        if row.get("Case ID")
    }
    budget_rows = [
        row
        for row in _sheet_rows(_open_sheet_containing(wb, "Budget Proposal"))
        if row.get("Case ID") and row.get("Company Name (auto)")
    ]
    return budget_rows, cases


def _sheet_value(row: dict[str, str], column: str) -> str:
    value = (row.get(column) or "").strip()
    if not value:
        raise RuntimeError(
            f"{row.get('Case ID') or '?'}: Budget Proposal tab is missing {column!r}"
        )
    return value


def budget_from_sheet(row: dict[str, str]) -> dict[str, int | str]:
    owner = parse_money(_sheet_value(row, "Owner Contribution (SGD)"))
    total = parse_money(_sheet_value(row, "Total Projected Income (SGD)"))
    working = parse_money(_sheet_value(row, "Working Capital (SGD)"))
    equipment = parse_money(_sheet_value(row, "Equipment & Renovation (SGD)"))
    marketing = parse_money(_sheet_value(row, "Marketing & Advertising (SGD)"))
    contingency = parse_money(_sheet_value(row, "Contingency Reserve (SGD)"))
    expenses = parse_money(_sheet_value(row, "Total Projected Expenses (SGD)"))
    loan = parse_money(_sheet_value(row, "Loan Amount Applied (SGD) (auto)"))
    if working + equipment + marketing + contingency != total:
        raise RuntimeError(
            f"{row.get('Case ID')}: expense lines "
            f"{working + equipment + marketing + contingency} != Total Projected Income {total}"
        )
    if expenses != total:
        raise RuntimeError(
            f"{row.get('Case ID')}: Total Projected Expenses {expenses} != Total Projected Income {total}"
        )
    if loan + owner != total:
        raise RuntimeError(
            f"{row.get('Case ID')}: loan {loan} + owner {owner} != Total Projected Income {total}"
        )
    return {
        "loan": loan,
        "owner": owner,
        "total": total,
        "working_capital": working,
        "equipment": equipment,
        "marketing": marketing,
        "contingency": contingency,
        "purpose": _sheet_value(row, "Proposal Purpose"),
        "funder_type": _sheet_value(row, "Funder Type"),
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


def build_record(row: dict[str, str], cases: dict[str, dict[str, str]]) -> dict:
    case_id = row["Case ID"]
    for column in BUDGET_COLS:
        _sheet_value(row, column)
    case = cases.get(case_id, {})
    company = _sheet_value(row, "Company Name (auto)")
    uen = _sheet_value(row, "UEN (auto)")
    rng = random.Random(int(case_id[1:]))
    budget = budget_from_sheet(row)
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
        "uen": uen,
        "address": case.get("Registered Address", ""),
        "applicant": case.get("Applicant Name (Director & UBO)", ""),
        "bank": bank,
        "other_bank": alt_bank,
        "profile": profile_name,
        "project": project,
        "purpose": budget["purpose"],
        "funder_type": budget["funder_type"],
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
        "fraud_case": row.get("Fraud Injected? (Y/N)") or case.get("Fraud Case? (Y/N)", "N"),
    }


def _hits(page: pymupdf.Page, text: str, count: int | None = None) -> list[pymupdf.Rect]:
    found = page.search_for(text)
    if not found:
        raise KeyError(f"Template text not found: {text!r}")
    if count is not None and len(found) < count:
        raise KeyError(f"Template text {text!r} found {len(found)} time(s), expected {count}.")
    return found


def _cover_rect(hit: pymupdf.Rect, x0: float, x1: float, y1: float | None = None) -> pymupdf.Rect:
    return pymupdf.Rect(x0, hit.y0 - 0.35, x1, (y1 if y1 is not None else hit.y1) + 0.35)


def _fit_size(text: str, width: float, fontsize: float, fontname: str, min_size: float = 6.5) -> float:
    font = _pdf_font(fontname)
    size = fontsize
    while size > min_size and font.text_length(text, fontsize=size) > width - 1:
        size -= 0.3
    return size


def _wrap_lines(text: str, width: float, fontsize: float, fontname: str = "helv") -> list[str]:
    font = _pdf_font(fontname)
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if font.text_length(trial, fontsize=fontsize) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines or [text]


def _draw_left(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, fontsize: float, fontname: str) -> None:
    if not text:
        return
    size = _fit_size(text, rect.width, fontsize, fontname)
    baseline = rect.y0 + size * 0.90
    page.insert_text((rect.x0, baseline), text, fontsize=size, color=(0, 0, 0), **_insert_kwargs(fontname))


def _draw_right(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, fontsize: float, fontname: str = "helv") -> None:
    if not text:
        return
    size = _fit_size(text, rect.width - 1, fontsize, fontname)
    width = _pdf_font(fontname).text_length(text, fontsize=size)
    baseline = rect.y0 + size * 0.90
    page.insert_text((rect.x1 - width, baseline), text, fontsize=size, color=(0, 0, 0), **_insert_kwargs(fontname))


def _label_amount_pair(hit: pymupdf.Rect, column: dict[str, float]) -> tuple[pymupdf.Rect, pymupdf.Rect]:
    label = _cover_rect(hit, column["label_x0"], column["label_x1"])
    amount = _cover_rect(hit, column["amount_x0"], column["amount_x1"])
    return label, amount


def fill_pdf(template: Path, record: dict, dest: Path) -> None:
    doc = pymupdf.open(template)
    try:
        page = doc[0]
        covers: list[pymupdf.Rect] = []
        draws: list[tuple] = []

        def replace_label(text: str, new: str, index: int, column: dict[str, float], *, size: float = BODY_SIZE, fontname: str = "helv") -> pymupdf.Rect:
            hit = _hits(page, text)[index]
            label, amount = _label_amount_pair(hit, column)
            covers.append(label)
            draw = pymupdf.Rect(hit.x0, hit.y0, column["label_x1"], hit.y1)
            draws.append(("left", draw, new, size, fontname))
            return amount

        def amount_on(text: str, index: int, column: dict[str, float]) -> pymupdf.Rect:
            hit = _hits(page, text)[index]
            return _cover_rect(hit, column["amount_x0"], column["amount_x1"])

        def replace_plain(text: str, new: str, index: int, *, size: float, fontname: str, x1: float | None = None, y1: float | None = None) -> None:
            hit = _hits(page, text)[index]
            rect = pymupdf.Rect(hit.x0, hit.y0 - 0.2, x1 if x1 is not None else hit.x1 + 2, y1 if y1 is not None else hit.y1)
            covers.append(rect)
            draws.append(("left", rect, new, size, fontname))

        replace_plain(
            "Proposal Budget - BCF as sole funder example",
            f"Proposal Budget - {record['funder_type']}",
            0,
            size=TITLE_SIZE,
            fontname="hebo",
            x1=359.0,
            y1=41.7,
        )
        replace_plain(
            "Proposal Budget - Multiple funder example",
            "Proposal Budget - Combined funding sources",
            0,
            size=TITLE_SIZE,
            fontname="hebo",
            x1=683.0,
            y1=41.7,
        )
        for index in (0, 1):
            replace_plain("Scallop Beach Art Gallery", record["company"], index, size=BODY_SIZE, fontname="helv", x1=(226.0 if index == 0 else 601.0))
            replace_plain("Snails & Scales Exhibit", record["purpose"], index, size=BODY_SIZE, fontname="helv", x1=(226.0 if index == 0 else 601.0))

        sole_income_amounts = [
            replace_label("Exhibition Sales", record["sole_income_labels"][0], 0, LEFT),
            replace_label("Community Foundation Grant", record["sole_income_labels"][1], 0, LEFT),
        ]
        sole_income_total = amount_on("TOTAL INCOME", 0, LEFT)
        multi_income_amounts = [
            replace_label("Exhibition Sales", record["multi_income_labels"][0], 1, RIGHT),
            replace_label("Community Foundation Grant", record["multi_income_labels"][1], 1, RIGHT),
            replace_label("Patron donations", record["multi_income_labels"][2], 0, RIGHT),
            replace_label("Corporate sponsors", record["multi_income_labels"][3], 0, RIGHT),
        ]
        multi_income_total = amount_on("TOTAL INCOME", 1, RIGHT)

        expense_originals = [
            "Exhibition coordination",
            "Installation",
            "Curatorial support",
            "Registration",
            "Publicity & Advertising",
            "Display",
            "Rent",
            "Supplies",
        ]
        labels = record["expense_labels"]
        sole_expense_amounts: list[pymupdf.Rect] = []
        multi_expense_amounts: list[pymupdf.Rect] = []
        for original, label in zip(expense_originals, labels):
            sole_expense_amounts.append(replace_label(original, label, 0, LEFT))
            multi_expense_amounts.append(replace_label(original, label, 1, RIGHT))

        last_left, last_right = labels[8] if isinstance(labels[8], tuple) else (labels[8], "")
        first_left = _hits(page, "Consignment payment to")[0]
        first_right = _hits(page, "Consignment payment to")[1]
        second_left = _hits(page, "artists")[0]
        second_right = _hits(page, "artists")[1]
        covers.extend([
            _cover_rect(first_left, LEFT["label_x0"], LEFT["label_x1"]),
            _cover_rect(second_left, LEFT["label_x0"], LEFT["label_x1"]),
            _cover_rect(first_right, RIGHT["label_x0"], RIGHT["label_x1"]),
            _cover_rect(second_right, RIGHT["label_x0"], RIGHT["label_x1"]),
        ])
        draws.append(("left", pymupdf.Rect(first_left.x0, first_left.y0, LEFT["label_x1"], first_left.y1), last_left, BODY_SIZE, "helv"))
        draws.append(("left", pymupdf.Rect(second_left.x0, second_left.y0, LEFT["label_x1"], second_left.y1), last_right, BODY_SIZE, "helv"))
        draws.append(("left", pymupdf.Rect(first_right.x0, first_right.y0, RIGHT["label_x1"], first_right.y1), last_left, BODY_SIZE, "helv"))
        draws.append(("left", pymupdf.Rect(second_right.x0, second_right.y0, RIGHT["label_x1"], second_right.y1), last_right, BODY_SIZE, "helv"))
        sole_expense_amounts.append(_cover_rect(second_left, LEFT["amount_x0"], LEFT["amount_x1"]))
        multi_expense_amounts.append(_cover_rect(second_right, RIGHT["amount_x0"], RIGHT["amount_x1"]))
        covers.append(sole_expense_amounts[-1])
        covers.append(multi_expense_amounts[-1])

        sole_expense_total = amount_on("TOTAL EXPENSES", 0, LEFT)
        multi_expense_total = amount_on("TOTAL EXPENSES", 1, RIGHT)

        for rect, amount in zip(sole_income_amounts, record["sole_income"]):
            covers.append(rect)
            draws.append(("right", rect, fmt_money(amount), BODY_SIZE, "helv"))
        covers.append(sole_income_total)
        draws.append(("right", sole_income_total, fmt_money(record["total"]), BODY_SIZE, "helv"))
        for rect, amount in zip(sole_expense_amounts, record["expenses"]):
            covers.append(rect)
            draws.append(("right", rect, fmt_money(amount), BODY_SIZE, "helv"))
        covers.append(sole_expense_total)
        draws.append(("right", sole_expense_total, fmt_money(record["total"]), BODY_SIZE, "helv"))

        for rect, amount in zip(multi_income_amounts, record["multi_income"]):
            covers.append(rect)
            draws.append(("right", rect, fmt_money(amount), BODY_SIZE, "helv"))
        covers.append(multi_income_total)
        draws.append(("right", multi_income_total, fmt_money(record["total"]), BODY_SIZE, "helv"))
        for rect, amount in zip(multi_expense_amounts, record["expenses"]):
            covers.append(rect)
            draws.append(("right", rect, fmt_money(amount), BODY_SIZE, "helv"))
        covers.append(multi_expense_total)
        draws.append(("right", multi_expense_total, fmt_money(record["total"]), BODY_SIZE, "helv"))

        for snippet in (
            "We request that you use a spreadsheet in this format",
            'You may find it helpful to use the "actual expense" column',
            "This will be required in submitting your Grant Final Report",
        ):
            hit = _hits(page, snippet)[0]
            covers.append(pymupdf.Rect(54.0, hit.y0 - 0.4, 570.0, hit.y1 + 0.4))

        note_start = _hits(page, "PLEASE NOTE")[0]
        note_end = _hits(page, "BCF Board approval")[0]
        note_rect = pymupdf.Rect(54.0, note_start.y0 - 0.4, 570.0, note_end.y1 + 18.0)
        covers.append(note_rect)
        note = (
            "PLEASE NOTE: If during the facility period, any actual expense differs from the "
            "budgeted expense by 10% or more, you must notify "
            f"{record['bank']}'s relationship manager as soon as possible since this may "
            "indicate a change in the purpose of the facility, and may require credit "
            "committee approval."
        )
        draws.append(("note", note_rect, note, BODY_SIZE, "helv"))

        for rect in covers:
            page.add_redact_annot(rect, fill=(1, 1, 1), cross_out=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE)

        for kind, rect, text, size, fontname in draws:
            if kind == "left":
                _draw_left(page, rect, text, fontsize=size, fontname=fontname)
            elif kind == "right":
                _draw_right(page, rect, text, fontsize=size, fontname=fontname)
            else:
                lines = _wrap_lines(text, NOTE_WIDTH, size)
                y = rect.y0 + size * 0.82
                for line in lines:
                    page.insert_text(
                        (rect.x0 + 3.0, y),
                        line,
                        fontsize=size,
                        color=(0, 0, 0),
                        **_insert_kwargs(fontname),
                    )
                    y += 14.1

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
            f"<td>{rec['purpose']}</td>"
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
    <p>{len(records)} use-of-proceeds budgets saved as PDF from budget-proposal-template.pdf</p>
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


def generate(
    output_dir: Path,
    xlsx: str | Path | None = None,
    cases: list[str] | None = None,
) -> list[dict]:
    mastersheet = find_mastersheet(xlsx)
    budget_rows, registry = load_mastersheet(mastersheet)
    if cases:
        wanted = {item.strip() for item in cases}
        budget_rows = [row for row in budget_rows if row.get("Case ID") in wanted]
    if not budget_rows:
        raise SystemExit(f"No Budget Proposal rows found in {mastersheet.name}.")
    source = template_path()
    output_dir.mkdir(parents=True, exist_ok=True)

    for pattern in ("C*_BudgetProposal.docx", "C*_BudgetProposal.pdf"):
        for stale in output_dir.glob(pattern):
            stale.unlink()
    for stale_name in ("index.html", "manifest.json"):
        stale = output_dir / stale_name
        if stale.exists():
            stale.unlink()
    work_dir = output_dir / "_work"
    if work_dir.exists():
        shutil.rmtree(work_dir)

    records: list[dict] = []
    for row in budget_rows:
        record = build_record(row, registry)
        if sum(record["sole_income"]) != record["total"]:
            raise RuntimeError(f"{record['case_id']}: sole income does not match total")
        if sum(record["multi_income"]) != record["total"]:
            raise RuntimeError(f"{record['case_id']}: combined funding does not match total")
        if sum(record["expenses"]) != record["total"]:
            raise RuntimeError(f"{record['case_id']}: expenses do not match total")

        filename = f"{record['case_id']}_BudgetProposal.pdf"
        record["filename"] = filename
        fill_pdf(source, record, output_dir / filename)
        records.append(record)

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
        description="Generate corporate-loan budget proposals from the document mastersheet."
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
        help="Case IDs to include; default is every row on the Budget Proposal tab.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = generate(args.out, xlsx=args.xlsx, cases=args.cases)
    print(f"Generated {len(records)} budget proposals in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
