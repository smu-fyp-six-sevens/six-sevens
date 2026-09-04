"""
Cross-checks every document-type sheet in `v9_document_mastersheet.xlsx` against
`documents_generated/<case_id>/` to confirm every row that should produce a PDF
actually has one on disk (and flags any file on disk that isn't traced back to a
mastersheet row).

Deliberately does NOT import generate_from_mastersheet.py or any doc_gen_scripts
generator module -- this only needs to read the mastersheet and list a directory,
so it stays fast and side-effect free. It re-derives each sheet's expected filename
by mirroring generate_from_mastersheet.py's own per-section logic (see the comments
below); if that orchestration logic changes, update the matching branch here too.

Filenames themselves don't need to match the mastersheet's own "Filename" column --
several sections (NRIC-Passport in particular) intentionally ignore it and use their
own naming convention. This only checks that *a* file exists for each row.

Usage (from backend/):
    python -m scripts.verify_documents                # every case with a Case Registry row
    python -m scripts.verify_documents C001 C002       # just these cases
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parents[1]
MASTERSHEET = BASE / "doc_gen_scripts" / "v9_document_mastersheet.xlsx"
OUT_DIR = BASE / "documents_generated"

DOC_SHEETS = [
    "Cert of Incorporation", "Cert of Incumbency", "Company Constitution",
    "Credit Reports", "List of Loans", "Bank Statements", "FATCA Self-Declaration",
    "Proof of Address", "NRIC-Passport", "Source of Wealth Docs", "Payslips",
]


def _load_rows(wb: openpyxl.Workbook, sheet: str) -> list[dict]:
    ws = wb[sheet]
    all_rows = list(ws.iter_rows(values_only=True))
    header = all_rows[0]
    return [dict(zip(header, row)) for row in all_rows[1:] if any(row)]


def _case_ids(wb: openpyxl.Workbook) -> list[str]:
    return [str(r["Case ID"]) for r in _load_rows(wb, "Case Registry") if r.get("Case ID")]


def expected_filenames(wb: openpyxl.Workbook, cases: list[str]) -> dict[str, set[str]]:
    """Case ID -> the set of filenames generate_from_mastersheet.py should have
    written for it, per mastersheet row, mirroring that script's own filename logic
    section by section."""
    expected: dict[str, set[str]] = {c: set() for c in cases}

    def add(case_id: str, filename: str | None) -> None:
        if case_id in expected and filename:
            expected[case_id].add(filename)

    for r in _load_rows(wb, "Cert of Incorporation"):
        cid = str(r.get("Case ID"))
        add(cid, r.get("Filename") or f"certificate_of_incorporation_{cid}.pdf")

    for r in _load_rows(wb, "Cert of Incumbency"):
        cid = str(r.get("Case ID"))
        add(cid, r.get("Filename") or f"{cid}_CertIncumbency.pdf")

    for r in _load_rows(wb, "Company Constitution"):
        cid = str(r.get("Case ID"))
        add(cid, r.get("Filename") or f"{cid}_Constitution.pdf")

    for r in _load_rows(wb, "Credit Reports"):
        cid = str(r.get("Case ID"))
        add(cid, r.get("Filename") or f"{cid}_CreditReport.pdf")

    for r in _load_rows(wb, "List of Loans"):
        cid = str(r.get("Case ID"))
        # A blank/"None" Loan Type is a genuine "this case has no existing loans"
        # row -- generate_from_mastersheet.py skips it, so no file is expected.
        loan_type = str(r.get("Loan Type") or "").strip()
        if not loan_type or loan_type.lower() == "none":
            continue
        add(cid, r.get("Filename") or f"{cid}_ListLoans.pdf")

    for r in _load_rows(wb, "Bank Statements"):
        cid = str(r.get("Case ID"))
        add(cid, r.get("Filename") or f"{cid}_BankStatement.pdf")

    # One combined self-cert PDF per case, regardless of how many individual rows
    # the tab carries for that case -- the GLDB form is one company-level document.
    fatca_cases = {str(r.get("Case ID (auto)")) for r in _load_rows(wb, "FATCA Self-Declaration")}
    for cid in fatca_cases:
        add(cid, f"{cid}_selfcert.pdf")

    for r in _load_rows(wb, "Proof of Address"):
        cid = str(r.get("Case ID (auto)"))
        template = str(r.get("Template") or "").lower()
        individual = str(r.get("Individual ID") or r.get("Filename") or f"{cid}-proof")
        if "utility" in template:
            add(cid, r.get("Filename") or f"utility_bill_{cid}.pdf")
        elif "bank statement" in template:
            add(cid, r.get("Filename") or f"{individual}_ProofAddress.pdf")
        # "NRIC" template rows are an intentional no-op: that individual's address
        # is already evidenced by their NRIC/Passport document (section below).

    # NRIC-Passport ignores its own Filename column -- generate_from_mastersheet.py
    # names these off the individual ID and template instead.
    for r in _load_rows(wb, "NRIC-Passport"):
        cid = str(r.get("Case ID (auto)"))
        template = str(r.get("Template") or "").lower()
        individual = str(r.get("Individual ID") or r.get("Filename") or f"{cid}-id")
        if "nric" in template:
            add(cid, f"{individual}_NRIC.pdf")
        if "passport" in template:
            add(cid, f"{individual}_Passport.pdf")

    # One combined financial-statements PDF per case, written out under every
    # filename the tab's rows for that case name (dedup via a set) -- pick the
    # alphabetically-first one as the file to check for, same as the generator does.
    sow_filenames: dict[str, set[str | None]] = {}
    for r in _load_rows(wb, "Source of Wealth Docs"):
        cid = str(r.get("Case ID"))
        sow_filenames.setdefault(cid, set()).add(r.get("Filename"))
    for cid, fnames in sow_filenames.items():
        fnames = {f for f in fnames if f} or {f"{cid}_SourceWealth_Financials.pdf"}
        add(cid, sorted(fnames)[0])

    for r in _load_rows(wb, "Payslips"):
        cid = str(r.get("Case ID (auto)"))
        individual = str(r.get("Individual ID") or r.get("Filename") or f"{cid}-payslip")
        add(cid, f"{individual}_Payslip.pdf")

    return expected


def main() -> None:
    wb = openpyxl.load_workbook(str(MASTERSHEET), data_only=True)
    cases = sys.argv[1:] or _case_ids(wb)

    expected = expected_filenames(wb, cases)
    disk = {c: (set(os.listdir(OUT_DIR / c)) if (OUT_DIR / c).exists() else set()) for c in cases}

    missing = [(c, f) for c in cases for f in sorted(expected[c] - disk[c])]
    extra = [(c, f) for c in cases for f in sorted(disk[c] - expected[c])]

    print(f"Checked {len(cases)} case(s) across {len(DOC_SHEETS)} document sheets.\n")

    print("=== MISSING (mastersheet row has no matching file on disk) ===")
    if missing:
        for cid, fname in missing:
            print(f"  {cid}: {fname}")
    else:
        print("  none")

    print("\n=== EXTRA (file on disk not traced to any mastersheet row) ===")
    if extra:
        for cid, fname in extra:
            print(f"  {cid}: {fname}")
    else:
        print("  none")

    print("\n=== PER-CASE COUNTS (expected vs. on disk) ===")
    for c in cases:
        flag = "" if len(expected[c]) == len(disk[c]) and not missing and not extra else "  <-- check"
        print(f"  {c}: expected {len(expected[c])}, on disk {len(disk[c])}{flag}")

    if missing or extra:
        sys.exit(1)


if __name__ == "__main__":
    main()
