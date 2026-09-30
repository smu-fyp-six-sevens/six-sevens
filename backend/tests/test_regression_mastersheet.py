"""
Regression tests (SCRUM-206) for the mock-document mastersheet — "Test Cases -
Sprint 1.xlsx" and "Test Cases - Sprint 2.xlsx", Test Document and
Development Test sheets:

  SCRUM-12  Generate non-fraudulent documents      DT-004
  SCRUM-13  Masterlist records                     TD-004 – TD-006, DT-007 – DT-009
  SCRUM-15  Unrecognised documents                 TD-001, TD-002, DT-010, DT-011
  SCRUM-120 Update mastersheet                     DT-023 – DT-026
  SCRUM-115 Generate additional file formats       DT-028

Checks the committed mastersheet (doc_gen_scripts/v16_document_mastersheet.xlsx)
itself. The document generator (scripts/generate_from_mastersheet.py) was
retired in refactor 61d730b, so the test cases that generated documents
(DT-001 – DT-003, DT-005, DT-020 – DT-022, DT-027, and the "every generated
document is logged" half of TD-003 / DT-006) are retired with it.

Fraud injection (SCRUM-14 DT-012/013, SCRUM-121 DT-014 – DT-019) is covered by
the existing tests/test_fraud_injections.py, test_injectors.py and
test_injection_router.py.
"""
from __future__ import annotations

import re
import warnings
from collections import defaultdict
from pathlib import Path

import openpyxl
import pytest

from app.models import DocumentType

pytestmark = pytest.mark.filterwarnings("ignore:Data Validation extension")

ORIGINAL_CASES = [f"C{n:03d}" for n in range(1, 37)]  # the 36 SCRUM-12 sets
MASTERSHEET = Path(__file__).resolve().parents[1] / "doc_gen_scripts" / "v16_document_mastersheet.xlsx"

NEW_CASES_FROM = 37  # SCRUM-120 added C037 onwards

DOCUMENT_SHEETS = [
    "Cert of Incorporation", "Cert of Incumbency", "Company Constitution", "Credit Reports",
    "List of Loans", "Bank Statements", "FATCA Self-Declaration", "Proof of Address",
    "NRIC-Passport", "Source of Wealth Docs", "Payslips",
]

UNRECOGNISED_SHEETS = ["Budget Proposal (Unrecognised Documents)", "Purchase Order (Unrecognised)"]

ALL_DOCUMENT_SHEETS = DOCUMENT_SHEETS + ["Notice of Assessment", "Optional Documents", *UNRECOGNISED_SHEETS]

# Known defect found by this regression run (reported, not fixed here). The
# main tests skip exactly this row so they still catch anything new; its own
# strict-xfail test below starts failing once it's fixed — the signal to
# delete the entry.
REG_005_UNTAGGED_FRAUD_ROWS = {("Cert of Incorporation", "BizFile_994116.pdf")}  # C042


@pytest.fixture(scope="session")
def workbook():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(MASTERSHEET, read_only=True, data_only=True)


def _rows(workbook, sheet: str) -> list[dict]:
    rows = workbook[sheet].iter_rows(values_only=True)
    header = next(rows)
    return [dict(zip(header, r)) for r in rows if any(c not in (None, "") for c in r)]


def _sheet_name(workbook, prefix: str) -> str:
    return next(name for name in workbook.sheetnames if name.startswith(prefix))


def _case_of(row: dict) -> str | None:
    return row.get("Case ID") or row.get("Case ID (auto)")


@pytest.fixture(scope="session")
def registry(workbook) -> dict[str, dict]:
    return {r["Case ID"]: r for r in _rows(workbook, "Case Registry")}


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-12", "DT-004")
def test_at_least_36_complete_sets_across_all_11_document_types(workbook, registry):
    cases_by_sheet = {sheet: {_case_of(r) for r in _rows(workbook, sheet)} for sheet in DOCUMENT_SHEETS}
    complete = [c for c in registry if all(c in cases for cases in cases_by_sheet.values())]
    assert len(complete) >= 36
    assert set(ORIGINAL_CASES) <= set(complete)


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-004 / DT-007")
def test_every_case_record_has_the_required_company_and_applicant_details(registry):
    required = [
        "Company Name", "Company UEN / Reg No.", "Registered Address",
        "Applicant Name (Director & UBO)", "Applicant NRIC No.",
    ]
    incomplete = {
        case_id: [field for field in required if not str(row.get(field) or "").strip()]
        for case_id, row in registry.items()
    }
    assert {c: f for c, f in incomplete.items() if f} == {}


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-005 / DT-008")
def test_every_document_row_is_linked_to_an_existing_case(workbook, registry):
    individuals = {r["Individual ID"]: r["Case ID"] for r in _rows(workbook, "Individuals")}
    orphans = []
    for prefix in ALL_DOCUMENT_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        for row in _rows(workbook, sheet):
            case_id = _case_of(row)
            if case_id not in registry:
                orphans.append((sheet, row.get("Filename"), case_id))
            individual = row.get("Individual ID")
            if individual and individuals.get(individual) != case_id:
                orphans.append((sheet, row.get("Filename"), f"{individual} not in {case_id}"))
    assert orphans == []


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-006 / DT-009")
def test_every_document_row_records_its_fraud_details_or_none(workbook):
    dimensions = {r[0] for r in workbook["Fraud Dimensions"].iter_rows(values_only=True) if r and r[0]}
    problems = []
    for prefix in ALL_DOCUMENT_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        for row in _rows(workbook, sheet):
            if (sheet, row.get("Filename")) in REG_005_UNTAGGED_FRAUD_ROWS:
                continue
            injected = str(row.get("Fraud Injected? (Y/N)") or "").strip().upper()
            dimension = row.get("Detection Dimension")
            if injected == "Y" and (dimension not in dimensions or not row.get("Fraud Description")):
                problems.append((sheet, row.get("Filename"), "fraud injected but type/description missing"))
            elif injected == "N" and dimension:
                problems.append((sheet, row.get("Filename"), "no fraud but a dimension recorded"))
            elif injected not in ("Y", "N"):
                problems.append((sheet, row.get("Filename"), f"Fraud Injected? is {injected!r}"))
    assert problems == []


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-13", "TD-006 / DT-009")
@pytest.mark.xfail(
    strict=True,
    reason="REG-005 (known defect): mastersheet row BizFile_994116.pdf (C042) is marked "
    "Fraud Injected = Y with no Detection Dimension or Fraud Description.",
)
def test_known_untagged_fraud_rows_have_been_tagged(workbook):
    for sheet, filename in REG_005_UNTAGGED_FRAUD_ROWS:
        row = next(r for r in _rows(workbook, sheet) if r.get("Filename") == filename)
        assert row.get("Detection Dimension") and row.get("Fraud Description")


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-15", "TD-001 / DT-010")
def test_unrecognised_templates_match_no_defined_document_type(workbook):
    defined = {t.value.replace("_", " ") for t in DocumentType} - {"unrecognised"}
    for prefix in UNRECOGNISED_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        subject = sheet.split("(")[0].strip().lower()  # "budget proposal", "purchase order"
        assert subject not in defined
        assert not any(subject in d or d in subject for d in defined)


@pytest.mark.sprint1
@pytest.mark.story("SCRUM-15", "TD-002 / DT-011")
def test_unrecognised_documents_are_listed_under_the_unrecognised_sheets(workbook):
    for prefix in UNRECOGNISED_SHEETS:
        sheet = _sheet_name(workbook, prefix[:25])
        assert "Unrecognised" in sheet
        rows = _rows(workbook, sheet)
        assert len(rows) >= 36
        assert all(row.get("Filename") for row in rows)


def _new_cases(registry) -> list[str]:
    return [c for c in registry if int(c[1:]) >= NEW_CASES_FROM]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-023")
def test_at_least_15_new_cases_were_added(registry):
    assert len(_new_cases(registry)) >= 15


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-024")
def test_new_cases_include_the_new_optional_document_types(workbook, registry):
    new = set(_new_cases(registry))
    optional_types = {r["Document Type"] for r in _rows(workbook, "Optional Documents") if r["Case ID"] in new}
    assert any("Proof of Address (Company)" in t for t in optional_types)
    assert any("Source of Wealth (Individual)" in t for t in optional_types)
    assert new <= {r["Case ID"] for r in _rows(workbook, "Notice of Assessment")}


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-025")
def test_new_cases_use_realistic_submission_filenames(workbook, registry):
    new = set(_new_cases(registry))
    names = [r["Filename"] for r in _rows(workbook, "NRIC-Passport") if r["Case ID (auto)"] in new]
    # Real submissions aren't named after an internal case ID ("C064_...").
    assert names
    assert not [n for n in names if re.match(r"^C\d{3}", str(n))]


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-120", "DT-026")
def test_fraud_cases_are_tagged_with_a_known_fraud_dimension(workbook, registry):
    dimensions = {r[0] for r in workbook["Fraud Dimensions"].iter_rows(values_only=True) if r and r[0]}
    tagged = defaultdict(set)
    known_defect_cases: set[str] = set()
    for prefix in ALL_DOCUMENT_SHEETS:
        for row in _rows(workbook, _sheet_name(workbook, prefix[:25])):
            if (prefix, row.get("Filename")) in REG_005_UNTAGGED_FRAUD_ROWS:
                known_defect_cases.add(_case_of(row))
                continue
            if str(row.get("Fraud Injected? (Y/N)") or "").upper() == "Y":
                tagged[_case_of(row)].add(row.get("Detection Dimension"))
    fraud_cases = [c for c, row in registry.items() if str(row.get("Fraud Case? (Y/N)")).upper() == "Y"]
    assert fraud_cases
    for case_id in fraud_cases:
        if not tagged[case_id] and case_id in known_defect_cases:
            continue  # REG-005 — its only fraud row is the untagged one
        assert tagged[case_id], f"{case_id} is a fraud case with no tagged document"
        assert tagged[case_id] <= dimensions, f"{case_id} has unknown dimension {tagged[case_id] - dimensions}"


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-115", "DT-028")
def test_generated_formats_follow_the_document_format_assumptions(workbook, registry):
    new = set(_new_cases(registry))
    ids = [r["Filename"] for r in _rows(workbook, "NRIC-Passport") if r["Case ID (auto)"] in new]
    assert ids and all(Path(n).suffix.lower() in {".jpg", ".jpeg", ".png"} for n in ids)

    bills = [r["Filename"] for r in _rows(workbook, "Proof of Address") if r["Case ID (auto)"] in new]
    assert all(Path(n).suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"} for n in bills)

    for sheet in ["Cert of Incorporation", "Cert of Incumbency", "Company Constitution", "Credit Reports",
                  "List of Loans", "Bank Statements", "FATCA Self-Declaration", "Source of Wealth Docs",
                  "Payslips", "Notice of Assessment"]:
        names = [r["Filename"] for r in _rows(workbook, sheet) if _case_of(r) in new]
        assert all(Path(n).suffix.lower() == ".pdf" for n in names), sheet

