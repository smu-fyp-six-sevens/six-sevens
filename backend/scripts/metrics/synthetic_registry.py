"""
Builds the stand-in ACRA register for the test companies from the mastersheet.

From backend/:
    python -m scripts.metrics.synthetic_registry                     # the v18 mastersheet
    python -m scripts.metrics.synthetic_registry path/to/sheet.xlsx

Writes app/fraud_detection/external_validation/registry_lookups/synthetic_acra_registry.json: one record per
Case Registry row (UEN, name, incorporation date, registered address), each a live company. The app reads it only
when SYNTHETIC_ACRA_REGISTRY=true is set in backend/.env (see synthetic_acra.py there).
"""

from __future__ import annotations

import json
import re
import sys
import warnings
from datetime import date, datetime
from pathlib import Path

import openpyxl

BACKEND = Path(__file__).resolve().parents[2]
OUT = BACKEND / "app" / "fraud_detection" / "external_validation" / "registry_lookups" / "synthetic_acra_registry.json"
DEFAULT_MASTERSHEET = BACKEND / "doc_gen_scripts" / "v18_document_mastersheet.xlsx"
_POSTAL = re.compile(r"(\d{6})\s*$")

warnings.filterwarnings("ignore", message="Data Validation extension is not supported")


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() or None if value else None


def build(mastersheet: Path) -> list[dict]:
    wb = openpyxl.load_workbook(str(mastersheet), read_only=True, data_only=True)
    rows = wb["Case Registry"].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    entities = []
    for row in rows:
        record = dict(zip(header, row))
        uen, name = record.get("Company UEN / Reg No."), record.get("Company Name")
        if not uen or not name:
            continue
        address = str(record.get("Registered Address") or "").strip()
        postal = _POSTAL.search(address)
        entities.append({
            "uen": str(uen).strip(),
            "name": str(name).strip().upper(),
            "status": "Live Company",
            "incorporation_date": _iso(record.get("Incorporation Date")),
            "address": address.upper(),
            # A postal code that isn't 6 digits (a typo in the sheet) is left out, so no address check runs on it.
            "postal_code": postal.group(1) if postal else None,
            "former_names": [],
            "case_id": str(record.get("Case ID") or ""),
        })
    wb.close()
    return entities


def main() -> None:
    mastersheet = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MASTERSHEET
    entities = build(mastersheet)
    OUT.write_text(json.dumps({
        "about": "Synthetic test companies only; read when SYNTHETIC_ACRA_REGISTRY=true. Built by "
                 "scripts/metrics/synthetic_registry.py from " + mastersheet.name + ".",
        "entities": entities,
    }, indent=2) + "\n")
    print(f"Wrote {len(entities)} test companies to {OUT.relative_to(BACKEND)}")


if __name__ == "__main__":
    main()
