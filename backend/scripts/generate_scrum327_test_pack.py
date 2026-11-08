"""Generate a small synthetic SCRUM-327 pack of labelled fraud and clean PDFs.

From backend/:
    python scripts/generate_scrum327_test_pack.py

The pack is written under documents_generated/SCRUM327_agent_refinements/ and
contains one positive and one clean control per included mastersheet dimension.
These are controlled synthetic examples, not copies of real customer documents.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pymupdf

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from evaluate_scrum327 import DEFAULT_MASTERSHEET, load_master_labels
from doc_gen_scripts.certificate_of_incorporation.coi_generator import generate_certificate_of_incorporation
from doc_gen_scripts.fatca_crs.docgen_fatca_crs import (
    GLDB_BLANK_PDF,
    build_personas as build_fatca_personas,
    fill_form as fill_fatca_form,
)
from doc_gen_scripts.personal_proof_of_residence.utility_bill_generator import (
    generate_utility_bill,
    synthesize_utility_bill_data,
)

DEFAULT_OUTPUT = BACKEND / "documents_generated" / "SCRUM327_agent_refinements"

# These entries have a usable expected dimension and a scenario we can
# reproduce locally without claiming to reconstruct the absent source PDF.
SCENARIOS: dict[str, tuple[str, str]] = {
    "C014": ("spelling", "spelling_tool"),
    "C026": ("underline", "font_consistency_tool"),
    "C018": ("alignment", "layout_anomalies_tool"),
    "C028": ("metadata", "metadata_tool"),
    "C001": ("future_date", "date_consistency_tool"),
    "C002": ("balance", "numerical_checks_tool"),
    "C008": ("fatca_spelling", "spelling_tool"),
    "C024": ("font_size", "font_consistency_tool"),
    "C021": ("font_family", "font_consistency_tool"),
    "C005": ("text_overlay", "layout_anomalies_tool"),
    "C045": ("logo_alignment", "layout_anomalies_tool"),
}

# Use a clean control with the same underlying document type where the source
# sheet contains several proof-of-address formats.
CLEAN_CONTROL_CASES = {"C024": "C002"}


def _put(page: pymupdf.Page, text: str, x: float, y: float, *, size: float = 10,
         font: str = "helv") -> None:
    page.insert_text((x, y), text, fontsize=size, fontname=font)


def _render_scenario(kind: str, fraud: bool, path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    if kind in {"spelling", "underline", "alignment"}:
        _put(page, "ACCOUNTING AND CORPORATE REGULATORY AUTHORITY", 58, 56, size=12)
        _put(page, "CERTIFICATE OF COMPANY RECORDS", 58, 91, size=15)
        name = "Harbor Light Shippng Pte Ltd" if kind == "spelling" and fraud else "Harbor Light Shipping Pte Ltd"
        name_x = 58 if kind != "alignment" or not fraud else 192
        _put(page, f"Company: {name}", name_x, 132)
        _put(page, "Registration number: 201634564N", 58, 158)
        _put(page, "Share capital", 58, 190)
        if kind == "underline" and fraud:
            page.draw_line((58, 193), (118, 193), width=0.8)
        _put(page, "Issued on 12 June 2026", 58, 222)
    elif kind == "metadata":
        _put(page, "EXPERIAN CREDIT REPORT", 58, 56, size=16)
        _put(page, "Company: Granite Civil Engineering Pte Ltd", 58, 102)
        _put(page, "Report date: 14 April 2026", 58, 128)
        doc.set_metadata({
            "title": "Experian Credit Report",
            "author": "Experian Credit Bureau" if not fraud else "Unknown Editor",
            "creator": "Experian Report Service" if not fraud else "Adobe Acrobat Pro",
            "producer": "Experian Report Service" if not fraud else "Adobe Acrobat Pro",
        })
    elif kind == "future_date":
        _put(page, "LOAN FACILITY LETTER", 58, 56, size=16)
        _put(page, "Borrower: Golden Harvest Trading Pte Ltd", 58, 98)
        _put(page, "Facility: Term loan", 58, 124)
        _put(page, f"Offer date: 14 February {3026 if fraud else 2026}", 58, 150)
        _put(page, "Repayment period: 60 months", 58, 176)
    elif kind == "balance":
        _put(page, "BANK STATEMENT", 58, 56, size=16)
        _put(page, "Account holder: Silver Oak Logistics Pte Ltd", 58, 98)
        _put(page, "Opening balance: SGD 1,000.00", 58, 136)
        _put(page, "Withdrawal: SGD 100.00", 58, 164)
        _put(page, f"Closing balance: SGD {1_200 if fraud else 900:.2f}", 58, 192)
    elif kind == "fatca_spelling":
        _put(page, "FATCA / CRS SELF-CERTIFICATION", 58, 56, size=15)
        _put(page, "Part 2B - Benefical Owner Status" if fraud else "Part 2B - Beneficial Owner Status", 58, 102)
        _put(page, "Entity: Pacific Rim Seafood Trading Pte Ltd", 58, 132)
        _put(page, "I confirm the information above is accurate.", 58, 162)
    elif kind == "font_size":
        _put(page, "UTILITY BILL", 58, 56, size=15)
        _put(page, "Balance Brought Forward from Previous Bill", 58, 98)
        _put(page, "Balance", 58, 132)
        _put(page, "Forward", 58, 158, size=16 if fraud else 10)
        _put(page, "Amount due: SGD 84.20", 58, 192)
    elif kind == "font_family":
        _put(page, "STATEMENT OF FINANCIAL POSITION", 58, 56, size=15)
        _put(page, "Trade receivables", 58, 106)
        _put(page, "Trade payables", 58, 132, font="tiro" if fraud else "helv")
        _put(page, "Cash and cash equivalents", 58, 158)
        _put(page, "Total liabilities", 58, 184)
    elif kind == "text_overlay":
        _put(page, "PAYSLIP", 58, 56, size=15)
        _put(page, "Employee: Chua Bee Choo", 58, 98)
        _put(page, "Employer: Crimson Textile Manufacturing Pte Ltd", 58, 124)
        _put(page, "Basic salary: SGD 3,000.00", 58, 160)
        _put(page, "Net pay: SGD 3,000.00", 58, 190)
        if fraud:
            # The duplicate overprint is intentional: it simulates the master's
            # text-overlay case while leaving the original text extractable.
            _put(page, "Net pay: SGD 4,000.00", 58, 190)
    elif kind == "logo_alignment":
        _put(page, "NOTICE OF ASSESSMENT", 58, 56, size=15)
        _put(page, "IRAS", 270 if fraud else 500, 104, size=20)
        _put(page, "Company: Lumina Events Group Pte Ltd", 58, 148)
        _put(page, "Year of Assessment: 2025", 58, 176)
        _put(page, "Tax payable: SGD 12,400.00", 58, 204)
    else:
        raise ValueError(f"Unknown scenario: {kind}")

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def _generate_real_coi(mastersheet: Path, case_id: str, path: Path, *, misspell_company: bool = False) -> None:
    """Render a complete certificate with the production project generator."""
    workbook = openpyxl.load_workbook(mastersheet, read_only=True, data_only=True)
    try:
        sheet = workbook["Case Registry"]
        headers = [str(value).strip() if value is not None else "" for value in next(sheet.iter_rows(values_only=True))]
        row = next((dict(zip(headers, values)) for values in sheet.iter_rows(min_row=2, values_only=True)
                    if str(values[0]).strip() == case_id), None)
        if row is None:
            raise ValueError(f"Case {case_id} is absent from the mastersheet")
    finally:
        workbook.close()

    company_name = str(row["Company Name"])
    if misspell_company:
        if "Shipping" not in company_name:
            raise ValueError(f"Expected Shipping in the company name for {case_id}")
        company_name = company_name.replace("Shipping", "Shippng", 1)
    incorporation_date = row["Incorporation Date"]
    if isinstance(incorporation_date, datetime):
        incorporation_date = incorporation_date.date()
    latest_date = row["Application Date"]
    if isinstance(latest_date, datetime):
        latest_date = latest_date.date()
    elif isinstance(latest_date, str):
        latest_date = date.fromisoformat(latest_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    generate_certificate_of_incorporation(
        str(path),
        company_name=company_name,
        company_number=str(row["Company UEN / Reg No."]),
        incorporation_date=incorporation_date,
        company_type=str(row["Company Type"]),
        latest_date=latest_date,
    )


def _generate_real_fatca(mastersheet: Path, case_id: str, path: Path) -> None:
    """Fill the production GLDB FATCA/CRS form for one mastersheet case."""
    personas = build_fatca_personas(mastersheet, cases=[case_id])
    if len(personas) != 1:
        raise ValueError(f"Expected one FATCA persona for {case_id}, got {len(personas)}")
    fill_fatca_form(personas[0], blank_pdf=GLDB_BLANK_PDF, out_pdf=path)


def _misspell_fatca_beneficial(path: Path) -> None:
    """Create the labelled typo by altering one word on a generated GLDB form."""
    doc = pymupdf.open(path)
    target = None
    page = None
    for candidate_page in doc:
        for block in candidate_page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    if span["text"].strip() == "Beneficial":
                        page, target = candidate_page, span
                        break
                if target:
                    break
            if target:
                break
        if target:
            break
    if not target or page is None:
        doc.close()
        raise ValueError(f"Could not find the Beneficial label in {path}")

    font_xref = next(
        (entry[0] for entry in page.get_fonts(full=True)
         if entry[4] == "TT0" and "Aptos" in entry[3]),
        None,
    )
    if font_xref is None:
        doc.close()
        raise ValueError(f"Could not find the embedded regular Aptos font in {path}")
    font_bytes = doc.extract_font(font_xref)[3]
    bbox = pymupdf.Rect(target["bbox"])
    origin = target["origin"]
    size = target["size"]
    page.add_redact_annot(bbox, fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0)
    page.insert_font(fontname="scrum327_aptos", fontbuffer=font_bytes)
    page.insert_text(origin, "Benefical", fontname="scrum327_aptos", fontsize=size, color=(0, 0, 0))
    rewritten = path.with_name(f"{path.stem}.rewritten.pdf")
    doc.save(rewritten, garbage=4, deflate=True)
    doc.close()
    os.replace(rewritten, path)


def _generate_real_utility_bill(mastersheet: Path, case_id: str, individual_id: str, path: Path) -> None:
    """Generate a complete proof-of-address bill with the production renderer."""
    workbook = openpyxl.load_workbook(mastersheet, read_only=True, data_only=True)
    try:
        registry_rows = workbook["Case Registry"].iter_rows(values_only=True)
        registry_headers = next(registry_rows)
        case = next(
            (dict(zip(registry_headers, row)) for row in registry_rows
             if str(row[0]).strip() == case_id),
            None,
        )
        people_rows = workbook["Individuals"].iter_rows(values_only=True)
        people_headers = next(people_rows)
        person = next(
            (dict(zip(people_headers, row)) for row in people_rows
             if str(row[0]).strip() == individual_id),
            None,
        )
    finally:
        workbook.close()
    if case is None or person is None:
        raise ValueError(f"Missing case or individual data for {case_id}/{individual_id}")

    application_date = case["Application Date"]
    if isinstance(application_date, datetime):
        application_date = application_date.date()
    elif isinstance(application_date, str):
        application_date = date.fromisoformat(application_date)
    # Use an address returned by OneMap's official API example so an invalid
    # synthetic master-sheet postal code does not mask this font-size scenario.
    address_lines = ["640 Rowell Road", "Singapore 200640"]
    details = synthesize_utility_bill_data(individual_id, application_date=application_date)
    details.update(
        customer_name=str(person["Name (auto/fill)"]),
        address_lines=address_lines,
        invoice_address_lines=address_lines,
        contract_end_date=details["bill_date"] + timedelta(days=365),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    generate_utility_bill(str(path), **details)


def _change_forward_font_size(path: Path) -> None:
    """Alter only "Forward" on the generated utility bill to match the source label."""
    doc = pymupdf.open(path)
    page = doc[0]
    spans = [
        span
        for block in page.get_text("dict")["blocks"]
        for line in block.get("lines", [])
        for span in line["spans"]
        if "Balance Brought Forward from Previous Bill" in span["text"]
    ]
    if len(spans) != 1:
        doc.close()
        raise ValueError(f"Expected one balance-forward label in {path}, found {len(spans)}")
    span = spans[0]
    bbox = pymupdf.Rect(span["bbox"])
    baseline = span["origin"][1]
    start_x = span["origin"][0]
    regular_size = span["size"]
    page.add_redact_annot(bbox, fill=(1, 1, 1))
    page.apply_redactions(images=0, graphics=0)

    pieces = [
        ("Balance Brought ", regular_size),
        ("Forward", 16.0),
        (" from Previous Bill", regular_size),
    ]
    x = start_x
    for text, size in pieces:
        # Keep the larger word's extracted bbox on the same text row so the
        # consistency checker compares its style to the adjacent label text.
        y = baseline - 1.0 if text == "Forward" else baseline
        page.insert_text((x, y), text, fontname="helv", fontsize=size, color=(0, 0, 0))
        x += pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    rewritten = path.with_name(f"{path.stem}.rewritten.pdf")
    doc.save(rewritten, garbage=4, deflate=True)
    doc.close()
    os.replace(rewritten, path)


def build_pack(mastersheet: Path = DEFAULT_MASTERSHEET, output: Path = DEFAULT_OUTPUT) -> dict:
    labels = load_master_labels(mastersheet)
    positives = {label["case_id"]: label for label in labels if label["fraud_expected"]}
    clean_by_sheet: dict[str, list[dict]] = {}
    for label in labels:
        if not label["fraud_expected"] and label["filename"].lower().endswith(".pdf"):
            clean_by_sheet.setdefault(label["source_sheet"], []).append(label)

    manifest = {"source": mastersheet.name, "synthetic": True, "cases": [], "excluded_master_cases": []}
    for case_id, (scenario, expected_tool) in SCENARIOS.items():
        positive = positives[case_id]
        controls = clean_by_sheet.get(positive["source_sheet"], [])
        if not controls:
            raise ValueError(f"No clean PDF control in {positive['source_sheet']}")
        preferred_control = CLEAN_CONTROL_CASES.get(case_id)
        control = next(
            (row for row in controls if row["case_id"] == preferred_control),
            next((row for row in controls if row["case_id"] != case_id), controls[0]),
        )
        positive_path = output / "fraud" / positive["filename"]
        clean_path = output / "clean" / control["filename"]
        if positive["source_sheet"] == "Cert of Incorporation":
            _generate_real_coi(mastersheet, positive["case_id"], positive_path, misspell_company=True)
            _generate_real_coi(mastersheet, control["case_id"], clean_path)
        elif positive["source_sheet"] == "FATCA Self-Declaration":
            _generate_real_fatca(mastersheet, positive["case_id"], positive_path)
            _misspell_fatca_beneficial(positive_path)
            _generate_real_fatca(mastersheet, control["case_id"], clean_path)
        elif positive["case_id"] == "C024":
            _generate_real_utility_bill(mastersheet, "C024", "C024-P1", positive_path)
            _change_forward_font_size(positive_path)
            _generate_real_utility_bill(mastersheet, "C002", "C002-P1", clean_path)
        else:
            _render_scenario(scenario, True, positive_path)
            _render_scenario(scenario, False, clean_path)
        manifest["cases"].append({
            "scenario": scenario,
            "source_case_id": case_id,
            "source_filename": positive["filename"],
            "source_sheet": positive["source_sheet"],
            "fraud_file": str(positive_path.relative_to(output)),
            "clean_control_case_id": control["case_id"],
            "clean_control_filename": control["filename"],
            "clean_file": str(clean_path.relative_to(output)),
            "expected_tool": expected_tool,
            "expected_description": positive["description"],
        })

    included = set(SCENARIOS)
    for label in labels:
        if label["fraud_expected"] and label["case_id"] not in included:
            manifest["excluded_master_cases"].append({
                "case_id": label["case_id"],
                "filename": label["filename"],
                "dimension": label["dimension"],
                "description": label["description"],
                "reason": (
                    "Not included in this first synthetic pack; source label or scenario needs manual review."
                ),
            })

    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    readme = """# SCRUM-327 agent refinement test pack

This pack contains controlled, synthetic PDFs generated from the SCRUM-327
mastersheet scenarios. Each fraud example has a clean counterpart for checking
false positives. The files are test data, not copies of the original source
documents or proof of a live agent baseline.

Upload each pair as separate documents and run individual document detection.
Do not run case analysis across unrelated pairs: cross-document identity checks
would add unrelated findings. Compare the fraud PDF with `expected_tool` in
`manifest.json`; the clean counterpart should not produce a suspicious finding.

The first pack omits source rows whose current mastersheet label is incomplete,
ambiguous, or inconsistent with the stated document scenario. See
`excluded_master_cases` in the manifest. Refine those labels before using them
to calculate accuracy or false-positive rates.
"""
    (output / "README.md").write_text(readme)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mastersheet", type=Path, default=DEFAULT_MASTERSHEET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build_pack(args.mastersheet, args.output)
    print(f"Generated {len(manifest['cases'])} fraud/clean pairs under {args.output}")
    print(f"Excluded {len(manifest['excluded_master_cases'])} master-sheet fraud rows pending label review")


if __name__ == "__main__":
    main()
