"""
Regenerate only a fixed subset of document types (passport, company
constitution, certificate of incorporation, certificate of incumbency,
source-of-wealth balance sheet/income statement, and NOA) for every case in
the mastersheet, without touching the other document types.

generate_from_mastersheet.py has no such selective mode -- generate_for_case()
always does every document type for one case at a time -- so this script
reuses its helpers/imports and replicates just the passport and Certificate
of Incorporation blocks (the two document types here with no standalone
mastersheet-driven generate(xlsx, outdir, cases=...) function of their own),
calling the other four types' existing generate() functions per case so
output still lands in documents_generated/<case_id>/ like every other
generator.

Usage (from backend/):
    python -m scripts.regenerate_selected
    python -m scripts.regenerate_selected C001 C002   # just these cases
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from scripts.generate_from_mastersheet import (
    MASTERSHEET,
    _ensure_out,
    _openpyxl_all_rows,
    _openpyxl_case_rows,
    _read_sheet,
    build_passport_persona,
    generate_passport_page,
    generate_certificate_of_incorporation,
    generate_cert_incumbency,
    generate_constitution,
    generate_optional_noa,
    generate_sow,
)


def _all_case_ids() -> list[str]:
    return [
        str(r["Case ID"]) for r in _openpyxl_all_rows("Case Registry") if r.get("Company Name")
    ]


def regenerate_case(case_id: str) -> None:
    out = _ensure_out(case_id)

    # Source of wealth docs — combined balance sheet / income statement PDF.
    sow_df = _read_sheet("Source of Wealth Docs")
    if not sow_df.empty and (sow_df["Case ID"].astype(str) == case_id).any():
        try:
            for p in generate_sow(MASTERSHEET, out, cases=[case_id]):
                print("Wrote", p)
        except Exception as e:
            print("Source-of-wealth generation failed:", e)

    # Passport only, out of the NRIC-Passport tab.
    id_df = _read_sheet("NRIC-Passport")
    if not id_df.empty:
        for r in _openpyxl_case_rows("NRIC-Passport", case_id, key="Case ID (auto)"):
            template = str(r.get("Template") or "").lower()
            if "passport" not in template:
                continue
            try:
                individual = str(r.get("Individual ID") or r.get("Filename") or f"{case_id}-id")
                pp_path = out / str(r.get("Filename") or f"{individual}_Passport.pdf")
                persona = build_passport_persona(r)
                generate_passport_page(str(pp_path), redact_photo=True, **persona)
                print("Wrote", pp_path)
            except Exception as e:
                print("Passport generation failed:", e)

    # Certificate of Incorporation.
    coi_df = _read_sheet("Cert of Incorporation")
    if not coi_df.empty:
        for _, r in coi_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            try:
                coi_path = out / str(r.get("Filename") or f"certificate_of_incorporation_{case_id}.pdf")
                generate_certificate_of_incorporation(
                    str(coi_path),
                    company_name=r.get("Company Name (auto)") or "",
                    company_number=str(r.get("UEN (auto)") or ""),
                    incorporation_date=r.get("Incorporation Date (auto)") or datetime.now().date(),
                    company_type=r.get("Company Type (auto)") or None,
                )
                print("Wrote", coi_path)
            except Exception as e:
                print("COI generation failed:", e)

    # Certificate of Incumbency.
    try:
        for p in generate_cert_incumbency(MASTERSHEET, out, cases=[case_id]):
            print("Wrote", p)
    except Exception as e:
        print("Cert of Incumbency generation failed:", e)

    # Company Constitution.
    try:
        for p in generate_constitution(MASTERSHEET, out, cases=[case_id]):
            print("Wrote", p)
    except Exception as e:
        print("Company Constitution generation failed:", e)

    # Notice of Assessment.
    try:
        for p in generate_optional_noa(MASTERSHEET, out, cases=[case_id]):
            print("Wrote", p)
    except Exception as e:
        print("NOA generation failed:", e)

    print("Regeneration completed for", case_id)


def main() -> None:
    cases = sys.argv[1:] or _all_case_ids()
    for case_id in cases:
        regenerate_case(case_id)


if __name__ == "__main__":
    main()
